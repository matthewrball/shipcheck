import contextlib
import importlib.util
import io
import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "shipcheck" / "scripts" / "run_advisor_lane.py"
spec = importlib.util.spec_from_file_location("run_advisor_lane", SCRIPT)
lane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lane)

HEALTHY = """
input="$(cat)"
if [[ "$input" == *"single word OK"* ]]; then echo OK; exit 0; fi
echo "review of: $input"
"""


def results(report):
    return [(item["provider"], item.get("stage"), item["result"]) for item in report["attempts"]]


class RunAdvisorLaneTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.prompt = self.tmp / "prompt.md"
        self.out = self.tmp / "answer.md"

    def fake(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\n" + textwrap.dedent(body))
        path.chmod(0o755)

    def run_lane(self, *args, prompt="Review the diff.\n", env=None):
        self.prompt.write_text(prompt)
        stdout = io.StringIO()
        environ = {"PATH": f"{self.bin}:/usr/bin:/bin", **(env or {})}
        with mock.patch.dict(os.environ, environ), contextlib.redirect_stdout(stdout):
            code = lane.main(["--prompt-file", str(self.prompt), "--out", str(self.out), *args])
        return code, json.loads(stdout.getvalue())

    def test_falls_back_past_hung_signed_out_and_exhausted_models(self):
        pid_file = self.tmp / "gemini.pid"
        self.fake("gemini", f"""
            echo $$ > {pid_file}
            trap '' TERM
            exec sleep 600
            """)
        self.fake("grok", """
            echo "Error: Not signed in. To authenticate without a browser, run: grok login --device-code" >&2
            exit 1
            """)
        self.fake("cursor-agent", """
            echo "ActionRequiredError: You've hit your usage limit" >&2
            exit 1
            """)
        self.fake("claude", HEALTHY)
        started = time.monotonic()
        code, report = self.run_lane(
            "--try", "gemini", "--try", "grok:grok-4.7", "--try", "cursor", "--try", "claude", "--canary-timeout", "1"
        )
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(code, 0)
        self.assertEqual((report["status"], report["provider"]), ("ok", "claude"))
        self.assertEqual(
            results(report),
            [
                ("gemini", "canary", "timed_out"),
                ("grok", "canary", "signed_out"),
                ("cursor", "canary", "exhausted"),
                ("claude", "canary", "ok"),
                ("claude", "run", "ok"),
            ],
        )
        self.assertIn("review of: Review the diff.", self.out.read_text())
        with self.assertRaises(ProcessLookupError):  # the SIGTERM-ignoring hang was SIGKILLed and reaped
            os.kill(int(pid_file.read_text()), 0)

    def test_limit_words_in_a_real_review_do_not_discard_it(self):
        self.fake("codex", """
            input="$(cat)"
            if [[ "$input" == *"single word OK"* ]]; then echo OK; exit 0; fi
            echo "app.py:12 retries HTTP 429 without honoring the rate limit window; quota exceeded errors are swallowed."
            """)
        self.fake("claude", HEALTHY)
        code, report = self.run_lane("--try", "codex", "--try", "claude")
        self.assertEqual((code, report["provider"]), (0, "codex"))
        self.assertIn("rate limit window", self.out.read_text())

    def test_credits_message_on_a_clean_exit_marks_the_family_out(self):
        self.fake("gemini", """
            cat >/dev/null
            echo "Print mode: run ended with error and no response: Your AI credits balance is too low to continue."
            """)
        self.fake("agy", 'echo "agy answered"\n')
        self.fake("claude", HEALTHY)
        code, report = self.run_lane("--try", "gemini", "--try", "antigravity", "--try", "claude")
        self.assertEqual(code, 0)
        self.assertEqual(
            results(report),
            [
                ("gemini", "canary", "exhausted"),
                ("antigravity", None, "skipped"),
                ("claude", "canary", "ok"),
                ("claude", "run", "ok"),
            ],
        )

    def test_budget_bounds_a_lane_that_never_answers(self):
        self.fake("gemini", "exec sleep 600\n")
        started = time.monotonic()
        code, report = self.run_lane("--try", "gemini", "--canary-timeout", "0", "--timeout", "100", "--budget", "1")
        self.assertLess(time.monotonic() - started, 10)
        self.assertEqual((code, report["status"]), (1, "unavailable"))
        self.assertEqual(results(report), [("gemini", "run", "timed_out")])
        self.assertFalse(self.out.exists())

    def test_lanes_never_see_metered_api_keys(self):
        self.fake("claude", "cat >/dev/null\nenv\n")
        secrets = {key: "secret" for key in ("XAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDECODE")}
        code, _ = self.run_lane("--try", "claude", "--canary-timeout", "0", env={**secrets, "SHIPCHECK_KEEP": "kept"})
        answer = self.out.read_text()
        self.assertEqual(code, 0)
        self.assertRegex(answer, r"(?m)^SHIPCHECK_KEEP=kept$")
        for key in secrets:
            self.assertNotRegex(answer, rf"(?m)^{key}=")

    def test_prompt_too_large_for_argv_falls_through_to_a_stdin_cli(self):
        self.fake("grok", 'echo "grok answered"\n')
        self.fake("claude", 'cat >/dev/null\necho "claude answered"\n')
        code, report = self.run_lane("--try", "grok", "--try", "claude", "--canary-timeout", "0", prompt="x" * 3_000_000)
        self.assertEqual((code, report["provider"]), (0, "claude"))
        self.assertEqual(results(report)[0], ("grok", "run", "failed"))

    def test_excluded_and_missing_providers_are_skipped_without_running(self):
        self.fake("claude", 'echo "claude answered"\n')
        code, report = self.run_lane("--try", "claude", "--try", "cursor", "--exclude", "claude")
        self.assertEqual((code, report["status"]), (1, "unavailable"))
        self.assertEqual(results(report), [("claude", None, "excluded"), ("cursor", None, "missing")])


if __name__ == "__main__":
    unittest.main()
