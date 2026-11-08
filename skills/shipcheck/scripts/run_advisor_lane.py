#!/usr/bin/env python3
"""Run one Shipcheck advisor lane on the first signed-in model that answers.

Every candidate gets a one-line canary before the real prompt. A candidate that is
missing, out of usage, signed out, failing, or past its deadline is killed and the
next one is tried, so a lane can never hold up shipping for longer than --budget.
Metered API-key variables are removed from the child environment: lanes run on the
user's signed-in plans only. Uses only the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path


CANARY_PROMPT = "Reply with the single word OK."
FAMILY = {
    "codex": "openai",
    "claude": "anthropic",
    "gemini": "google",
    "antigravity": "google",
    "grok": "xai",
    "cursor": "cursor",
}
MODEL_FLAG = {"codex": "--model", "claude": "--model", "grok": "--model", "cursor": "--model"}
# Same session markers `omc ask` strips, plus API keys that would switch a CLI to metered billing.
STRIPPED_ENV = {
    "CLAUDECODE",
    "CLAUDE_SESSION_ID",
    "CLAUDECODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
}
CODEX_STRIPPED_ENV = {"RUST_LOG", "RUST_BACKTRACE", "RUST_LIB_BACKTRACE"}
EXHAUSTED = re.compile(
    r"RESOURCE_EXHAUSTED|credits? balance is too low|out of (credits|quota)|insufficient.?(credits|quota)"
    r"|usage limit|hit your limit|limit reached|quota (exceeded|exhausted)|exceeded your (current )?quota"
    r"|rate.?limit|too many requests|\b429\b",
    re.IGNORECASE,
)
SIGNED_OUT = re.compile(
    r"not signed in|not logged in|not authenticated|log ?in required|please (re-?)?log ?in"
    r"|unauthori[sz]ed|\b401\b|authentication (failed|required)",
    re.IGNORECASE,
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def candidate(spec: str) -> tuple[str, str | None]:
    provider, _, model = spec.partition(":")
    if provider not in FAMILY:
        raise argparse.ArgumentTypeError(f"unknown provider {provider!r}; choose from {', '.join(FAMILY)}")
    if model and provider not in MODEL_FLAG:
        raise argparse.ArgumentTypeError(f"{provider} does not take a model pin")
    return provider, model or None


def command(provider: str, prompt: str, model: str | None) -> tuple[list[str], bool]:
    """Return argv and whether the prompt goes on stdin. Flags mirror `omc ask`."""
    pin = [MODEL_FLAG[provider], model] if model else []
    if provider == "codex":
        return ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", *pin, "-"], True
    if provider == "claude":
        return ["claude", "-p", *pin], True
    if provider == "gemini":
        return ["gemini", "--yolo"], True
    if provider == "antigravity":
        return ["agy", "--dangerously-skip-permissions", "-p", prompt], False
    if provider == "grok":
        return ["grok", *pin, "-p", prompt, "--always-approve"], False
    return ["cursor-agent", "--print", "--force", "--trust", "--sandbox", "disabled", *pin, prompt], False


def child_env(provider: str) -> dict[str, str]:
    stripped = STRIPPED_ENV | (CODEX_STRIPPED_ENV if provider == "codex" else set())
    return {key: value for key, value in os.environ.items() if key not in stripped}


def stop(proc: subprocess.Popen) -> None:
    """Kill the whole process group: provider CLIs wrap other CLIs and some trap SIGTERM."""
    for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass


def tail(path: Path, limit: int = 8192) -> str:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        return ANSI.sub("", handle.read().decode("utf-8", "replace"))


def classify(stage: str, code: int | None, answer: str, errors: str) -> str:
    if code is None:
        return "timed_out"
    answered = code == 0 and bool(answer.strip())
    if answered and stage == "run":
        return "ok"
    # A canary answer is one word, so limit or sign-in text in it is real, not review content.
    text = answer if answered else f"{answer}\n{errors}"
    if EXHAUSTED.search(text):
        return "exhausted"
    if SIGNED_OUT.search(text):
        return "signed_out"
    return "ok" if answered else "failed"


def attempt(
    provider: str, model: str | None, stage: str, prompt_file: Path, limit: float, cwd: str | None, workdir: Path
) -> tuple[dict, Path]:
    argv, use_stdin = command(provider, prompt_file.read_text(encoding="utf-8"), model)
    out_path = workdir / f"{provider}-{stage}.out"
    err_path = workdir / f"{provider}-{stage}.err"
    started = time.monotonic()
    code: int | None = None
    with out_path.open("wb") as out, err_path.open("wb") as err, prompt_file.open("rb") as prompt:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=prompt if use_stdin else subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=child_env(provider),
                start_new_session=True,
            )
        except OSError as exc:
            record = {"provider": provider, "model": model, "stage": stage, "result": "failed", "seconds": 0.0}
            return {**record, "detail": str(exc)}, out_path
        try:
            code = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            stop(proc)
    answer, errors = tail(out_path), tail(err_path)
    result = classify(stage, code, answer, errors)
    record = {
        "provider": provider,
        "model": model,
        "stage": stage,
        "result": result,
        "seconds": round(time.monotonic() - started, 1),
    }
    if result == "timed_out":
        record["detail"] = f"no answer within {limit:.0f}s; process group killed"
    elif result != "ok":
        text = f"{answer}\n{errors}"
        marker = {"exhausted": EXHAUSTED, "signed_out": SIGNED_OUT}.get(result)
        line = next((line for line in text.splitlines() if marker and marker.search(line)), "")
        record["detail"] = (line.strip() or " ".join(text.split())[-300:])[:300]
    return record, out_path


def run_lane(args: argparse.Namespace) -> dict:
    started = time.monotonic()
    deadline = started + args.budget
    excluded = {FAMILY[name] for name in args.exclude}
    exhausted: set[str] = set()
    attempts: list[dict] = []
    winner: dict | None = None
    with tempfile.TemporaryDirectory(prefix="shipcheck-lane-") as tmp:
        workdir = Path(tmp)
        canary_file = workdir / "canary.txt"
        canary_file.write_text(CANARY_PROMPT + "\n", encoding="utf-8")
        for provider, model in args.try_:
            base = {"provider": provider, "model": model}
            if FAMILY[provider] in excluded:
                attempts.append({**base, "result": "excluded"})
                continue
            if FAMILY[provider] in exhausted:
                attempts.append({**base, "result": "skipped", "detail": "same family already out of usage"})
                continue
            if shutil.which(command(provider, "", model)[0][0]) is None:
                attempts.append({**base, "result": "missing"})
                continue
            stages = [("canary", canary_file, args.canary_timeout)] if args.canary_timeout > 0 else []
            stages.append(("run", args.prompt_file, args.timeout))
            for stage, prompt_file, stage_timeout in stages:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    attempts.append({**base, "stage": stage, "result": "skipped", "detail": "lane budget spent"})
                    break
                record, answer_path = attempt(
                    provider, model, stage, prompt_file, min(stage_timeout, remaining), args.cwd, workdir
                )
                attempts.append(record)
                if record["result"] == "exhausted":
                    exhausted.add(FAMILY[provider])
                if record["result"] != "ok":
                    break
                if stage == "run":
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(answer_path), args.out)
                    winner = base
            if winner or deadline <= time.monotonic():
                break
    return {
        "status": "ok" if winner else "unavailable",
        "provider": winner["provider"] if winner else None,
        "model": winner["model"] if winner else None,
        "out": str(args.out) if winner else None,
        "attempts": attempts,
        "seconds": round(time.monotonic() - started, 1),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one Shipcheck advisor lane with bounded fallback across signed-in model CLIs.",
        epilog="Exit codes: 0 a model answered (written to --out), 1 no candidate answered, 2 usage error.",
    )
    parser.add_argument("--prompt-file", type=Path, required=True, help="lane prompt, UTF-8")
    parser.add_argument("--out", type=Path, required=True, help="where the answering model's output is written")
    parser.add_argument(
        "--try",
        dest="try_",
        type=candidate,
        action="append",
        required=True,
        metavar="PROVIDER[:MODEL]",
        help=f"candidate in preference order; repeatable. Providers: {', '.join(FAMILY)}",
    )
    parser.add_argument("--exclude", action="append", default=[], choices=sorted(FAMILY), help="skip this provider's family")
    parser.add_argument("--canary-timeout", type=float, default=90.0, help="seconds for the one-line health check; 0 skips it")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per real attempt")
    parser.add_argument("--budget", type=float, default=1200.0, help="total seconds for the lane across all candidates")
    parser.add_argument("--cwd", help="working directory for the provider CLI, normally the PR worktree")
    args = parser.parse_args(argv)
    result = run_lane(args)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
