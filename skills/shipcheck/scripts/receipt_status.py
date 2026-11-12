#!/usr/bin/env python3
"""Say whether a git-local Shipcheck receipt matches this head and reviewed scope.

Read-only. Standard library only. No network and no GitHub writes. Prints a
fingerprint or a status. Does not store file contents, diffs, secrets, or review bodies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def git_out(repo: Path, *args: str) -> str:
    proc = git(repo, *args)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def toplevel(start: Path) -> Path:
    proc = git(start, "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "not a git repository")
    return Path(proc.stdout.strip()).resolve()


def shipcheck_dir(start: Path) -> Path:
    top = toplevel(start)
    proc = git(top, "rev-parse", "--git-path", "shipcheck")
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "not a git repository")
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else top / path


def root_relative(top: Path, base: Path, spec: str) -> str:
    """Return a root-relative scope. Specs are relative to base, not the process cwd."""
    text = spec.replace("\\", "/").strip()
    if not text:
        return ""
    if Path(text).is_absolute():
        candidate = Path(text).resolve()
    else:
        while text.startswith("./"):
            text = text[2:]
        candidate = base.resolve() if text in {"", "."} else (base / text).resolve()
    try:
        rel = candidate.relative_to(top.resolve()).as_posix()
    except ValueError:
        return ""
    return rel or "."


def is_excluded(path: str, excluded: set[str]) -> bool:
    return any(path == item or path.startswith(item + "/") for item in excluded)


def nul_records(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def staged_oids(repo: Path, spec: str) -> dict[str, str]:
    proc = git(repo, "ls-files", "-s", "-z", "--full-name", "--", spec)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    found: dict[str, list[str]] = {}
    for item in nul_records(proc.stdout):
        meta, path = item.split("\t", 1)
        parts = meta.split()
        if len(parts) < 3:
            continue
        found.setdefault(path, []).append(f"{parts[2]}:{parts[1]}")
    return {path: ",".join(oids) for path, oids in found.items()}


def untracked_paths(repo: Path, spec: str) -> list[str]:
    proc = git(repo, "ls-files", "-o", "--exclude-standard", "-z", "--full-name", "--", spec)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git ls-files failed")
    return nul_records(proc.stdout)


def hash_objects(repo: Path, paths: list[str]) -> dict[str, str]:
    existing = [path for path in paths if (repo / path).is_file()]
    if not existing:
        return {}
    proc = git(repo, "hash-object", "--stdin-paths", input_text="\n".join(existing) + "\n")
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or "git hash-object failed")
    hashes = proc.stdout.splitlines()
    if len(hashes) != len(existing):
        raise RuntimeError("git hash-object returned an unexpected count")
    return dict(zip(existing, hashes))


def manifest(base: Path, reviewed: list[str], excluded: list[str]) -> list[str]:
    top = toplevel(base)
    skip = {item for item in (root_relative(top, base, path) for path in excluded) if item}
    tracked: dict[str, str] = {}
    extra: set[str] = set()
    for spec in reviewed:
        spec = root_relative(top, base, spec)
        if not spec:
            continue
        for path, oid in staged_oids(top, spec).items():
            if not is_excluded(path, skip):
                tracked[path] = oid
        for path in untracked_paths(top, spec):
            if path not in tracked and not is_excluded(path, skip):
                extra.add(path)
    hashes = hash_objects(top, [*tracked, *extra])
    lines = [f"tracked\t{path}\t{oid}\t{hashes.get(path, '-')}" for path, oid in sorted(tracked.items())]
    lines += [f"untracked\t{path}\t-\t{hashes.get(path, '-')}" for path in sorted(extra)]
    return lines


def fingerprint(repo: Path, reviewed: list[str], excluded: list[str]) -> str:
    return hashlib.sha256("\n".join(manifest(repo, reviewed, excluded)).encode()).hexdigest()


def load_receipts(repo: Path) -> list[dict]:
    path = shipcheck_dir(repo) / "receipts.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    receipts = data.get("receipts")
    if not isinstance(receipts, list):
        raise RuntimeError("receipts.json has no receipts list")
    return receipts


def row(text: str, label: str) -> str | None:
    prefix = f"- {label}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return None


def has_pr(text: str) -> bool:
    value = row(text, "PR") or ""
    return value.startswith("https://") or value.startswith("http://")


def status_label(text: str) -> str:
    return row(text, "Status") or "unknown"


def classify(repo: Path) -> str:
    current = git_out(repo, "rev-parse", "HEAD").strip()
    matched = [item for item in load_receipts(repo) if item.get("head") == current]
    if not matched:
        return "none for this scope\n"

    def saved_at(item: dict) -> str:
        return str(item.get("saved_at") or "")

    exact = []
    for item in matched:
        reviewed = item.get("reviewed_paths") or []
        excluded = item.get("excluded_paths") or []
        if fingerprint(repo, reviewed, excluded) == item.get("fingerprint"):
            exact.append(item)
    if exact:
        chosen = max(exact, key=saved_at)
        text = chosen.get("receipt") or ""
        if not text.endswith("\n"):
            text += "\n"
        if has_pr(text):
            return f"historical\nreviewed scope unchanged\nStatus: {status_label(text)} (historical)\n"
        return "current\n" + text
    chosen = max(matched, key=saved_at)
    return f"stale\nStatus: {status_label(chosen.get('receipt') or '')} (stale)\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print whether a git-local Shipcheck receipt matches this head and reviewed scope. Read-only."
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--fingerprint", action="store_true", help="print the reviewed-scope fingerprint and exit")
    parser.add_argument("--reviewed", action="append", default=[], help="file or directory included in the review")
    parser.add_argument("--exclude", action="append", default=[], help="dirty path excluded from the review")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.fingerprint:
            if not args.reviewed:
                parser.error("--fingerprint requires at least one --reviewed path")
            print(fingerprint(repo, args.reviewed, args.exclude))
            return 0
        sys.stdout.write(classify(repo))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
