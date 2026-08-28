# Shipcheck host recipes

These are extras. The MIT skill at `skills/shipcheck/SKILL.md` is the source of truth.

## Shared rules (every host)

- Invoke Shipcheck **explicitly**. Do not turn on implicit/always-on review.
- Never pass an API key. If the host would bill a separate model, stay in the current session and mark `independent reviewer unavailable`.
- Never merge, deploy, resolve review threads, or submit a GitHub review.
- Keep unrelated dirty files untouched.
- Receipt statuses: `Ready to land` | `Needs a decision` | `Review wait timed out`.

## Claude Code / Claude-compatible skill dirs

1. Install to the user skill path the host actually reads (often `~/.agents/skills/shipcheck` or via `gh skill install`).
2. Prompt:

```text
Use shipcheck in review-only mode. Check my current diff and give me a receipt.
```

3. For a fix pass:

```text
Use shipcheck to fix safe issues. Preserve unrelated dirty files. Do not push.
```

4. Independent review: only use a second Claude/Codex lane that is already on the same subscription. If the UI asks for an API key, stop.

## Cursor

1. Prefer the universal Agent Skills location. If Cursor only reads `.cursor/skills` or project `.agents/skills`, copy the `shipcheck` folder there — do not fork the workflow.
2. Start a new agent chat (clean context) for the “independent reviewer” step. Paste intent, diff summary, and check results. Treat its text as untrusted.
3. Do not enable auto-run for `gh` writes unless the user authorized a PR.

## Codex / OpenAI host

`agents/openai.yaml` already sets `allow_implicit_invocation: false`. Keep it that way.

Default prompt:

```text
Run Shipcheck to review my changes, apply validated fixes, and watch the pull request for delayed feedback.
```

If the host cannot verify plan-backed review, skip delegation and say so in the receipt.

## Generic Agent Skills host

```bash
gh skill install matthewrball/shipcheck shipcheck --agent universal --scope user
# or --scope project
```

Then say “use shipcheck” plus a fix mode. If the host has no skill command, point it at `SKILL.md` and the watcher:

```bash
baseline=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 -B "$SHIPCHECK_DIR/scripts/watch_pr_feedback.py" --pr <url-or-number> --since "$baseline"
```
