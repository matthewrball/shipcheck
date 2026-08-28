# Shipcheck receipt template

Copy into the PR body or a local `artifacts/` note. Fill every bracket. Do not claim independent review unless a second context actually ran.

```markdown
## Shipcheck receipt

- **Status:** Ready to land | Needs a decision | Review wait timed out
- **Fix mode:** review only | fix safe issues | fix anything in scope
- **Intent:** [one sentence]
- **Must not change:** [files / behavior]
- **Diff scope:** [branch / commits / dirty paths reviewed]
- **Checks run:** [command → pass/fail]
- **Independent reviewer:** [host + model family] | unavailable (same-session fallback)
- **Fixes applied:** [list or “none”]
- **PR:** [url or “not opened”]
- **Delayed feedback:** [quiet / items still open / timed out]
- **Decision needed:** [none | question]
```
