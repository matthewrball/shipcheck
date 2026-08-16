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


def pull(cross=False):
    return {
        "url": "https://github.com/acme/app/pull/7",
        "number": 7,
        "headRefOid": "abc",
        "isCrossRepository": cross,
    }


def clean_checks(head="abc"):
    return {"headRefOid": head, "statusCheckRollup": []}


def comment(cid="c1", body="Fix this", at="2026-08-14T00:00:00Z", author="reviewer", author_type="User"):
    return {
        "id": cid,
        "body": body,
        "url": "u",
        "createdAt": at,
        "updatedAt": at,
        "author": {"login": author, "__typename": author_type},
    }


class WatchPrFeedbackTests(unittest.TestCase):
    def test_pre_baseline_comment_does_not_block_settlement(self):
        gh = FakeGh(
            [pull()],
            [page(comments=[comment()]), page(comments=[comment()])],
            [clean_checks()],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                "https://github.com/acme/app/pull/7",
                poll_interval=1,
                quiet_window=1,
                max_wait=10,
                since="2026-08-14T00:00:01Z",
            )
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["pr"]["owner"], "acme")
        self.assertFalse(result["pr"]["is_cross_repository"])
        item = result["feedback"][0]
        self.assertEqual(item["ack_id"], "comment:c1:2026-08-14T00:00:00Z")
        self.assertFalse(item["after_baseline"])
        self.assertFalse(item["blocks_ready"])
        self.assertNotIn("on_current_head", item)

    def test_after_baseline_comment_blocks_ready(self):
        gh = FakeGh(
            [pull()],
            [page(comments=[comment(at="2026-08-14T00:00:02Z")])],
            [clean_checks()],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                max_wait=1, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "feedback_ready")
        self.assertTrue(result["feedback"][0]["after_baseline"])
        self.assertTrue(result["feedback"][0]["blocks_ready"])
        self.assertNotIn("on_current_head", result["feedback"][0])

    def test_acknowledged_feedback_settles_after_quiet_window(self):
        gh = FakeGh(
            [pull()],
            [page(comments=[comment()]), page(comments=[comment()])],
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
            [pull()],
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
            "author": {"login": "reviewer", "__typename": "User"},
        }
        first_reply = comment("tc1", "First", "2026-08-14T00:00:01Z")
        second_reply = comment("tc2", "Late", "2026-08-14T00:00:02Z")
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
            [pull()],
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
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                max_wait=1, since="2026-08-14T00:00:00Z"
            )
        self.assertEqual(result["status"], "feedback_ready")
        self.assertEqual({item["id"] for item in result["feedback"]}, {"tc1", "tc2", "r2"})
        thread_items = [item for item in result["feedback"] if item["kind"] == "thread_comment"]
        self.assertTrue(all(item["is_resolved"] and item["is_outdated"] for item in thread_items))
        self.assertTrue(all(not item["blocks_ready"] for item in thread_items))
        review_item = next(item for item in result["feedback"] if item["id"] == "r2")
        self.assertTrue(review_item["blocks_ready"])
        self.assertNotIn("on_current_head", review_item)

    def test_empty_changes_requested_review_is_feedback(self):
        review = {
            "id": "r1",
            "body": "",
            "url": "r",
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": {"oid": "old"},
            "author": {"login": "reviewer", "__typename": "User"},
        }
        gh = FakeGh(
            [pull()],
            [page(reviews=[review])],
            [clean_checks()],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                max_wait=1, since="2026-08-14T00:00:01Z"
            )
        item = result["feedback"][0]
        self.assertEqual(result["status"], "feedback_ready")
        self.assertEqual(item["state"], "CHANGES_REQUESTED")
        self.assertTrue(item["after_baseline"])
        self.assertFalse(item["on_current_head"])
        self.assertTrue(item["blocks_ready"])
        self.assertFalse(item["likely_noise"])

    def test_empty_approved_review_does_not_block(self):
        review = {
            "id": "r1",
            "body": "",
            "url": "r",
            "state": "APPROVED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": {"oid": "abc"},
            "author": {"login": "reviewer", "__typename": "User"},
        }
        gh = FakeGh([pull()], [page(reviews=[review]), page(reviews=[review])], [clean_checks()])
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                poll_interval=1, quiet_window=1, max_wait=10, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "settled")
        self.assertTrue(result["feedback"][0]["likely_noise"])
        self.assertFalse(result["feedback"][0]["blocks_ready"])
        self.assertTrue(result["feedback"][0]["on_current_head"])

    def test_bot_changes_requested_still_blocks(self):
        review = {
            "id": "r1",
            "body": "Please fix the leak",
            "url": "r",
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": {"oid": "abc"},
            "author": {"login": "coderabbitai", "__typename": "Bot"},
        }
        gh = FakeGh([pull()], [page(reviews=[review])], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                once=True, since="2026-08-14T00:00:01Z"
            )
        item = result["feedback"][0]
        self.assertEqual(result["status"], "feedback_ready")
        self.assertFalse(item["likely_noise"])
        self.assertTrue(item["blocks_ready"])

    def test_bot_comment_does_not_block(self):
        gh = FakeGh(
            [pull()],
            [
                page(comments=[comment(at="2026-08-14T00:00:02Z", author="codecov", author_type="Bot")]),
                page(comments=[comment(at="2026-08-14T00:00:02Z", author="codecov", author_type="Bot")]),
            ],
            [clean_checks()],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(
                poll_interval=1, quiet_window=1, max_wait=10, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "settled")
        self.assertTrue(result["feedback"][0]["likely_noise"])
        self.assertFalse(result["feedback"][0]["blocks_ready"])

    def test_terminal_failure_never_settles(self):
        gh = FakeGh(
            [pull()],
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

    def test_cancelled_check_needs_attention(self):
        gh = FakeGh(
            [pull()],
            [page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [
                        {
                            "__typename": "CheckRun",
                            "name": "ci",
                            "status": "COMPLETED",
                            "conclusion": "CANCELLED",
                        }
                    ],
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(max_wait=10)
        self.assertEqual(result["status"], "checks_need_attention")
        self.assertEqual(result["attention_checks"][0]["conclusion"], "CANCELLED")

    def test_once_snapshot_when_clean(self):
        gh = FakeGh([pull()], [page()], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(once=True)
        self.assertEqual(result["status"], "snapshot")
        self.assertEqual(result["feedback"], [])

    def test_once_returns_pending_without_waiting(self):
        gh = FakeGh(
            [pull()],
            [page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [{"__typename": "StatusContext", "context": "ci", "state": "PENDING"}],
                }
            ],
        )
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, clock, clock.sleep, Path(tmp) / "state.json").watch(once=True)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(clock.now, 0)

    def test_once_reports_blocking_feedback(self):
        gh = FakeGh(
            [pull()],
            [page(comments=[comment(at="2026-08-14T00:00:02Z")])],
            [clean_checks()],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                once=True, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "feedback_ready")
        self.assertTrue(result["feedback"][0]["blocks_ready"])

    def test_cross_repository_flag_is_surfaced(self):
        gh = FakeGh([pull(cross=True)], [page()], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(once=True)
        self.assertTrue(result["pr"]["is_cross_repository"])

    def test_waiting_check_is_pending(self):
        gh = FakeGh(
            [pull()],
            [page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [
                        {"__typename": "CheckRun", "name": "ci", "status": "WAITING", "conclusion": ""}
                    ],
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(once=True)
        self.assertEqual(result["status"], "pending")

    def test_once_reports_failure_even_if_another_check_is_pending(self):
        gh = FakeGh(
            [pull()],
            [page()],
            [
                {
                    "headRefOid": "abc",
                    "statusCheckRollup": [
                        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
                        {"__typename": "CheckRun", "name": "ci", "status": "IN_PROGRESS", "conclusion": ""},
                    ],
                }
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(once=True)
        self.assertEqual(result["status"], "checks_failed")

    def test_pre_baseline_changes_requested_still_blocks(self):
        review = {
            "id": "r1",
            "body": "",
            "url": "r",
            "state": "CHANGES_REQUESTED",
            "submittedAt": "2026-08-14T00:00:00Z",
            "commit": {"oid": "abc"},
            "author": {"login": "reviewer", "__typename": "User"},
        }
        gh = FakeGh([pull()], [page(reviews=[review])], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                once=True, since="2026-08-14T00:00:01Z"
            )
        item = result["feedback"][0]
        self.assertEqual(result["status"], "feedback_ready")
        self.assertFalse(item["after_baseline"])
        self.assertTrue(item["blocks_ready"])

    def test_dismissed_review_with_body_does_not_block(self):
        review = {
            "id": "r1",
            "body": "Please fix the leak",
            "url": "r",
            "state": "DISMISSED",
            "submittedAt": "2026-08-14T00:00:02Z",
            "commit": {"oid": "abc"},
            "author": {"login": "reviewer", "__typename": "User"},
        }
        gh = FakeGh([pull()], [page(reviews=[review])], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                once=True, since="2026-08-14T00:00:01Z"
            )
        self.assertEqual(result["status"], "snapshot")
        self.assertTrue(result["feedback"][0]["likely_noise"])
        self.assertFalse(result["feedback"][0]["blocks_ready"])

    def test_bot_thread_comment_still_blocks(self):
        thread = {
            "id": "thread1",
            "isResolved": False,
            "isOutdated": False,
            "path": "app.py",
            "line": 3,
            "comments": {
                "nodes": [
                    comment("tc1", "This leaks", "2026-08-14T00:00:02Z", author="coderabbitai", author_type="Bot")
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
        gh = FakeGh([pull()], [page(threads=[thread])], [clean_checks()])
        with tempfile.TemporaryDirectory() as tmp:
            result = watch.Watcher(gh, state_path=Path(tmp) / "state.json").watch(
                once=True, since="2026-08-14T00:00:01Z"
            )
        item = result["feedback"][0]
        self.assertEqual(result["status"], "feedback_ready")
        self.assertFalse(item["likely_noise"])
        self.assertTrue(item["blocks_ready"])


if __name__ == "__main__":
    unittest.main()
