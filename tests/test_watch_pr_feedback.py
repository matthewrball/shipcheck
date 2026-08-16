import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "shipcheck" / "scripts" / "watch_pr_feedback.py"
spec = importlib.util.spec_from_file_location("watch_pr_feedback", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeGh:
    def __init__(self, pulls, feedback, checks, thread_pages=()):
        self.pulls = list(pulls)
        self.feedback_pages = list(feedback)
        self.feedback_pages_last = self.feedback_pages[-1]
        self.checks = list(checks)
        self.current_check = None
        self.thread_pages = list(thread_pages)

    def __call__(self, args):
        if args[:2] == ["pr", "view"]:
            if "headRefOid,statusCheckRollup" in args:
                return self.checks[0] if len(self.checks) == 1 else self.checks.pop(0)
            return self.pulls[min(len(self.pulls) - 1, 0)]
        if args[:2] == ["api", "graphql"]:
            query = " ".join(args)
            if "node(id:" in query or "node($id" in query:
                if self.thread_pages:
                    return self.thread_pages.pop(0)
                return {"data": {"node": {"comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
            page = self.feedback_pages.pop(0) if self.feedback_pages else self.feedback_pages_last
            self.feedback_pages_last = page
            return page
        raise AssertionError(args)


def page(head="abc", comments=(), reviews=(), threads=(), comments_next=None, reviews_next=None, threads_next=None):
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": head,
                    "comments": {
                        "nodes": list(comments),
                        "pageInfo": {
                            "hasNextPage": comments_next is not None,
                            "endCursor": comments_next,
                        },
                    },
                    "reviews": {
                        "nodes": list(reviews),
                        "pageInfo": {
                            "hasNextPage": reviews_next is not None,
                            "endCursor": reviews_next,
                        },
                    },
                    "reviewThreads": {
                        "nodes": list(threads),
                        "pageInfo": {
                            "hasNextPage": threads_next is not None,
                            "endCursor": threads_next,
                        },
                    },
                }
            }
        }
    }


def clean_checks(head="abc"):
    return {"headRefOid": head, "statusCheckRollup": []}


class WatchPrFeedbackTests(unittest.TestCase):
    def test_returns_unacknowledged_comment_from_explicit_url(self):
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [page(comments=[{"id": "c1", "body": "Fix this", "url": "u", "createdAt": "2026-08-14T00:00:00Z", "updatedAt": "2026-08-14T00:00:00Z", "author": {"login": "bot"}}])],
            [clean_checks()],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                "https://github.com/acme/app/pull/7",
                quiet_window=1,
                max_wait=10,
                since="2026-08-14T00:00:01Z",
            )
        self.assertEqual(result["status"], "feedback_ready")
        self.assertEqual(result["pr"]["owner"], "acme")
        self.assertEqual(result["feedback"][0]["ack_id"], "comment:c1:2026-08-14T00:00:00Z")
        self.assertFalse(result["feedback"][0]["after_baseline"])

    def test_acknowledged_feedback_settles_after_quiet_window(self):
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [
                page(comments=[{"id": "c1", "body": "Fix this", "url": "u", "createdAt": "2026-08-14T00:00:00Z", "updatedAt": "2026-08-14T00:00:00Z", "author": {"login": "bot"}}]),
                page(comments=[{"id": "c1", "body": "Fix this", "url": "u", "createdAt": "2026-08-14T00:00:00Z", "updatedAt": "2026-08-14T00:00:00Z", "author": {"login": "bot"}}]),
            ],
            [clean_checks()],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                ack=["comment:c1:2026-08-14T00:00:00Z"], poll_interval=1, quiet_window=1, max_wait=10
            )
        self.assertEqual(result["status"], "settled")
        self.assertEqual(clock.now, 1)

    def test_pending_check_resets_quiet_until_terminal(self):
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [page(), page(), page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [{"__typename": "StatusContext", "context": "ci", "state": "PENDING"}],
                },
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [{"__typename": "StatusContext", "context": "ci", "state": "SUCCESS"}],
                },
            ],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                poll_interval=1, quiet_window=2, max_wait=10
            )
        self.assertEqual(result["status"], "settled")
        self.assertEqual(clock.now, 3)

    def test_paginates_reviews_and_nested_thread_replies(self):
        review = {
            "id": "r2",
            "body": "Second page",
            "url": "r",
            "state": "COMMENTED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": None,
            "author": {"login": "reviewer"},
        }
        first_reply = {"id": "tc1", "body": "First", "url": "1", "createdAt": "2026-08-14T00:00:01Z", "updatedAt": "2026-08-14T00:00:01Z"}
        second_reply = {"id": "tc2", "body": "Late", "url": "2", "createdAt": "2026-08-14T00:00:02Z", "updatedAt": "2026-08-14T00:00:02Z"}
        thread = {
            "id": "thread1",
            "isResolved": True,
            "isOutdated": True,
            "path": "app.py",
            "line": 3,
            "comments": {
                "nodes": [first_reply],
                "pageInfo": {"hasNextPage": True, "endCursor": "tc1"},
            },
        }
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [page(threads=[thread], reviews_next="r1"), page(reviews=[review])],
            [clean_checks()],
            thread_pages=[
                {
                    "data": {
                        "node": {
                            "comments": {
                                "nodes": [second_reply],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(max_wait=1)
        self.assertEqual(result["status"], "feedback_ready")
        self.assertEqual(
            {item["id"] for item in result["feedback"]},
            {"tc1", "tc2", "r2"},
        )
        thread_items = [item for item in result["feedback"] if item["kind"] == "thread_comment"]
        self.assertTrue(all(item["is_resolved"] and item["is_outdated"] for item in thread_items))

    def test_empty_changes_requested_review_is_feedback(self):
        review = {
            "id": "r1",
            "body": "",
            "url": "r",
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": {"oid": "old"},
            "author": {"login": "reviewer"},
        }
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [page(reviews=[review])],
            [clean_checks()],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                max_wait=1, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "feedback_ready")
        self.assertEqual(result["feedback"][0]["state"], "CHANGES_REQUESTED")
        self.assertTrue(result["feedback"][0]["after_baseline"])
        self.assertFalse(result["feedback"][0]["on_current_head"])

    def test_terminal_failure_never_settles(self):
        gh = FakeGh(
            [{"url": "https://github.com/acme/app/pull/7", "number": 7, "headRefOid": "abc"}],
            [page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [{"__typename": "StatusContext", "context": "ci", "state": "FAILURE"}],
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(max_wait=10)
        self.assertEqual(result["status"], "checks_failed")


if __name__ == "__main__":
    unittest.main()
