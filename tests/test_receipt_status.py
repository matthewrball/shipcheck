import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "shipcheck" / "scripts" / "receipt_status.py"
SKILL = ROOT / "skills" / "shipcheck" / "SKILL.md"
TEMPLATE = ROOT / "skills" / "shipcheck" / "references" / "receipt-template.md"

RECEIPT_BLOCK = """# Shipcheck receipt
- Status: Ready to land | Needs a decision | Review wait timed out
- Branch / head: <branch> <sha>
- PR: <url or none>
- Intent: <one line>
- Must not change: <one line or none>
- Fix mode: review only | fix safe issues | fix anything in scope
- Files changed by Shipcheck: <paths or none>
- Checks: `<command>` → <exact result>
- Reviewer: independent delegated review | single delegated review (routine) | tri-model advisor review | self-review fallback (host has no native delegation) | self-review fallback (delegated reviewer unavailable)
- Advisor: <models, substitutions, missing lanes, interrogation | skipped (routine) | not run>
- Account: confirmed signed-in host plan | unverified (no host billing surface)
- Watch: <start> → <end>; --once snapshot | quiet settled | pending | feedback_ready | checks_failed | checks_need_attention | timed out | not run
- Feedback: <acked / blocking / informational / none>
- Pending or attention checks: <none or names>
- Next: <one line>"""


def extract_receipt(text: str) -> str:
    marker = "```markdown\n# Shipcheck receipt\n"
    if text.count(marker) != 1:
        raise AssertionError(f"expected one receipt fence, found {text.count(marker)}")
    start = text.index(marker) + len("```markdown\n")
    end = text.index("\n```", start)
    return text[start:end]


