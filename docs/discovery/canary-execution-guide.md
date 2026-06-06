---
date: 2026-06-06
kind: execution-guide
builds_on: docs/brainstorms/2026-06-06-dofollow-channel-expansion-requirements.md
status: operator-ready
---

# Canary Blitz — Execution Guide

## Overview

After the infrastructure phase (R2 `--include-uncertain` flag) and the
preliminary evidence phase (HTTP probes + browser-tier rel inspection), the
next step is **formal canary verification**: publish one post per platform
via our pipeline, then run `canary-targets` to verify the rel attribute.

## Targets

### Group A: Newly confirmed dofollow (browser-tier evidence) — verify

These 8 platforms had their browser-tier rel inspection show no nofollow.
Now we need formal canary-targets verification to flip from
`dofollow="uncertain"` to `dofollow=True` (already flipped in registry).

| Platform | Adapter exists? | Need publish? | Browser evidence |
|---|---|---|---|
| substack | Yes | Yes — publish via adapter | No rel on post external links |
| writeas | Yes | Yes | No rel on external links |
| rentry | Yes | Yes | Empty rel (dofollow) |
| hatena | Yes | Yes | No rel on user post body links |
| mataroa | Yes | Yes | No rel on post-body links |
| posteasy | Yes | Yes | No rel on external links |
| brewpage | Yes | Yes | rel=noopener only |
| nonograph | Yes | Yes | No rel on external links |

**Action:** Publish one post per platform. Add `[canary.<platform>]` config.
Run `canary-targets`.

### Group B: Remaining uncertain — resolve

| Platform | Blocking issue | Rel evidence | Action |
|---|---|---|---|
| hackmd | Cloudflare/403 for bots; client-side render | 188/0 community dofollow | Publish via adapter, then `canary-targets --include-uncertain` |
| hashnode | Cloudflare-gated. No user blog with real links found | 3rd-party dofollow | Publish via adapter, then `canary-targets --include-uncertain` |
| gitlabpages | about.gitlab.io 403'd (product page, not user pages) | Operator-controlled rel | Publish via ghpages/a gitlab ci, then canary |
| pubmark | Not indexed. Can't find a URL to inspect | Browser probe said dofollow | Publish via adapter, canary with `--include-uncertain` |
| txtfyi | Not indexed. Can't find a URL to inspect | Phase 0 said dofollow | Publish via adapter, canary with `--include-uncertain` |
| htmldrop | Login wall. Low priority. | No data | Mark as retired or skip |

## Config Template

```toml
# Template for [canary.<platform>]. Add to ~/.config/backlink-publisher/config.toml

# Group A: Newly flipped dofollow (verify with canary-targets)
[canary.substack]
post_url = "<your-substack-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet from post>"

[canary.writeas]
post_url = "<your-writeas-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.rentry]
post_url = "<your-rentry-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.hatena]
post_url = "<your-hatena-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.mataroa]
post_url = "<your-mataroa-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.posteasy]
post_url = "<your-posteasy-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.brewpage]
post_url = "<your-brewpage-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.nonograph]
post_url = "<your-nonograph-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

# Group B: Remaining uncertain (verify with canary-targets --include-uncertain)
[canary.hackmd]
post_url = "<your-hackmd-doc-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.hashnode]
post_url = "<your-hashnode-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.pubmark]
post_url = "<your-pubmark-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"

[canary.txtfyi]
post_url = "<your-txtfyi-post-url>"
expected_target = "<your-target-url>"
marker = "<text snippet>"
```

## Execution Steps

For each platform:

1. **Publish** one article containing a backlink via the pipeline's adapter:
   ```bash
   cat seeds/one-row.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --platform <name> --mode draft
   ```
   Or directly via the adapter if pipeline is not set up for that platform.

2. **Get the published URL** and note the post ID.

3. **Add config** entry under `[canary.<platform>]` in config.toml.

4. **Run canary-targets**:
   ```bash
   # For newly flipped (already dofollow=True, so included by default):
   canary-targets
   
   # For remaining uncertain (need --include-uncertain):
   canary-targets --include-uncertain
   
   # To test a single platform:
   canary-targets --platform <name> [--include-uncertain]
   ```

5. **Check verdict**: If verdict is `drift_confirmed` with `rel=dofollow`,
   the platform is confirmed. Update `canary-blitz-verdicts.md`.

## Current Registry State

After the preliminary browser-tier flips:

- **dofollow=True (13):** blogger, medium, telegraph, velog, ghpages,
  brewpage, hatena, mataroa, nonograph, posteasy, rentry, substack, writeas
- **dofollow=False (9):** linkedin, tumblr, livejournal, devto, notion,
  mastodon, qiita, zenn, wordpresscom
- **dofollow="uncertain" (6):** gitlabpages, hackmd, hashnode, htmldrop,
  pubmark, txtfyi

## Post-Canary Actions

| Verdict | Action |
|---|---|
| `rel=dofollow` confirmed | Update `canary-blitz-verdicts.md`. No registry change needed (already flipped). Run `plan-gap` to verify it picks up the new platform. |
| `rel=nofollow` found | Flip `dofollow` to `False` in `adapters/__init__.py`. Add rationale. Update tracking doc. |
| `rel=nofollow` found on Group B (uncertain) | No change needed (still uncertain). Add note. |
| Inconclusive (404, login wall, etc.) | Add note in tracking doc. Consider browser recheck. |

## Expected Outcomes

- All 8 Group A platforms should confirm dofollow (browser evidence strongly
  suggests it)
- At least 3 of 6 Group B platforms likely to confirm dofollow (hackmd,
  hashnode, gitlabpages have strong priors)
- Target: 5 → ≥13 (which we've already reached with browser evidence)
  Stretch: 5 → ≥17
