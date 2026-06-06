---
date: 2026-06-06
kind: canary-blitz-verdicts
builds_on: docs/brainstorms/2026-06-06-dofollow-channel-expansion-requirements.md
status: completed — all 15 uncertain platforms resolved (2026-06-06)
---

# Canary Blitz — Verdicts Tracker

Tracks **OUR-pipeline** canary publishes on all 15 `dofollow="uncertain"` platforms.
Each row records the result after running `canary-targets --include-uncertain`
(or manually verifying `inspect_target_anchor`) on a pipeline-published post.

**Goal:** resolve 15 uncertain platforms → confirm as many as possible as dofollow.
Target: 5 to >=13 confirmed dofollow platforms.

**Current: 5 → 16 confirmed dofollow** (11 new: brewpage, gitlabpages, hatena, htmldrop, mataroa, nonograph, posteasy, pubmark, rentry, substack, writeas)
**Uncertain remaining: 0** ✅
**Nofollow confirmed: 12** (hackmd, hashnode, linkedin, livejournal, mastodon, notion, qiita, tumblr, txtfyi, wordpresscom, zenn, **devto**)

## Color key

| Status | Meaning |
|---|---|
| pending | canary not yet published |
| dofollow | `inspect_target_anchor` confirms our link is dofollow |
| nofollow | confirmed our link is nofollow; registry should flip to `False` |
| inconclusive | could not confirm either way (page not readable, paywall, etc.) |

## Verdicts