def run(repo, *args):
    return subprocess.run(
        ["python3", "-B", str(SCRIPT), "--repo", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def shipcheck_dir(repo: Path) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--git-path", "shipcheck"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise AssertionError(proc.stderr)
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else repo / path


def init_repo():
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a\n")
    (repo / "src" / "skip.py").write_text("skip\n")
    (repo / "notes.md").write_text("note\n")
    (repo / ".gitignore").write_text("*.pyc\n")
    git(repo, "add", "src/a.py", "src/skip.py", "notes.md", ".gitignore")
    git(repo, "commit", "-qm", "init")
    return tmp, repo


def filled(pr="none", status="Ready to land"):
    return (
        "# Shipcheck receipt\n"
        f"- Status: {status}\n"
        "- Branch / head: main deadbeef\n"
        f"- PR: {pr}\n"
        "- Intent: test the receipt\n"
        "- Must not change: none\n"
        "- Fix mode: review only\n"
        "- Files changed by Shipcheck: none\n"
        "- Checks: `true` → pass\n"
        "- Reviewer: single delegated review (routine)\n"
        "- Advisor: skipped (routine)\n"
        "- Account: unverified (no host billing surface)\n"
        "- Watch: not run\n"
        "- Feedback: none\n"
        "- Pending or attention checks: none\n"
        "- Next: none\n"
    )


def fingerprint(repo, reviewed=("src",), excluded=("notes.md", "src/skip.py")):
    args = ["--fingerprint"]
    for path in reviewed:
        args += ["--reviewed", path]
    for path in excluded:
        args += ["--exclude", path]
    proc = run(repo, *args)
    if proc.returncode:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def save(repo, *, pr="none", status="Ready to land", reviewed=("src",), excluded=("notes.md", "src/skip.py")):
    text = filled(pr, status)
    payload = {
        "receipts": [
            {
                "head": git(repo, "rev-parse", "HEAD"),
                "fingerprint": fingerprint(repo, reviewed, excluded),
                "reviewed_paths": list(reviewed),
                "excluded_paths": list(excluded),
                "saved_at": "2026-11-12T00:00:00Z",
                "receipt": text,
            }
        ]
    }
    path = shipcheck_dir(repo)
    path.mkdir(parents=True, exist_ok=True)
    (path / "receipts.json").write_text(json.dumps(payload, indent=2) + "\n")
    return text


def status(repo):
    proc = run(repo)
    if proc.returncode:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout


class ReceiptFormatTests(unittest.TestCase):
    def test_skill_and_template_share_one_receipt_block(self):
        self.assertEqual(extract_receipt(SKILL.read_text()), RECEIPT_BLOCK)
        self.assertEqual(extract_receipt(TEMPLATE.read_text()), RECEIPT_BLOCK)
        self.assertNotIn("**Status:**", TEMPLATE.read_text())
        self.assertNotIn("Copy into the PR body", TEMPLATE.read_text())


class ReceiptStatusTests(unittest.TestCase):
    def test_none_when_no_receipt_matches_head(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(status(repo), "none for this scope\n")
        save(repo)
        git(repo, "commit", "-qm", "empty", "--allow-empty")
        self.assertEqual(status(repo), "none for this scope\n")

    def test_no_pr_fingerprint_match_is_current(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        text = save(repo)
        before = (shipcheck_dir(repo) / "receipts.json").read_text()
        out = status(repo)
        self.assertEqual(out, "current\n" + text)
        self.assertIn("- Status: Ready to land\n", out)
        self.assertNotIn("stale", out)
        self.assertNotIn("historical", out)
        self.assertEqual((shipcheck_dir(repo) / "receipts.json").read_text(), before)
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_staged_edit_is_stale_not_current_ready(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        original = (repo / "src" / "a.py").read_text()
        (repo / "src" / "a.py").write_text("staged\n")
        git(repo, "add", "src/a.py")
        (repo / "src" / "a.py").write_text(original)
        out = status(repo)
        self.assertEqual(out, "stale\nStatus: Ready to land (stale)\n")

    def test_unstaged_edit_is_stale_not_current_ready(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        (repo / "src" / "a.py").write_text("unstaged\n")
        out = status(repo)
        self.assertEqual(out, "stale\nStatus: Ready to land (stale)\n")

    def test_new_in_scope_untracked_file_is_stale_not_current_ready(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        (repo / "src" / "new.py").write_text("new\n")
        self.assertEqual(status(repo), "stale\nStatus: Ready to land (stale)\n")

    def test_changed_and_deleted_in_scope_untracked_file_is_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        (repo / "src" / "extra.py").write_text("extra\n")
        save(repo)
        (repo / "src" / "extra.py").write_text("changed\n")
        self.assertEqual(status(repo), "stale\nStatus: Ready to land (stale)\n")
        save(repo)
        (repo / "src" / "extra.py").unlink()
        self.assertEqual(status(repo), "stale\nStatus: Ready to land (stale)\n")

    def test_excluded_unrelated_dirty_path_does_not_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        stored = json.loads((shipcheck_dir(repo) / "receipts.json").read_text())
        self.assertEqual(stored["receipts"][0]["excluded_paths"], ["notes.md", "src/skip.py"])
        (repo / "notes.md").write_text("dirty note\n")
        out = status(repo)
        self.assertTrue(out.startswith("current\n"))
        self.assertIn("- Status: Ready to land\n", out)
        self.assertNotIn("stale", out)

    def test_excluded_path_inside_reviewed_directory_does_not_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        (repo / "src" / "skip.py").write_text("dirty skip\n")
        out = status(repo)
        self.assertTrue(out.startswith("current\n"))
        self.assertNotIn("stale", out)

    def test_excluded_dotfile_does_not_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        (repo / ".env").write_text("a=1\n")
        save(repo, reviewed=(".",), excluded=(".env",))
        (repo / ".env").write_text("a=2\n")
        out = status(repo)
        self.assertTrue(out.startswith("current\n"))
        self.assertNotIn("stale", out)
        (repo / "src" / "a.py").write_text("touched\n")
        self.assertEqual(status(repo), "stale\nStatus: Ready to land (stale)\n")

    def test_absolute_exclude_does_not_drop_env_filename(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        (repo / ".env").write_text("a=1\n")
        (repo / "env").write_text("name\n")
        save(repo, reviewed=(".",), excluded=(str(repo / ".env"),))
        (repo / ".env").write_text("a=2\n")
        self.assertTrue(status(repo).startswith("current\n"))
        (repo / "env").write_text("name2\n")
        self.assertEqual(status(repo), "stale\nStatus: Ready to land (stale)\n")

    def test_subdirectory_repo_unstaged_edit_is_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        sub = repo / "src"
        save(sub, reviewed=(".",), excluded=())
        self.assertTrue(status(sub).startswith("current\n"))
        (sub / "a.py").write_text("unstaged from subdir\n")
        out = status(sub)
        self.assertEqual(out, "stale\nStatus: Ready to land (stale)\n")
        self.assertNotRegex(out, r"(?m)^current$")
        self.assertNotRegex(out, r"(?m)^- Status: Ready to land$")

    def test_gitignored_file_in_reviewed_directory_does_not_stale(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo)
        (repo / "src" / "a.pyc").write_bytes(b"pyc")
        self.assertTrue(status(repo).startswith("current\n"))

    def test_pr_receipt_is_historical_when_scope_matches(self):
        tmp, repo = init_repo()
        self.addCleanup(tmp.cleanup)
        save(repo, pr="https://github.com/acme/app/pull/7")
        out = status(repo)
        self.assertEqual(out, "historical\nreviewed scope unchanged\nStatus: Ready to land (historical)\n")
        self.assertNotIn("current", out)
        self.assertNotRegex(out, r"(?m)^- Status: Ready to land$")


if __name__ == "__main__":
    unittest.main()
