---
date: 2026-06-06
kind: discovery-pipeline
builds_on: docs/brainstorms/2026-06-06-dofollow-channel-expansion-requirements.md
status: active
---

# Discovery Pipeline — Repeatable Platform Sourcing Process

A systematic, repeatable process for sourcing new backlink channel candidates
beyond the current 28 registered platforms. Designed to feed the `channel-probe`
skill for structured triage.

## Overview

```
Source Directories  →  Dedup  →  Score & Rank  →  channel-probe  →  Queue
     |                    |            |               |
AlternativeTo       registered()  likelihood ×   GO / NO-GO /
Wikipedia lists     retired/      adapter cost ×   NEEDS-CANARY
Platform families   rejected      referral value
Niche communities   canary-tracker
```

## Step 1: Source Candidates (R6)

### Source A — Platform Directories

Run periodic batch queries against structured directory listings:

| Source | What to query | Method |
|---|---|---|
| AlternativeTo | "blog publishing platform", "free blogging platform", "write-only publishing" | Web search + scrape |
| G2 / Capterra | "Blogging Platform" category, free-tier filter | Web search |
| Wikipedia | "List of blog publishing platforms", "Comparison of blogging software" | Web fetch |
| Slant.co | "What are the best free blogging platforms" | Web search |
| Awesome lists | GitHub "awesome-blogging", "awesome-publishing" | Web search |

### Source B — Platform-Family Enumeration

When a platform is built on open-source software, the same software may power
other instances:

| Parent software | Known instances | Enumeration method |
|---|---|---|
| WriteFreely | write.as, prose.sh, writefree.ly, ~100 other instances | Search `powered by WriteFreely`, federation directories |
| Plume | plume.example instances | Plume federation API |
| Ghost | ghost.org blogs | Search `powered by Ghost`, Ghost(Pro) directory |
| WordPress | wordpress.com, thousands of .org installs | Search community-managed lists |
| Hugo / Jekyll / 11ty | static site generators → ghpages/gitlabpages/cloudflare pages | GitHub search for publish workflows |

### Source C — Niche/Topical Communities

Platforms that serve specific communities and may offer dofollow profiles:

| Community type | Examples |
|---|---|
| Developer platforms | dev.to, hashnode, medium (tech), hackmd |
| Knowledge bases | gitbook, notion, wikis |
| Writing communities | substack, medium (general), wattpad, inkitt |
| Academic/research | academia.edu, researchgate |
| Niche social | mastodon instances, lemmy instances |

### Source D — Canary-Proximity Candidates

When a platform is confirmed dofollow, check if the same team/company operates
other publishing surfaces:

- Medium → Substack comparison leads to other newsletter platforms
- Telegraph → sibling services (telegra.ph, Graph API)
- Blogger → Google Sites, Google Docs publishing

## Step 2: Dedup (R7)

Each candidate must survive a 4-way dedup check:

```
candidate → is it in registered_platforms()?
         → is it in docs/notes/retired-platforms/? 
         → is it in _REJECTED_PLATFORMS in src/backlink_publisher/publishing/registry.py?
         → is it in docs/discovery/canary-blitz-verdicts.md or canary-pending.md?
```

If yes to **any**, skip the candidate (with a one-line note in the batch output).

Decision records for rejected platforms live in `docs/notes/retired-platforms/`.
Each file covers: why rejected, reopen conditions, date of decision.

## Step 3: Score & Rank (R8)

Surviving candidates are scored on 5 axes, each 1-5 (5 = best):

| Axis | What to evaluate | 1 (worst) | 5 (best) |
|---|---|---|---|
| Dofollow likelihood | Preliminary HTTP `rel` check on a public post | Explicit `rel=nofollow` on all outbound links | `rel=dofollow` or absent `rel` (default dofollow) |
| Indexation potential | `site:` search results count | 0 indexed pages or blocked by robots.txt | 10k+ indexed pages, fresh content |
| Adapter complexity | Estimated engineering effort to build adapter | Requires headed browser + complex auth + rate limiting | Anonymous form-post or simple token API |
| Referral value | Domain Authority / traffic estimate | DA < 10, no referral traffic | DA 60+, active referral traffic |
| Bad-neighborhood risk | Spam / malware / porn adjacency | Known spam hoster, low-quality content farm | Clean editorial content, moderation |

