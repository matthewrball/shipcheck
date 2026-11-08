# Tri-model advisor review

Use this when a Shipcheck-watched PR has no incoming or in-progress review. Three models review the same diff and interrogate it so Shipcheck can apply bounded improvements. Inspired by Oh My Claude / Oh My Codex `ask` and `ccg` (Claude-Codex-Gemini) advisor routing; Shipcheck runs its own bounded lanes and does not need OMC.

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
2. Already-authenticated local CLIs through `scripts/run_advisor_lane.py`. It owns the `codex`, `claude`, `gemini`, `agy`, `grok`, and `cursor-agent` flags, so never hand-assemble them. Do not run a lane through bare `omc ask`: it puts no deadline on its `--version` probe or on most provider runs, so a lane can hang indefinitely.

Default first advisor is Codex. Then pick two other families from what is actually available (Claude, Gemini, Antigravity, Grok, Cursor, or another host model). Prefer a different family for each lane.

## Never wait on a lane

An advisor lane must never delay shipping. Apply these rules to every lane, host-native or CLI:

- **Check first.** Send each model a one-line canary before the review. A usage-limit, quota, credits, rate-limit, or sign-in error, or no answer by the canary deadline, means that model is out for this run.
- **Substitute, don't wait.** When Gemini is out, use Grok 4.7 (`grok-4.7`) next, then any other signed-in family that no other lane is using.
- **Stop at the deadline.** Kill a lane that runs past its deadline, record it, and move on. A missing or timed-out advisor lane never blocks `Ready to land` by itself.
- **Disclose.** Put every substitution and missing lane on the receipt, e.g. `gemini out of usage → grok-4.7`.

`run_advisor_lane.py` applies these rules mechanically. Resolve `SHIPCHECK_DIR` to the directory containing `SKILL.md`, and point `--cwd` at a scratch worktree, never the user's shared checkout. Lanes 1 and 2 are independent, so run them in parallel with disjoint candidate lists; start lane 3 after both finish. Run the block as one host background job:

```bash
lane="$SHIPCHECK_DIR/scripts/run_advisor_lane.py"
python3 -B "$lane" --cwd <worktree> --prompt-file lane1.md --out lane1-answer.md --try codex --try claude > lane1.json &
python3 -B "$lane" --cwd <worktree> --prompt-file lane2.md --out lane2-answer.md --try gemini --try grok:grok-4.7 --try cursor > lane2.json &
wait
python3 -B "$lane" --cwd <worktree> --prompt-file lane3.md --out lane3-answer.md \
  --try claude --try grok:grok-4.7 --try gemini --try codex \
  --exclude <lane 1 provider> --exclude <lane 2 provider> > lane3.json
```

For lane 3, exclude every provider that answered lane 1 or 2, and every provider those lanes found `exhausted`, `signed_out`, or `timed_out`, so lane 3 never waits on the same failure twice.

Each run prints one JSON line: `status` (`ok` or `unavailable`), the answering `provider` and `model`, `out`, and every attempt with its `result` (`ok`, `missing`, `excluded`, `skipped`, `exhausted`, `signed_out`, `timed_out`, or `failed`). Exit `0` means the answer is in `--out`; exit `1` means no candidate answered, so record the lane missing. Defaults: `--canary-timeout 90`, `--timeout 900` per attempt, `--budget 1200` per lane. The runner kills each provider's whole process group at its deadline, so it returns within seconds of its budget. It strips metered API-key variables (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`) from the lane's environment.

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
