# Bloglovin — Retired Platform

**Decision date:** 2026-05-25  
**Status:** NO-GO — retired, do not implement

## Evidence

- Rebranded to **Activate** in 2018; original blog-aggregation product abandoned.
- Service went dark in December 2021; blog-post discovery and reader features shut down.
- `https://www.bloglovin.com/` returns Cloudflare 403 as of 2026-05-25 probe.
- No blog-post publish API was ever exposed publicly.

## Decision

Bloglovin is no longer a viable backlink channel. No adapter will be implemented.
Operator spike findings: `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`

## If reconsidered

Require: live homepage returning 200, documented publish API, and a successful
pipeline canary before re-opening.

## Re-confirmation — 2026-05-29 (hands-on probe)

Re-examined under a "Tier-2 syndication" framing (claim a Blogger blog → let
bloglovin auto-mirror its RSS). Probe re-confirmed NO-GO; all three reopen
criteria still fail:

- **HTTP fetch:** `https://www.bloglovin.com/` and `/@user/<slug>` mirror pages
  return **403 to every UA** (preflight bot, Googlebot UA, and desktop-browser
  UA) — Cloudflare JS challenge, not mere UA filtering. The pipeline's
  `link_attr_verifier` preflight fetch cannot reach a verdict.
- **Real browser:** loads (passes Cloudflare) but the homepage is a **login
  wall**; `/@user/<slug>` post pages **redirect to `/login`** and render no
  article body (body text ~52 chars).
- **No backlink surface:** on a rendered mirror page the only outbound links are
  bloglovin's own socials (facebook/twitter/instagram/pinterest/tiktok). **Zero
  links to the source blog, the original post, or any target** — so even
  "second-order" link value is nil.
- **Indexation:** `site:bloglovin.com` surfaces only stale structural pages
  (home, about, tos, signup), no fresh dated mirror pages. Login-gated +
  bot-403 content cannot be crawled.
- **No publish API** (unchanged since 2026-05-25).

Conclusion: the blog-aggregation/reader product is an abandoned login-gated
shell with no public, linkable, indexable surface. Do not implement. Brainstorm
artifact: `docs/brainstorms/2026-05-29-bloglovin-syndication-tier-requirements.md`.