Total score = sum of 5 axes (max 25). Rank descending.

## Step 4: channel-probe Triage (R9)

Top-ranked candidates (target: >=10 per discovery batch) are fed through the
`channel-probe` skill for full triage:

```
channel-probe triage:
  1. HTTP user-agent reachability matrix (preflight-bot / Googlebot / browser UA)
  2. site: index check via search
  3. Real-browser backlink-surface inspection:
     - Fetch a real post
     - Run verify_link_attributes / inspect_target_anchor
     - Check rel on outbound <a href> links
  4. Map result to dofollow/referral_value taxonomy
  5. Emit GO / NO-GO / NEEDS-CANARY verdict
```

## Step 5: Record Results (R10)

### GO candidates
- Enter queue for adapter development (separate planning milestone)
- No auto-registration — operator decides priority and timing

### NO-GO candidates
- Record in `docs/notes/retired-platforms/` with:
  - Platform name and URL
  - Date evaluated
  - Reason (nofollow, noindex, paywall, bad neighborhood, etc.)
  - Reopen conditions (what would change the verdict)

### NEEDS-CANARY candidates
- Register as `dofollow="uncertain"` with a `rationale=` argument
- Add to `docs/discovery/canary-pending.md` with a deadline
- Schedule for a pipeline canary publish

## Cadence

- **Initial batch:** One comprehensive batch to build the initial queue
- **Ongoing:** Monthly re-check (set calendar reminder)
- **Trigger:** A new platform-family launch or operator discovery of a promising
  platform triggers an ad-hoc mini-batch

## Output Artifacts

| Artifact | Purpose | Location |
|---|---|---|
| Discovery run log | Raw batch results, rankings, dedup decisions | `docs/discovery/<date>-run.md` |
| Canary pending tracker | Platforms awaiting pipeline canary | `docs/discovery/canary-pending.md` |
| Canary blitz verdicts | Results of our-pipeline canaries | `docs/discovery/canary-blitz-verdicts.md` |
| Retired platform ledger | Permanent NO-GO records | `docs/notes/retired-platforms/` |

---

## Batch 1 Results (2026-06-06)

First discovery batch execution. Sources consulted: AlternativeTo, Wikipedia
"List of blog publishing platforms", SEO blog recommendations, niche community
platforms.

### Candidates Found

| # | Candidate | Domain | How found | Status |
|---|---|---|---|---|---|
| 1 | Over-blog | over-blog.com | SEO blog recommendations | ✅ Dofollow (needs browser adapter) |
| 2 | Unmarkdown | unmarkdown.com | AlternativeTo alt to write.as | ✅ GO — REST API adapter feasible |
| 3 | JustPaste.it | justpaste.it | AlternativeTo (free pastebin alternative) | ❌ NO-GO (plain text URLs, no backlink surface) |
| 4 | LarkPen | larkpen.com | AlternativeTo (free writing platform) | ❌ NO-GO (no indexed user content) |
| 5 | MDBin | mdbin.sivaramp.com | Pastebin/free hosting lists | ⏱️ Deferred |
| 6 | Wattpad | wattpad.com | Wikipedia "List of blog publishing platforms" | ❌ Nofollow external links |
| 7 | Vocal.media | vocal.media | SEO blog lists | ⏱️ Needs more investigation |
| 8 | HubPages | hubpages.com | SEO blog lists | ❌ HTTP 403 (blocked) |
| 9 | Issuu | issuu.com | Wikipedia writing/publishing lists | ⏱️ Deferred |
| 10 | WriteFreely instances | writefreely.debian.social | WriteFreely federation | ❌ DNS failure on test instance |

### Dedup Results

All 10 candidates survived dedup:
- None are in `registered_platforms()`
- None match retired/rejected platforms in `docs/notes/retired-platforms/`
  or `_REJECTED_PLATFORMS`
- None appear in canary tracking docs

