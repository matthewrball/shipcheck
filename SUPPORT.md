# Shipcheck Support Pack

The [MIT skill](skills/shipcheck/) stays free forever. This page is an optional one-time Lightning add-on, not a paywall.

## Price

**21,000 sats**, one time. No subscription. No Stripe. Abendrot is unrelated and stays free.

## What you get

1. Extra host recipes (Claude Code, Cursor, Codex, generic Agent Skills).
2. A receipt template that matches the public `SKILL.md` statuses.
3. A Lightning payment that lands on the same phoenixd node that watches for first inbound.

The install command does not change:

```bash
gh skill install matthewrball/shipcheck shipcheck --agent universal --scope user
```

## Pay (BOLT12)

Tap this if your wallet supports BOLT12: [Pay 21,000 sats](lightning:lno1zrxq8pjw7qjlm68mtp7e3yvxee4y5xrgjhhyf2fxhlphpckrvevh50u0qdpyxyzy7acq3w6gq3fyrwxu7ldqk60qmfwa0z8v94yek4sh7xs02qsr9frx63d9vyjvch23x8ltg0596saaan362cfh88uf8dyqtcuy5ecqqvcfq2plqzpq0h65fd9zx084za55elvc6elg0krqeyxsujrf8s7hy6j29plm56gvwllt3x83fyc4dhu74tz9qvj8d8w9p62w86ld0al42d9l5uyas8nr5mdc3ulcsfru6j3lvrrw2qqsqudzktpu2sfvafctuk7vpc7evg)

Or paste this reusable offer into Phoenix, Zeus, or another BOLT12 wallet. First inbound opens a Lightning channel via the ACINQ LSP.

```
lno1zrxq8pjw7qjlm68mtp7e3yvxee4y5xrgjhhyf2fxhlphpckrvevh50u0qdpyxyzy7acq3w6gq3fyrwxu7ldqk60qmfwa0z8v94yek4sh7xs02qsr9frx63d9vyjvch23x8ltg0596saaan362cfh88uf8dyqtcuy5ecqqvcfq2plqzpq0h65fd9zx084za55elvc6elg0krqeyxsujrf8s7hy6j29plm56gvwllt3x83fyc4dhu74tz9qvj8d8w9p62w86ld0al42d9l5uyas8nr5mdc3ulcsfru6j3lvrrw2qqsqudzktpu2sfvafctuk7vpc7evg
```

Amount: **21000** sat. Description: `shipcheck-support-pack`.

After the payment is seen, the pack files are sent as a reply on the paid invoice / this repo (host recipes + receipt template).

## What this is not

- Not a GitHub Sponsors or Polar checkout.
- Not a paid tier of the skill.
- Not Abendrot, Delegance, or Alinery.
