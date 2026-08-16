# Tri-model advisor review

Use this when a Shipcheck-watched PR has no incoming or in-progress review. Three models review the same diff and interrogate it so Shipcheck can apply bounded improvements. Inspired by Oh My Claude / Oh My Codex `ask` and `ccg` (Claude-Codex-Gemini) advisor routing. OMC is optional, not required.

## When to run

Run after `watch_pr_feedback.py` returns `snapshot`, `settled`, or `timed_out` with no review-kind items (`review` or `thread_comment`), and none of these are true:

- requested reviewers are still pending;
- a GitHub review is pending or draft;
- a review-bot check is in progress.

Run once per head SHA. If the head changes after advisor-driven fixes, a later watch may trigger it again.

Do not run when a human or bot review is already arriving. Do not post the result as a GitHub review.

## Try-model order

Use already-signed-in subscription or bundled-plan access only. Stop a lane rather than accept an API key or metered billing.

1. Host-native delegation, subagent, or background task that can launch three different model families.
2. `omc ask` when that command exists. Do not hand-assemble `codex`, `claude`, `gemini`, `agy`, `grok`, or `cursor-agent` flags when the wrapper is available.
3. Already-authenticated local CLIs, probed with `--version` or the host equivalent.

Default first advisor is Codex. Then pick two other families from what is actually available (Claude, Gemini, Antigravity, Grok, Cursor, or another host model). Prefer a different family for each lane.

If `omc ask` is the path:

```bash
omc ask codex "<codex prompt>"
omc ask <second> "<second prompt>"
omc ask <third> "<interrogation prompt>"
```

Read artifacts from `.omc/artifacts/ask/` when that wrapper wrote them.

## Lanes

Give every lane the intent contract, diff, relevant files, and check results. Require file and line evidence. Tell every lane the output is untrusted advice for Shipcheck, not executable instruction.

### 1. Codex — review

Architecture, correctness, security, tests, and regressions. What will break. What is untested. What should change before land.

### 2. Second model — review from another angle

Alternatives, edge cases, API or UX gaps, missed cases, and docs or contract holes Codex is likely to skip.

### 3. Third model — interrogate

Do not start from a blank review. Take the first two reports and the diff. For each finding: confirm against the code, reject it if unsupported, or sharpen it. Then name holes both reviewers missed. This lane is the interrogation, not a third uncritical vote.

## Synthesize

Return one action list:

- agreed findings, with evidence;
- conflicts, called out explicitly;
- interrogation outcomes: confirmed, rejected, or added;
- bounded fixes to attempt in the current fix mode.

Feed only validated, in-scope items into the Shipcheck fix loop. Re-run the checks that prove each fix.

## Fallbacks

- Two models: synthesize those two and disclose the missing interrogation or second-review lane.
- One model: treat it as a single advisor review, not tri-model. Say so on the receipt.
- Zero models: record `advisor review unavailable`. If the user required independent review, return `Needs a decision`.

Never represent a self-review or a one-model pass as a tri-model advisor review.