### HTTP Probe Results

Ran `scripts/channel_probe.py` on reachable homepages:

| Candidate | Verdict | Notes |
|---|---|---|
| over-blog.com | needs-canary | 200 all UAs, no login wall, no Cloudflare |
| justpaste.it | needs-canary | 200 all UAs |
| larkpen.com | needs-canary | 200 all UAs |
| mdbin.sivaramp.com | needs-canary | 200 all UAs |
| unmarkdown.com | needs-canary | 200 all UAs |
| wattpad.com | needs-canary | 200 all UAs |
| vocal.media | needs-canary | 200 all UAs |
| hubpages.com | no-go-unreachable | 403 all UAs |
| issuu.com | needs-canary | 200 all UAs |
| writefreely.debian.social | no-go-unreachable | DNS failure |

### Browser-Tier Rel Inspection

Performed Playwright/requests+BeautifulSoup inspection of existing posts/pages and live API tests:

| Candidate | External link rel observed | Verdict |
|---|---|---|
| Over-blog | ✅ **Dofollow** — Homepage links to user blogs have empty rel. User blog post external links have empty rel or `rel=noreferrer noopener` (security-only). One nofollow found but it was manually authored by blogger (sponsored content, not auto-injected). Over-blog does **not** auto-inject nofollow. | ✅ Dofollow confirmed |
| Unmarkdown | ✅ **Dofollow — confirmed via live API test.** REST API `POST /v1/demo/publish` works without auth (rate-limited, no email auth needed). Markdown links rendered as clean `<a>` tags with `rel=""` (empty). Badge link has `rel=noopener noreferrer` (no nofollow). | ✅ Dofollow confirmed |
| JustPaste.it | ❌ **NO-GO.** Playwright confirmed URLs render as plain text, NOT clickable `<a>` tags. No backlink surface exists. | ❌ No-go |
| LarkPen | ❌ **NO-GO.** Only homepage indexed. No user content discoverable. Cannot verify dofollow. Insufficient SEO value. | ❌ No-go |
| Wattpad | All external links: `rel=nofollow noreferrer` | ❌ Low dofollow potential |
| Vocal.media | Mixed: some links have no rel, some have `nofollow` | ⚠️ Needs more investigation |

### Scoring (All Candidates)

| Candidate | Dofollow (1-5) | Indexation (1-5) | Adapter (1-5) | Referral (1-5) | Risk (1-5) | Total | Verdict |
|---|---|---|---|---|---|---|---|
| Unmarkdown | 5 | 3 | 5 | 2 | 4 | **19** | ✅ GO |
| Over-blog | 5 | 5 | 2 | 4 | 4 | **20** | ⏱️ PENDING (needs browser adapter) |
| Vocal.media | 2 | 4 | 3 | 3 | 3 | **15** | ⏱️ Deferred |
| Issuu | 2 | 5 | 2 | 4 | 3 | **16** | ⏱️ Deferred |
| MDBin | 2 | 2 | 4 | 1 | 3 | **12** | ⏱️ Deferred |
| JustPaste.it | 1 | 4 | 4 | 2 | 4 | **15** | ❌ NO-GO |
| LarkPen | 1 | 1 | 3 | 1 | 3 | **9** | ❌ NO-GO |

### Recommendations

| Priority | Candidate | Recommended action |
|---|---|---|
| **P0** | **Unmarkdown** | **Build adapter (REST API, confirmed dofollow).** Simple `POST /v1/demo/publish` with Markdown body. Register as `dofollow=True, visibility="experimental"`. |
| P1 | Over-blog | High DA (~87), dofollow confirmed, but needs browser adapter (no write API). Longer-term investment. |
| P2 | Vocal.media | Needs focused browser inspection on a real article with embedded external links. |
| P3 | Others | Deferred to next batch cycle. |

### Next Batch

- Try AlternativeTo "free blogging platform" and "pastebin" categories more thoroughly
- Search Wikipedia "List of social platforms" for writing-focused niche sites
- Check Ghost(Pro) directory for free-tier instances
- Investigate WriteFreely federation for active, maintained instances