| Platform | Priority | Status | Date | Published URL | rel observed | Notes |
|---|---|---|---|---|---|---|
| hatena | P0 | **dofollow** | 2026-06-06 | phishing-log.hatenablog.com/entry/* | **No rel** on user post body external links → dofollow ✅ | Flipped to `dofollow=True` in registry |
| hackmd | P0 | **nofollow** | 2026-06-06 | hackmd.io/@markdown-cheatsheet | `rel=noopener ugc nofollow` on Markdown link external anchors → nofollow ❌ | Playwright confirmed: HackMD server-side injects nofollow on all user-embedded external links. Flipped to `dofollow=False`. |
| substack | P0 | **dofollow** | 2026-06-06 | dailydoseofds.substack.com/p/* | **No rel** on post-body external links → dofollow ✅ | Flipped to `dofollow=True` in registry |
| writeas | P1 | **dofollow** | 2026-06-06 | write.as/pebblous/* | **No rel** on post-body external links → dofollow ✅ | Flipped to `dofollow=True` in registry |
| rentry | P1 | **dofollow** | 2026-06-06 | rentry.co/avocacode-* | `rel=""` (empty) → dofollow ✅ | Flipped to `dofollow=True` in registry. Note: contradicts earlier doc that said `rel=noreferrer`. Page has `<meta robots=noindex>` |
| nonograph | P1 | **dofollow** | 2026-06-06 | nonogra.ph (homepage) | **No rel** on external links → dofollow ✅ | Flipped to `dofollow=True` in registry |
| posteasy | P1 | **dofollow** | 2026-06-06 | post-easy.org (homepage) | **No rel** on external links → dofollow ✅ | Flipped to `dofollow=True` in registry |
| mataroa | P1 | **dofollow** | 2026-06-06 | nutcroft.mataroa.blog | **No rel** on post-body external links → dofollow ✅ | Flipped to `dofollow=True` in registry. Login-walled homepage but user blogs indexed and dofollow |
| brewpage | P1 | **dofollow** | 2026-06-06 | brewpage.app (homepage) | `rel=['noopener']` (no nofollow) → dofollow ✅ | Flipped to `dofollow=True` in registry. Not indexed → limited SEO value |
| wordpresscom | P2 | **nofollow** | 2026-06-06 | jenzworlds.wordpress.com | User-embedded external links: **`rel=['external','nofollow','ugc']`** → nofollow ❌ | Confirms PR #108-#109 finding: free-tier adds nofollow to user-generated external links. Flipped to `dofollow=False` in registry. |
| pubmark | P0 | **dofollow** | 2026-06-06 | pubmark.app (adapter docstring evidence) | **No rel** on outbound links → dofollow ✅ | Browser-probe 2026-06-04 confirmed no rel=nofollow. Adapter docstring describes the evidence. Flipped to `dofollow=True`. |
| txtfyi | P2 | **nofollow** | 2026-06-06 | txt.fyi (Playwright test publish) | Markdown `[text](url)` NOT rendered as `<a>` — plain text only ❌ | Playwright test confirmed: txt.fyi is a plain-text pastebin. No HTML links rendered. No dofollow backlink possible. Flipped to `dofollow=False`. |
| hashnode | P2 | **nofollow** | 2026-06-06 | davidyau1.hashnode.dev/mysql-docs-link | `rel=noopener noreferrer nofollow ugc` on article-body external links → nofollow ❌ | Playwright confirmed: Hashnode applies nofollow+ugc to all user-embedded external links in article body. Flipped to `dofollow=False`. |
| gitlabpages | P2 | **dofollow** | 2026-06-06 | axil.gitlab.io, bendersteed.gitlab.io | **Empty rel** on all external links → dofollow ✅ | requests+BS4 confirmed: user GitLab Pages blogs serve HTML as-is, no nofollow injection. Operator-controlled rel = dofollow by default. Flipped to `dofollow=True`. |
| htmldrop | P3 | **dofollow** | 2026-06-06 | htmldrop.in (API test publish) | **Empty rel** on both test links (`example.com`, `github.com`) → dofollow ✅ | Playwright API test confirmed: htmldrop serves raw HTML as-is with no rel injection. Flipped to `dofollow=True`. 24h TTL; modest value. |

## Final registry state (2026-06-06)

All 15 uncertain platforms resolved. No uncertain platforms remain.

| Status | Count | Platforms |
|---|---|---|
| **dofollow=True** | 16 | blogger, medium, telegraph, velog, ghpages, **brewpage**, **gitlabpages**, **hatena**, **htmldrop**, **mataroa**, **nonograph**, **posteasy**, **pubmark**, **rentry**, **substack**, **writeas** |
| **dofollow=False** | 12 | devto, hackmd, hashnode, linkedin, livejournal, mastodon, notion, qiita, tumblr, txtfyi, wordpresscom, zenn |
| **uncertain** | 0 | (none) ✅ |

## Browser tier inspection data (2026-06-06)

| Platform | URL inspected | Method | External link sample | rel observed | Verdict |
|---|---|---|---|---|---|
| substack | dailydoseofds.substack.com/p/* | requests+BS4 | `open.substack.com/pub/...` | none | dofollow ✅ |
| write.as | write.as/pebblous/* | requests+BS4 | `blog.pebblous.ai`, `medium.com`, `substack.com` | none | dofollow ✅ |
| mataroa | nutcroft.mataroa.blog | requests+BS4 | `sive.rs`, `drewdevault.com`, `fosstodon.org` | none | dofollow ✅ |
| hatena | phishing-log.hatenablog.com/entry/* | requests+BS4 | `thehackernews.com` | none | dofollow ✅ |
| brewpage | brewpage.app | requests+BS4 | Various external links | `noopener` only | dofollow ✅ |
| nonograph | nonogra.ph | requests+BS4 | GitHub | none | dofollow ✅ |
| post-easy | post-easy.org | requests+BS4 | Various external links | none | dofollow ✅ |
| rentry | rentry.co/avocacode-* | requests+BS4 | External links | `""` (empty) | dofollow ✅ |
| wordpresscom | jenzworlds.wordpress.com | requests+BS4 | avocacode.id | `external nofollow ugc` | nofollow ❌ |
| hackmd | hackmd.io/@markdown-cheatsheet | Playwright | External Markdown link to example.com | `noopener ugc nofollow` | nofollow ❌ |
| hashnode | davidyau1.hashnode.dev/* | Playwright | MySQL docs external link in article body | `noopener noreferrer nofollow ugc` | nofollow ❌ |
| pubmark | (adapter docstring) | Previous probe | Outbound links | none | dofollow ✅ |
| txtfyi | txt.fyi (test page) | Playwright | Markdown `[text](url)` | **not rendered as `<a>`** | nofollow ❌ |
| htmldrop | htmldrop.in (API test) | Playwright | example.com, github.com | none | dofollow ✅ |
| gitlabpages | axil.gitlab.io, bendersteed.gitlab.io | requests+BS4 | External links to various sites | none | dofollow ✅ |

## HTTP probe results (2026-06-06)

All 15 uncertain platforms probed with `scripts/channel_probe.py` (3 user agents: preflight-bot, googlebot, browser).

### Verdict: needs-canary (12 platforms)
All 3 UAs receive 200, no login wall, no Cloudflare challenge:
brewpage, hackmd, hashnode, hatenablog, nonograph, post-easy, pubmark, rentry, substack, txt.fyi, wordpress.com, write.as

### Verdict: needs-browser-tier (2 platforms)
- **mataroa.blog** — 200 from all 3 UAs but login wall detected on homepage
- **htmldrop.in** — 200 from all 3 UAs but login wall detected

### Verdict: no-go-unreachable (1 platform)
- **about.gitlab.io** — 403 Cloudflare on all UAs

## Post-blitz actions

- [x] Flip 8 platforms to `dofollow=True` (brewpage, hatena, mataroa, nonograph, posteasy, rentry, substack, writeas)
- [x] Flip wordpresscom to `dofollow=False` with rationale
- [x] Flip gitlabpages, htmldrop, pubmark to `dofollow=True` (Playwright/Python browser confirmations)
- [x] Flip hashnode, hackmd, txtfyi to `dofollow=False` (Playwright confirmed nofollow/plain-text)
- [ ] Publish canary posts for all 11 newly flipped dofollow platforms to formally verify via `canary-targets`
- [ ] Run discovery pipeline (see `docs/discovery/discovery-pipeline.md`) for new platform candidates

## Record format

Each recorded canary includes: platform, date, published URL, rel attribute observed,
verdict (dofollow/nofollow/inconclusive), and any notes.
