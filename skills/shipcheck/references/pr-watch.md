# PR watch

Load this file only when the user authorized a PR open, push, or update, or invoked Shipcheck on an existing PR. Do not load it to review a diff without pushing.

`SHIPCHECK_DIR` is the directory containing `SKILL.md`. The watcher never writes to GitHub.

## Snapshot, then one checks re-snapshot

Immediately before an authorized PR open or push, capture a UTC baseline. On an existing-PR handoff with no new push, omit `--since` so a stored baseline is reused. Do not mint a fresh baseline that hides outstanding review.

```bash
baseline=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 -B "$SHIPCHECK_DIR/scripts/watch_pr_feedback.py" --pr <url-or-number> --since "$baseline" --once
```

If the user asked to wait for delayed feedback, run the same command without `--once` as a background job. Do not block the session on the default 20-minute wait. Do not drop `--once` unless they asked to wait.

If they did not ask to wait, and the snapshot is `pending` or `check_count` is 0 right after the push, run one checks-only re-snapshot before the receipt:

```bash
python3 -B "$SHIPCHECK_DIR/scripts/watch_pr_feedback.py" --pr <url-or-number> --checks-only
```

That command re-reads checks once. It does not fetch review feedback, write watcher state, or poll. `feedback: null` means not fetched. Do not treat it as "no feedback"; keep the earlier `--once` feedback. Pending after this re-snapshot still blocks `Ready to land`. `check_count` 0 after it is unreported, not green, and blocks too. Two empty snapshots are not evidence that no checks are configured. Registration can lag both, and a check suite can be queued with no rollup row yet.

An empty rollup does not block only when independent evidence shows no checks are configured. That evidence is not another rollup of this head. Show all of:

- `gh api repos/<owner>/<repo>/actions/workflows` has `total_count` 0
- the base branch has no required status checks
- `gh api repos/<owner>/<repo>/commits/<base-sha>/check-suites` has no suites

A queued, in-progress, or conclusion-less check suite on this head or the base branch means checks have not reported, even when `check_count` is 0. If you cannot show all three, zero blocks. The first empty rollup alone is not green.

Defaults: `--poll-interval 15`, `--quiet-window 120`, `--max-wait 1200`.
Exit codes: `0` settled or snapshot, `1` pending checks, `3` feedback ready, `4` timed out, `5` checks failed, `6` checks need attention, `2` error.

## What the watcher does

- reads comments, reviews, review threads, statuses, and check runs through `gh`;
- counts only the newest run of each check, so a cancelled run that a newer run replaced does not need attention;
- stores acknowledgement state and baselines under `git rev-parse --git-path shipcheck`;
- reports `check_count`, the number of newest runs in the rollup;
- snapshots once with `--once`, re-reads checks once with `--checks-only`, or polls until settle or timeout without `--once`;
- outputs machine-readable JSON;
- never writes to GitHub.

## Classify

Use `after_baseline`, `on_current_head` (present only when the item has a commit), `is_resolved`, `is_outdated`, `likely_noise`, `by_pr_author`, and `blocks_ready`. `blocks_ready` prevents `Ready to land`.

Non-blocking: pre-baseline comments, resolved or outdated threads, empty `APPROVED` or `COMMENTED` reviews, any `DISMISSED` review, bot issue comments, and the PR author's own comments. Bot reviews and thread comments can still block. `CHANGES_REQUESTED` blocks even when it predates the baseline or the body is empty. A question-only review, or feedback that conflicts with intent, needs a user decision.

Ack with repeatable `--ack <ack_id>` only after the work and checks finish. Interrupted work must be delivered again. Non-blocking items may be acked so they drop out of later output. Never ack an actionable item before its fix is done.

A rework cycle is: apply fixes, push, capture a new baseline, watch again. Stop after three rework cycles, the configured timeout, failing or attention-needed checks, conflicting feedback, high-risk requests, or successful quiet settlement.

Do not merge, reply to comments, resolve review threads, submit GitHub reviews, or deploy. `gh pr ready` is allowed only by the rule in `SKILL.md`.
