# Shipcheck receipt template

One format. `SKILL.md` copies the block below. Do not add sections, bold, or a second template. Do not put this receipt, the word Shipcheck, or a shipcheck status in a PR body unless the user explicitly asks.

Omit a row only when it cannot exist. These rows can exist: use `none`, `not run`, or `skipped (routine)` instead of deleting them. `Next` is the only forward-looking row. It answers "what's next?" in one line. It is not a second report.

```markdown
# Shipcheck receipt
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
- Next: <one line>
```

On the Watch row, append `; checks-only <status>` when that re-snapshot ran. Do not add a row for it.

Persist the filled receipt under `git rev-parse --git-path shipcheck`, in `receipts.json`, next to the watcher state. Append to the `receipts` array. Do not replace other entries. Fields are only:

- `head`: full `git rev-parse HEAD`
- `fingerprint`: stdout of `scripts/receipt_status.py --fingerprint`
- `reviewed_paths`: files or directories included in the review
- `excluded_paths`: unrelated dirty paths excluded from the review
- `saved_at`: UTC timestamp
- `receipt`: the markdown block, nothing else

Do not store file contents, diffs, secrets, or review bodies. A reviewed directory covers new, changed, and deleted untracked files inside it. Excluded paths are not hashed. A later commit is a new head and does not match. Do not treat the same bytes, now committed, as this receipt.

`scripts/receipt_status.py` is read-only. It recomputes the fingerprint and prints `current` plus the receipt, `stale`, `historical`, or `none for this scope`. `current` is the only offline `Ready to land` it may repeat, and only when the receipt has no PR. A PR receipt is `historical` even when the head and fingerprint still match. `reviewed scope unchanged` is a fact next to that label, not a new `Ready to land`.
