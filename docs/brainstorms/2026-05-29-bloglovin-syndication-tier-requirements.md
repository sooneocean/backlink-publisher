---
date: 2026-05-29
topic: bloglovin-syndication-tier
---

# Bloglovin Syndication Tier

> ## ⛔ NO-GO — re-confirmed by hands-on probe 2026-05-29
>
> **Verdict: do not build.** A live probe on 2026-05-29 re-confirmed the standing
> retirement (`docs/notes/retired-platforms/bloglovin.md`, 2026-05-25; reaffirmed
> in `docs/brainstorms/2026-05-28-livejournal-prod-enablement-bloglovin-retire-requirements.md`).
> All three reopen criteria fail; see the re-confirmation block in the retire
> note for evidence. The decisive finding: rendered mirror pages contain **zero
> outbound links to the source blog or target** (only bloglovin's own socials)
> and are **login-gated + 403 to all bots** — there is no public, linkable,
> indexable backlink surface to integrate with. This document is retained as the
> record of why the Tier-2 reframing was examined and rejected.
>
> Two independent blockers, both confirmed against the repo and the live site,
> invalidate the premise:
>
> 1. **Cloudflare 403 to bots** (`docs/runbooks/2026-05-25-dofollow-canary-closeout.md`):
>    `bloglovin.com` returns 403 to bot user-agents. The recurring verifier
>    (R4–R7) reuses `link_attr_verifier`'s preflight bot-UA fetch, which would
>    receive 403 forever — the verifier can never reach a verdict. And if bots
>    are 403'd wholesale, search engines likely can't index the mirror pages
>    either, so the SEO value the whole feature chases may not exist.
> 2. **The aggregation product is dead.** The retire note records "blog-post
>    discovery and reader features shut down" (Dec 2021). The Tier-2 model
>    depends on claim→auto-pull→fresh-mirror-page; if that product is gone, the
>    `@user` mirror pages found during research are likely pre-2021 legacy
>    artifacts, not newly generated surfaces.
>
> **Reopen criteria** (from the retire note, none currently met): live homepage
> returning **200**, a documented **publish API**, and a successful **pipeline
> canary**. Do not proceed to `/ce:plan` until these are resolved — see
> *Outstanding Questions → Resolve Before Planning*.

## Problem Frame

We want backlink value from `bloglovin.com` (high legacy DR). But Bloglovin
does **not** match the contract every existing adapter implements
(`publish(payload, mode, config) -> AdapterResult` returning a `published_url`).
Bloglovin is an **RSS aggregator**, not an authoring platform: you *claim* an
existing blog that exposes a valid RSS 2.0 feed, and Bloglovin then auto-pulls
new posts into mirror pages under `bloglovin.com/@user/...` plus a profile page.
There is no public API; the platform is semi-abandoned (rebranded to "Activate"
~2018/2021, last major update ~2021, mobile app relaunched 2024) but the
consumer site still serves and indexes profile/mirror pages.

Because the pipeline already publishes to several RSS-bearing blogs, Bloglovin
is best modeled as a **Tier-2 syndication layer** sitting on top of one source
blog the pipeline already maintains — **Blogger** (clean full-content RSS,
native `dofollow=True`, fast Google indexing). Claiming the Blogger blog once
makes every future pipeline post automatically gain extra Bloglovin backlink
surfaces, at zero per-article cost. The open risk — and the reason code is
worth writing — is that we do not yet know whether Bloglovin mirror links carry
real SEO value (dofollow vs nofollow, direct target anchor vs second-order link
to the canonical post). A recurring verifier closes that gap.

## User Flow

```
ONE-TIME SETUP (runbook; operator-driven, ~minutes)
┌────────────────────────────────────────────────────────────┐
│ 1. Operator: on bloglovin.com, search the Blogger blog URL,  │
│    click "Claim" → Bloglovin shows an <a href...> snippet.   │
│ 2. Pipeline: publish a post containing that snippet to the   │
│    Blogger blog (REUSE existing BloggerAPIAdapter).          │
│ 3. Operator: back on Bloglovin, click "Claim Blog" →         │
│    Bloglovin verifies the snippet is live → blog claimed.    │
│ 4. Bloglovin begins auto-pulling the Blogger RSS feed.       │
└────────────────────────────────────────────────────────────┘

STEADY STATE (automatic, per Blogger post)
   Blogger post (carries target backlink)
        │  RSS 2.0 feed
        ▼
   bloglovin.com/@you/<slug>   ← mirror page  ─┐
   bloglovin.com/@you          ← profile      ─┤ extra backlink surfaces
                                                │
   RECURRING VERIFIER (code; reuse canary infra)│
   ┌─────────────────────────────────────────── ┘
   │ • profile page live + links to site?
   │ • recent Blogger posts present as mirror pages? (lag-aware)
   │ • mirror link rel attribute → dofollow / nofollow?
   │     - if mirror renders full content: is the target anchor
   │       itself present + dofollow on the mirror page? (direct value)
   │     - else: is the read-more link to the canonical Blogger
   │       post dofollow? (second-order value)
   └─→ feed the verdict back into the dofollow / referral_value taxonomy
```

## Requirements

**One-Time Claim (runbook)**
- R1. Provide a setup runbook (`docs/runbooks/`) covering the full claim flow:
  pick the Blogger source blog → publish the Bloglovin verification snippet via
  the existing `BloggerAPIAdapter` → complete the Bloglovin UI claim → confirm
  the RSS feed begins syncing.
- R2. The verification-snippet publish step reuses the existing Blogger adapter
  path; no new authoring code is written for Bloglovin.
- R3. The runbook is written to be repeatable for additional source blogs later
  (WordPress.com, Tumblr, …) without rework, even though only Blogger is in
  scope now.

**Recurring Verifier**
- R4. A recurring, read-only, advisory verifier (mirroring the `canary-targets`
  contract: JSONL receipts on stdout, recon summary on stderr, always exit 0)
  checks that the claimed Bloglovin profile is live and links to the configured
  site.
- R5. The verifier checks that recent Blogger posts have appeared as Bloglovin
  mirror pages, accounting for RSS-pull lag (a missing very-recent post is not
  yet a failure).
- R6. The verifier classifies the link value of each mirror page using the
  existing `link_attr_verifier` helpers (`inspect_target_anchor`,
  `verify_link_attributes`, interstitial unwrapping): if the mirror renders the
  full post, verify the **target anchor** is present + dofollow on the mirror
  page (direct backlink value); otherwise verify the **read-more / canonical
  link** to the original Blogger post is dofollow (second-order value).
- R7. The verifier's verdict is the evidence an operator uses to flip Bloglovin's
  declared `dofollow` status from `"uncertain"` to a settled value, following
  the project's existing canary-driven workflow.

**Channel Representation & Reporting**
- R8. Bloglovin is represented as a **syndication-tier channel** so it surfaces
  in reporting/dashboard (tied to its Blogger source), but it MUST NOT become a
  per-row publish dispatch target — `publish-backlinks --platform bloglovin`
  must never be a valid invocation, since Bloglovin cannot accept authored rows.
- R9. Bloglovin's declared link metadata starts as `dofollow="uncertain"` with a
  rationale (≥80 chars) and a `referral_value` classification, satisfying the
  registry's nofollow/uncertain gate, and is updated from R7 evidence.

## Success Criteria
- After the one-time claim, new Blogger posts appear as Bloglovin mirror pages
  with no per-article operator action.
- The verifier produces a clear, repeatable verdict on whether Bloglovin mirror
  links carry dofollow (direct or second-order) value — turning today's "unknown
  SEO value" into a settled, evidence-backed `dofollow` declaration.
- Bloglovin shows up in the channel dashboard as a syndication tier without ever
  being dispatchable as a publish target.
- Adding a second source blog later requires only re-running the runbook, no
  code changes.

## Scope Boundaries
- **No per-row publishing to Bloglovin.** It is not an adapter in the
  `publish()` sense.
- **No browser automation of the Bloglovin claim/verify clicks.** The claim is a
  one-time, operator-driven UI action; automating a semi-abandoned site's UI is
  high-maintenance, low-ROI.
- **Only Blogger as the source blog for now.** Multi-source claiming is a
  documented future extension, not built.
- **Not building a Bloglovin profile-link-only play** (rejected: single
  nofollow-likely profile link is too little value to justify integration).
- No attempt to revive/depend on the defunct Activate influencer API.

## Key Decisions
- **Tier-2 syndication model over per-row adapter**: Bloglovin structurally
  cannot accept authored articles; forcing it into the adapter contract is the
  wrong abstraction.
- **Blogger as first source**: strongest native dofollow + cleanest full-content
  RSS + fastest indexing among the pipeline's RSS-bearing channels.
- **Runbook claim + automated verifier**: the claim is one-time (cheap as a
  runbook, snippet-publish reuses the Blogger adapter); the verifier is the
  recurring, load-bearing piece because Bloglovin's link value is genuinely
  uncertain and verification is the project's core discipline.
- **Reuse `canary-targets` + `link_attr_verifier` infra** rather than inventing a
  new verification stack.

## Dependencies / Assumptions
- Assumes the pipeline already publishes to (or can publish to) a Blogger blog
  whose full-content RSS feed is enabled and reachable.
- Assumes `bloglovin.com` still serves and indexes profile + mirror pages in
  2026 (verified live during research; mirror pages like
  `bloglovin.com/@user/<slug>` currently resolve).
- Reuses: `BloggerAPIAdapter`, the `link_attr_verifier` helpers, the
  receipt/recon/exit-0 *shape* of `canary-targets` (NOT its cohort/dispatch —
  see Outstanding Questions), and the registry's `dofollow`/`referral_value`/
  `visibility`/`policy` declaration system. **Correction:** the
  `feat/channel-tier-grouping` branch groups by credential `auth_type` and reads
  `active_platforms()` — it is *not* a ready home for a syndication tier; that
  surface is unbuilt.

## Outstanding Questions

### Resolve Before Planning
These are blockers, not refinements — each can independently kill the feature.

- [Affects whole doc][User decision] **Does the standing NO-GO get overturned?**
  bloglovin was retired 2026-05-25 with explicit reopen criteria. Proceeding
  requires the operator to either meet those criteria or consciously waive them.
- [Affects whole doc][Needs research] **Live reachability for bots.** Probe
  whether `bloglovin.com` profile/mirror pages return **200** (not Cloudflare
  403) to the verifier's preflight bot UA (`backlink-publisher/0.1 preflight-targets`).
  If 403, the reused verifier path is infeasible and (worse) search-engine
  indexing of mirror pages is in doubt — which removes the feature's entire
  reason to exist.
- [Affects whole doc][Needs research] **Does claim→auto-pull still function?**
  The reader/aggregation product shut down Dec 2021. Confirm a freshly claimed
  blog still generates new mirror pages today, not just legacy pre-2021 ones.
  If auto-pull is dead, no amount of code produces new backlink surfaces.

### Deferred to Planning
- [Affects R6][Needs research] Does Bloglovin render **full RSS content** on
  mirror pages (so the in-content target anchor appears directly), or only an
  **excerpt + read-more** link to the canonical post? Determines whether the
  backlink is direct or second-order. Verify hands-on against a real mirror page.
- [Affects R6/R7][Needs research] What `rel` does Bloglovin put on (a) the
  mirror→canonical link and (b) any in-content links — dofollow, nofollow, or a
  redirect/interstitial wrapper that `_unwrap_interstitial` must handle?
- [Affects R4][Technical] `canary-targets` cannot be reused as-is: its
  `_build_cohort()` includes only `dofollow_status(p) is True` platforms and
  raises on an empty cohort, so an `"uncertain"` (R9) or unregistered bloglovin
  can never enter it. Reuse is limited to the `link_attr_verifier` **helpers**
  and the receipt/recon/exit-0 contract **shape** — the verifier must be a new,
  separately-cohorted verb. (feasibility, P1)
- [Affects R8][Technical] How to represent a non-dispatchable syndication channel
  without it leaking into `registered_platforms()` → CLI argparse → schema enum
  → publish dispatch. **What validation rule or code guard prevents
  `--platform bloglovin` from ever being a valid publish invocation?** Options:
  a new `visibility`/`policy` kind, a separate syndication registry, or
  config-only representation. (coherence + feasibility)
- [Affects R8][Technical] The named `feat/channel-tier-grouping` branch is **not**
  a drop-in home: `group_channels_by_tier` buckets by credential `auth_type`
  (anon / fill-creds / browser-login) and reads `active_platforms()`. It has no
  "syndication" concept and would only show bloglovin if it were registered —
  the exact leak R8 forbids. A real syndication reporting surface is unscoped
  work. (feasibility, P1)
- [Affects R4/R5][Technical] The canary store and `[canary.<platform>]` config
  are keyed by **registered** platform name, which collides with R8's "do not
  register" constraint. A non-registered bloglovin needs either a separate
  syndication config namespace + health store, or a registry representation
  excluded from `registered_platforms()`. Resolve jointly with the R8 item.
  (feasibility, P1)
- [Affects R5][Technical] Define the RSS-lag tolerance window, the source that
  enumerates "recent Blogger posts" (Blogger API vs the pipeline's events/history
  store), and the verdict when that source is empty or the profile yields zero
  mirror pages. (feasibility, P2)
- [Affects R1][Technical] Exact Bloglovin claim-snippet handling: capture it
  during the manual step and feed it to the Blogger publish, vs. a fixed
  snippet — confirm Bloglovin's current verification mechanism during planning.

## Next Steps
→ **Blocked.** Resolve the *Resolve Before Planning* items first — chiefly the
operator decision on overturning the NO-GO and a hands-on probe confirming bots
get 200 (not 403) and claim→auto-pull still works. If those clear, resume
`/ce:brainstorm`; only then `/ce:plan`.
