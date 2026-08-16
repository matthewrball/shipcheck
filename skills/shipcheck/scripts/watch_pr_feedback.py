#!/usr/bin/env python3
"""Watch a GitHub PR for delayed review feedback.

Uses only stdlib plus the GitHub CLI. It never writes to GitHub.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PASSING = {"SUCCESS", "NEUTRAL", "SKIPPED"}


class GhError(RuntimeError):
    pass


def run_gh(args: list[str]) -> dict:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=False)
    if proc.returncode:
        raise GhError(proc.stderr.strip() or proc.stdout.strip() or "gh failed")
    return json.loads(proc.stdout or "{}")


def parse_repo(url: str) -> tuple[str, str]:
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+)/pull/\d+", url)
    if not match:
        raise ValueError(f"cannot parse canonical PR URL: {url}")
    return match.group(1), match.group(2).removesuffix(".git")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def state_file() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--git-path", "shipcheck"],
        text=True,
        capture_output=True,
        check=False,
    )
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or "not a git repository")
    path = Path(out.stdout.strip())
    path.mkdir(parents=True, exist_ok=True)
    return path / "pr-feedback-state.json"


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"prs": {}}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


class Watcher:
    def __init__(self, run=run_gh, clock=time.monotonic, sleep=time.sleep, state_path: Path | None = None):
        self.run = run
        self.clock = clock
        self.sleep = sleep
        self.state_path = state_path

    def resolve_pr(self, pr: str | None) -> dict:
        args = ["pr", "view"]
        if pr:
            args.append(pr)
        args += ["--json", "url,number,headRefOid,isCrossRepository"]
        data = self.run(args)
        owner, repo = parse_repo(data["url"])
        return {
            "url": data["url"],
            "number": int(data["number"]),
            "owner": owner,
            "repo": repo,
            "head": data["headRefOid"],
            "is_cross_repository": bool(data.get("isCrossRepository")),
        }

    def graphql(
        self,
        pr: dict,
        comments_cursor: str | None = None,
        reviews_cursor: str | None = None,
        threads_cursor: str | None = None,
    ) -> dict:
        query = """
        query($owner:String!, $repo:String!, $number:Int!, $commentsCursor:String, $reviewsCursor:String, $threadsCursor:String) {
          repository(owner:$owner, name:$repo) {
            pullRequest(number:$number) {
              headRefOid
              comments(first:100, after:$commentsCursor) {
                nodes { id body url createdAt updatedAt author { login } }
                pageInfo { hasNextPage endCursor }
              }
              reviews(first:100, after:$reviewsCursor) {
                nodes { id body url state submittedAt commit { oid } author { login } }
                pageInfo { hasNextPage endCursor }
              }
              reviewThreads(first:100, after:$threadsCursor) {
                nodes {
                  id isResolved isOutdated path line
                  comments(first:100) {
                    nodes { id body url createdAt updatedAt author { login } }
                    pageInfo { hasNextPage endCursor }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        args = [
            "api",
            "graphql",
            "-f",
            f"owner={pr['owner']}",
            "-f",
            f"repo={pr['repo']}",
            "-F",
            f"number={pr['number']}",
            "-f",
            f"query={query}",
        ]
        for name, cursor in (
            ("commentsCursor", comments_cursor),
            ("reviewsCursor", reviews_cursor),
            ("threadsCursor", threads_cursor),
        ):
            if cursor:
                args += ["-f", f"{name}={cursor}"]
        return self.run(args)

    def thread_comments(self, thread_id: str, cursor: str) -> list[dict]:
        query = """
        query($id:ID!, $cursor:String) {
          node(id:$id) {
            ... on PullRequestReviewThread {
              comments(first:100, after:$cursor) {
                nodes { id body url createdAt updatedAt author { login } }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        out = self.run(["api", "graphql", "-f", f"id={thread_id}", "-f", f"cursor={cursor}", "-f", f"query={query}"])
        conn = out["data"]["node"]["comments"]
        comments = list(conn["nodes"])
        while conn["pageInfo"]["hasNextPage"]:
            out = self.run(
                [
                    "api",
                    "graphql",
                    "-f",
                    f"id={thread_id}",
                    "-f",
                    f"cursor={conn['pageInfo']['endCursor']}",
                    "-f",
                    f"query={query}",
                ]
            )
            conn = out["data"]["node"]["comments"]
            comments += conn["nodes"]
        return comments

    def feedback(self, pr: dict) -> tuple[str, list[dict]]:
        items: list[dict] = []
        comments_cursor = reviews_cursor = threads_cursor = None
        head = pr["head"]
        seen = set()
        while True:
            pull = self.graphql(pr, comments_cursor, reviews_cursor, threads_cursor)["data"]["repository"]["pullRequest"]
            head = pull["headRefOid"]
            for node in pull["comments"]["nodes"]:
                self.add_item(items, seen, self.item("comment", node))
            for node in pull["reviews"]["nodes"]:
                self.add_item(
                    items,
                    seen,
                    self.item(
                        "review",
                        node,
                        state=node.get("state"),
                        commit=(node.get("commit") or {}).get("oid"),
                    ),
                )
            for thread in pull["reviewThreads"]["nodes"]:
                comments = list(thread["comments"]["nodes"])
                if thread["comments"]["pageInfo"]["hasNextPage"]:
                    comments += self.thread_comments(thread["id"], thread["comments"]["pageInfo"]["endCursor"])
                for node in comments:
                    self.add_item(
                        items,
                        seen,
                        self.item(
                            "thread_comment",
                            node,
                            thread=thread["id"],
                            path=thread.get("path"),
                            line=thread.get("line"),
                            is_resolved=bool(thread.get("isResolved")),
                            is_outdated=bool(thread.get("isOutdated")),
                        ),
                    )
            comments_page = pull["comments"]["pageInfo"]
            reviews_page = pull["reviews"]["pageInfo"]
            threads_page = pull["reviewThreads"]["pageInfo"]
            if not (comments_page["hasNextPage"] or reviews_page["hasNextPage"] or threads_page["hasNextPage"]):
                break
            comments_cursor = comments_page["endCursor"] if comments_page["hasNextPage"] else comments_cursor
            reviews_cursor = reviews_page["endCursor"] if reviews_page["hasNextPage"] else reviews_cursor
            threads_cursor = threads_page["endCursor"] if threads_page["hasNextPage"] else threads_cursor
        return head, items

    @staticmethod
    def add_item(items: list[dict], seen: set[str], item: dict) -> None:
        if item["ack_id"] not in seen:
            seen.add(item["ack_id"])
            items.append(item)

    @staticmethod
    def item(kind: str, node: dict, **extra) -> dict:
        updated = node.get("updatedAt") or node.get("submittedAt") or node.get("createdAt")
        return {
            "ack_id": f"{kind}:{node['id']}:{updated}",
            "kind": kind,
            "id": node["id"],
            "body": node.get("body") or "",
            "url": node.get("url"),
            "author": (node.get("author") or {}).get("login"),
            "updated_at": updated,
            **{k: v for k, v in extra.items() if v is not None},
        }

    def checks(self, pr: dict) -> dict:
        data = self.run(["pr", "view", pr["url"], "--json", "headRefOid,statusCheckRollup"])
        rollup = data.get("statusCheckRollup") or []
        signature = sorted(
            (
                item.get("__typename") or "",
                item.get("name") or item.get("context") or "",
                item.get("status") or item.get("state") or "",
                item.get("conclusion") or "",
            )
            for item in rollup
        )
        pending, failing = [], []
        for item in rollup:
            status = (item.get("status") or item.get("state") or "").upper()
            conclusion = (item.get("conclusion") or "").upper()
            if status in {"QUEUED", "IN_PROGRESS", "PENDING", "EXPECTED"}:
                pending.append(item)
            elif status in {"FAILURE", "ERROR"} or (conclusion and conclusion not in PASSING):
                failing.append(item)
        return {
            "head": data["headRefOid"],
            "signature": signature,
            "pending": pending,
            "failing": failing,
        }

    def watch(
        self,
        pr_arg=None,
        poll_interval=15.0,
        quiet_window=120.0,
        max_wait=1200.0,
        ack=(),
        since: str | None = None,
    ) -> dict:
        pr = self.resolve_pr(pr_arg)
        path = self.state_path or state_file()
        state = load_state(path)
        key = pr["url"]
        pr_state = state["prs"].setdefault(key, {"acknowledged": [], "baselines": {}})
        baselines = pr_state.setdefault("baselines", {})
        baseline = since or baselines.get(pr["head"]) or utc_now()
        parse_timestamp(baseline)
        baselines[pr["head"]] = baseline
        pr_state["acknowledged"] = sorted(set(pr_state.get("acknowledged", [])) | set(ack))
        save_state(path, state)

        deadline = self.clock() + max_wait
        quiet_since = self.clock()
        last_head = pr["head"]
        last_sig = None
        while True:
            head, items = self.feedback(pr)
            if head != last_head:
                last_head = head
                quiet_since = self.clock()
            check = self.checks(pr)
            if check["head"] != head:
                head = check["head"]
            if head != last_head:
                last_head = head
                baseline = baselines.setdefault(head, utc_now())
                save_state(path, state)
                quiet_since = self.clock()
            if check["signature"] != last_sig:
                last_sig = check["signature"]
                quiet_since = self.clock()
            seen = set(pr_state.get("acknowledged", []))
            baseline_time = parse_timestamp(baseline)
            for item in items:
                updated = item.get("updated_at")
                item["after_baseline"] = bool(updated and parse_timestamp(updated) >= baseline_time)
                item["on_current_head"] = item.get("commit") in {None, head}
                item["baseline_at"] = baseline
            unacked = [item for item in items if item["ack_id"] not in seen]
            if unacked:
                return self.result("feedback_ready", pr, head, unacked, check)
            now = self.clock()
            if now >= deadline:
                return self.result("timed_out", pr, head, [], check)
            if not check["pending"] and check["failing"]:
                return self.result("checks_failed", pr, head, [], check)
            if not check["pending"] and not check["failing"] and now - quiet_since >= quiet_window:
                pr_state["last_observed_head"] = head
                pr_state["last_observed_at"] = utc_now()
                save_state(path, state)
                return self.result("settled", pr, head, [], check)
            self.sleep(min(poll_interval, max(0.0, deadline - now)))

    @staticmethod
    def result(status: str, pr: dict, head: str, feedback: list[dict], check: dict) -> dict:
        return {
            "status": status,
            "pr": {"url": pr["url"], "number": pr["number"], "owner": pr["owner"], "repo": pr["repo"]},
            "head": head,
            "feedback": feedback,
            "pending_checks": check["pending"],
            "failing_checks": check["failing"],
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", help="PR URL or number; defaults to current branch PR")
    parser.add_argument("--poll-interval", type=float, default=15.0)
    parser.add_argument("--quiet-window", type=float, default=120.0)
    parser.add_argument("--max-wait", type=float, default=1200.0)
    parser.add_argument("--ack", action="append", default=[], help="acknowledge a returned ack_id")
    parser.add_argument("--since", help="UTC watch baseline captured before PR open/push")
    parser.add_argument("--state-dir", type=Path, help="override the Git-local state directory")
    args = parser.parse_args(argv)
    try:
        state_path = args.state_dir / "pr-feedback-state.json" if args.state_dir else None
        result = Watcher(state_path=state_path).watch(
            args.pr, args.poll_interval, args.quiet_window, args.max_wait, args.ack, args.since
        )
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return {"settled": 0, "feedback_ready": 3, "timed_out": 4, "checks_failed": 5}.get(result["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
