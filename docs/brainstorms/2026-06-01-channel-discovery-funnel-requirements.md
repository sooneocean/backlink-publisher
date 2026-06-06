---
date: 2026-06-01
topic: channel-discovery-funnel
related_skill: .claude/skills/channel-probe/SKILL.md
related_script: scripts/channel_probe.py
builds_on: docs/brainstorms/2026-05-25-remaining-channel-viability-expansion-requirements.md
reviewed: 2026-06-01 (document-review — 6 personas; corrections applied inline)
---

# Channel Discovery Funnel — sourcing layer that feeds the probe

## Problem Frame

The operator wants **more dofollow backlink channels** wired into the publisher.
Today the evaluation tier is partial — the `channel-probe` skill takes a **known**
candidate and an agent drives an HTTP-UA matrix + `site:` index check + a real-browser
`<a> rel` inspection to a GO / NO-GO / NEEDS-CANARY judgement. But it only evaluates a
channel **you already named**, and candidate sourcing is entirely manual ("let's try
justpaste.it, teletype.in"). There is **no discovery layer** — nothing that *finds*
candidates and feeds them to the probe.

The naive reading ("find any site that gives dofollow") is a trap. Most free-publishing
sites hand out dofollow links that **Google never indexes** (spam farms, zero DA), so they
add footprint / bad-neighborhood risk with zero SEO equity. So the objective is narrower:

> Surface candidates ranked by **indexed-dofollow value × low adapter cost × not-a-bad-neighborhood**,
> deduped against everything already decided, as a read-only report the operator gates by hand.

The intended highest-leverage sourcing signal is **platform-family enumeration**: find more
*instances* of software we already have an adapter for. **But the document-review pass
falsified the "near-zero cost" version of this premise** (see ⚠️ below) — reuse is real
only after a one-time adapter-generalization task, and only for families whose links are
actually dofollow-and-indexed. The premise must be validated by a spike before it can anchor
the round.

> ⚠️ **Three load-bearing premises were checked against the code and do not hold as written.
> They are corrected inline and gate this round (see Resolve Before Planning):**
> 1. **"Reuse = near-zero cost" is false.** `writeas_api.py` hardcodes `https://write.as/api`,
>    the `X-Writeas-Token` header, `published_url = https://write.as/{slug}`, `platform="writeas"`,
>    and a single token path. `telegraph_api.py` is bound even tighter (`api.telegra.ph` + the
>    proprietary Node-JSON tree). A self-hosted WriteFreely instance / Telegraph clone needs a
>    real adapter-generalization refactor (parameterize base URL + per-instance credentials +
>    published-URL + platform name) before any reuse — and the registry keys one chain per
>    platform string with no "family" / multi-instance abstraction, so N instances = N
>    `register()` calls + N credential paths.
> 2. **The "decided-ledger" does not exist.** The prior round's R4 only *proposed* a location;
>    it was never built. `docs/notes/retired-platforms/` has one entry (bloglovin); the
>    CONDITIONAL-deferred verdicts (justpaste.it, teletype.in) live only as prose with no
>    queryable store. R8/R9 must *create* this store, not "reuse" it.
> 3. **The per-family saturation cap has no home.** `footprint.py` is a byte-level HTML
>    self-fingerprint regression gate — it has no concept of platform-family, no per-family
>    instance counter; the anchor scheduler picks anchor type, not family fan-out. R13's cap
>    is net-new work, and it is the control the round says makes family-enum safe.

## Discovery Funnel (conceptual flow)

```
SOURCES (pluggable; each candidate tagged with provenance)
  (1) family-enum   -- PRIMARY (after the reuse-spike validates it)
                       instances of software we already adapter, dofollow-confirmed only
                       (write.as -> self-hosted WriteFreely; telegraph -> clones)
  (2) SERP footprint   "powered by writefreely", inurl:write, "guest post"+niche
  (3) competitor backlinks   DEFERRED -- needs paid data (Ahrefs/Semrush/CommonCrawl)
  (4) public list / LLM   cheapest seed, lowest quality -- UNTRUSTED INPUT
        |
        v
VALIDATE + DEDUP
  every source emits only validated public-host URLs (SSRF gate, see R14)
  dedup against the decided-store (registry + retired-notes + backfilled deferred)
        |
        v
EVALUATE -- reuse the probe, but SSRF-harden it first (R14)
  HTTP-UA matrix (channel_probe.py, batchable) -> categorical signal only
  site: index check + browser <a rel> tier -> AGENT-DRIVEN, not a library call
  no GO verdict / no register() line comes from the script -- the agent decides
        |
        v
RANK (tiered, deterministic -- no invented numeric score; see R6)
  bucket: reuse-GO > new-adapter-GO > needs-canary > no-go
  within bucket: sort by the probe's categorical dofollow/indexed signals
        |
        v
OUTPUT -> docs/discovery/<date>-run.md   (READ-ONLY, human gate; sanitized, see R17)
  tiered table + per-candidate evidence + agent-drafted register()/retired-note
  operator go/no-go per row; new verdicts written back to the decided-store
```

## Requirements

**Sourcing (the new discovery layer)**
- R1. **Primary = throwaway-account high-DA blog families (quality over quantity).** The spike (below) killed the "cheap mass family-enum" thesis: anonymous outside posting only exists on hosted write.as, not on the self-hosted family, so real dofollow+DA blog families (WriteFreely / Ghost / self-hosted WordPress) gate posting behind a per-instance account. Decision: **target open-registration instances of these families and publish via a dedicated throwaway account per instance** (R11 relaxed — see below). This yields a **few high-DA channels**, not many cheap ones. Two build tasks are in scope: (a) **adapter generalization** — parameterize the `write.as` adapter's base URL, auth header (`Authorization: Token` for self-hosted vs the `X-Writeas-Token` write.as legacy), `published_url`, per-instance token path, and platform name; (b) per-instance throwaway-account bind. A family is admitted **only after its links are confirmed dofollow-and-indexed**; nofollow-by-default families (Fediverse/Mastodon) are excluded.
- R2. The other signals are added incrementally: (a) SERP footprint mining via `WebSearch` patterns; (b) competitor backlink mining (**deferred** — gated on a data-source decision); (c) public lists / LLM seeds (cheapest, lowest quality, **untrusted input** — see R15). Ship family-enum as a single concrete source this round; the formal pluggable-source module boundary is **deferred until a second source actually lands** (do not build an extension framework with one consumer).
- R3. Every candidate records its **provenance** (which source surfaced it) so per-source hit-rate (candidates → GO → live channel) is measurable and low-yield sources can be pruned. Provenance grants **traceability, not trust**.

**Evaluation (reuse the probe — but harden it)**
- R4. Each candidate runs through the `channel-probe` pipeline. **Only the HTTP-UA matrix (`channel_probe.py`) is batchable and deterministic; it emits a categorical signal, not a GO verdict and not a `register()` line.** The decisive `site:` index check and browser `<a> rel` tier are **agent-driven** — the discovery layer orchestrates per-candidate agent passes, it does not get an automated GO from a library call. The report's draft `register()`/retired-note is produced by the agent, not the script.
- R5. For JS-SPA candidates the probe's static-HTML regex returns `total_anchors=0` and a **false** dofollow signal (inherited caveat, prior round R15). SPA-family candidates must route through a headless-render pass before any `uncertain→dofollow=True` claim.

**Ranking**
- R6. Rank by a **deterministic tiered sort**, not a numeric composite: bucket candidates `reuse-adapter-GO > new-adapter-GO > needs-canary/uncertain > no-go`, then within each bucket sort by the probe's existing categorical dofollow/indexed signals. A weighted numeric score is **deferred until a first run produces a candidate corpus to calibrate against** — and until a real DA/indexation data source exists (today there is none; see Resolve Before Planning). The report must visibly separate "GO + reuse existing adapter" from "GO + needs a new heavy adapter."
- R7. Discovery reuses the **existing value gate** (viable AND NOT (`nofollow` AND low referral)). **Note:** the gate's DA/indexation tier is currently a grade *label* in `registry.py`, not a measured cutoff — no DA data source is wired — so "indexed-dofollow likelihood" degrades to a binary `site:` presence check until the cutoff is defined (Resolve Before Planning).

**Dedup / memory (build the store — it does not exist yet)**
- R8. Before probing, dedup each candidate against a **decided-store** that this round must **establish**: the registry (already queryable in code) + `docs/notes/retired-platforms/` (exists) + CONDITIONAL-deferred verdicts (currently prose-only — **backfill** justpaste.it / teletype.in as retired-notes entries with a `conditional` status flag as part of this round). An already-decided candidate is skipped (shown with its prior verdict), never re-probed.
- R9. Verdicts produced by a discovery run are written **back** to the decided-store so the next run inherits them. The store's physical form (live-derived vs a consolidated index file) is a **Resolve-Before-Planning** decision.

**Output / human gate**
- R10. Output is a tiered markdown report at `docs/discovery/<date>-run.md`: a candidate table (tier, candidate, family, probe signal, dofollow, indexed, adapter cost, recommendation) + per-candidate evidence (HTTP matrix, `site:` finding, browser rel evidence) + an agent-drafted `register()` line or `retired-platforms` note.
- R11. **Read-only DISCOVERY; controlled-account PUBLISH (relaxed 2026-06-01).** The discovery + probe tier stays strictly read-only (never logs in, no account, no ToS accept, no auto-register; every GO is operator-gated one row at a time). **But the chosen primary (R1) requires posting to account-gated blog families**, so the *publish* side is permitted to create a **dedicated throwaway account per instance**, following the existing livejournal / teletype / mastodon discipline: never the operator's primary identity, and all credentials/cookies/tokens written via `safe_write.atomic_write` at `0o600`. **Honest throughput + cost note:** GO requires a per-candidate agent-driven browser pass, and each converted instance now also costs one throwaway-account bind — so this is a *few-high-DA-channels* play, not a high-throughput one; the bottleneck is per-instance verify + bind, not sourcing.

**Safety / reputation**
- R12. Bad-neighborhood screen is a **first-class rejection axis** (spam farms, adult-ad neighborhoods — the `jkforum` precedent — PBN-shaped sites), flagged and demoted regardless of dofollow. A toxic-neighborhood backlink can actively *harm* the protected source blog (Google penalty), not merely waste effort. (No automated classifier exists today; whether R12 is a manual judgement or net-new code is a planning question.)
- R13. **Family-saturation is a footprint risk and its cap is net-new work.** Mass-publishing the same content + target link across N instances of one software family is a detectable footprint. **No per-family fan-out cap exists** (`footprint.py` governs HTML link-signature concentration, not instance counts). This round must either (a) build a per-family instance cap `N` and wire it into the publish path, or (b) state that discovery only **reports** family-saturation and per-family fan-out stays **unenforced** until that cap lands — consistent with the "newly registered channel is idle until quota is wired" boundary. The cap is the control that makes family-enum safe, so option (b) means GO candidates are surfaced but **not yet safely publishable at family scale**.

**Security / abuse surface (added by review)**
- R14. **SSRF guard before batch.** `channel_probe.py` is the repo's lone fetch path that does **not** route through `net_safety` (`_check_url_for_ssrf` / `_make_ssrf_opener`) — it uses raw `requests.get(..., allow_redirects=True)`. Before the funnel drives it on machine-sourced candidates, the probe's HTTP fetch **must** adopt the same SSRF guard the production pipeline uses: reject RFC1918 / loopback / link-local / cloud-metadata (169.254.169.254) resolutions and re-validate every redirect hop. The SPA headless-render path (R5) must enforce the same host gate.
- R15. **Untrusted source input.** SERP / public-list / LLM candidates are attacker-influenceable. Every source module emits only validated URLs (scheme ∈ {http,https}, parseable public host, passes the R14 gate) before entering the dedup/probe stream; no candidate is fetched on the strength of its provenance alone.
- R16. **Probing is itself ToS/abuse-exposed.** "Read-only" must include abuse discipline on the *probing* side: per-host rate limit + concurrency cap + a per-run fetch budget, and an explicit decision on robots.txt and the spoofed-Googlebot UA (which risks abuse complaints / egress-IP bans), not just "never log in."
- R17. **Report hygiene.** Discovery reports are sanitized before write — strip query strings / tokens from captured `final_url` and redirect chains (reuse `net_safety.safe_for_log`) — and `docs/discovery/` gets an explicit commit-vs-gitignore decision (it is currently **not** gitignored, unlike `docs/diagnostics/`). No credentials or internal hostnames persisted in the clear.

## Success Criteria
- A discovery run produces a **tiered, deduped** candidate report of open-registration high-DA blog-family instances; no candidate already in the decided-store is re-probed; new verdicts are written back.
- The generalized adapter publishes to **at least one** real non-`write.as` WriteFreely (or Ghost) instance via a throwaway account — proving the convert path end-to-end (plan → validate → publish → dofollow canary).
- Zero auto-registration; the discovery/probe tier is read-only on targets; the SSRF guard (R14) is in place before any batch; throwaway credentials stored `0o600`.
- Family-saturation is surfaced and the per-family cap `N` is set conservatively (R13a).
- **Intermediate-proxy honesty:** "converts to a live channel" means registered + publishable + dofollow-canary-confirmed, **not** that the operator's own posted link is indexed and passing equity — the project has no indexation/GA4 loop to measure that. A green scorecard here is not delivered SEO equity; per-link indexation verification is a named follow-on.

## Scope Boundaries
- **No auto-registration** (decided) — not even `experimental`.
- **No new probe logic** — reuse `channel_probe.py` + the `channel-probe` skill (but R14 SSRF-hardening of the existing fetch is in scope, not new probe logic).
- **Competitor backlink mining is deferred** (needs a data-source decision); ships with family-enum primary + SERP-footprint + public/LLM seed.
- **No publish quota/proportion wiring** for newly added channels — a newly registered channel is idle until quota is wired (prior round R16); see R13.
- Does not change the dofollow/referral taxonomy or the value-gate semantics — only notes that the DA tier is not yet a measured cutoff.

## Key Decisions
- Discovery layer = candidate **sourcing funnel** feeding the existing probe; the gap is sourcing, not evaluation.
- **Primary signal = throwaway-account high-DA blog families (quality over quantity).** The "cheap mass family-enum" thesis was falsified by spike (anonymous posting is write.as-hosted-only; the self-hosted family gates posting behind accounts). The round targets a *few* open-registration high-DA instances via a generalized adapter + per-instance throwaway account.
- **R11 relaxed:** discovery/probe stays read-only; publish may create per-instance throwaway accounts (existing livejournal/teletype discipline, `0o600`).
- **Convert path** (not report-only): adapter generalization + throwaway-account bind in scope, so a GO becomes a live channel.
- Ranking = **deterministic tiered sort** on a **binary `site:` indexed** signal (no DA data source yet; numeric DA cutoff deferred).
- The **decided-store must be built this round** (it does not exist); dedup against registry + retired-notes + backfilled CONDITIONAL-deferred verdicts.
- The probe must be **SSRF-hardened** (R14) before being driven on machine-sourced URLs.
- Output = tiered report + human go/no-go; auto-register rejected on auditability grounds.

## Dependencies / Assumptions
- Reuses the registry value gate, the dofollow/referral taxonomy, the `channel-probe` skill, and (for R17) `net_safety.safe_for_log`.
- **Does NOT yet have:** a generalized (multi-instance) adapter, a decided-store, a per-family fan-out cap, a DA/indexation data source, or a bad-neighborhood classifier — each is either spiked, built, or explicitly deferred by this round.
- Assumes a maintained public instance list exists for at least one seed family (WriteFreely / Fediverse directories), and that a non-trivial fraction of enumerated instances actually allow anonymous/token API posting without per-instance ToS acceptance (R11 forbids accepting ToS) — to be measured.

## Outstanding Questions

### Resolve Before Planning
- [Affects R1][Spike — DONE 2026-06-01] **Reuse premise FALSIFIED for the WriteFreely family.** Read-only spike result: (a) API generalizes — the adapter's `POST /api/posts` + `data.slug` IS the WriteFreely API and works on self-hosted instances; (b) but the adapter needs 3 changes to generalize (the `X-Writeas-Token` header is write.as-legacy — self-hosted WriteFreely uses `Authorization: Token <token>`; plus hardcoded base URL and published_url) — bounded refactor, not near-zero; (c) **the killer: self-hosted WriteFreely always requires a registered account — anonymous posting is a write.as-hosted-only feature.** Posting to a self-hosted instance needs an account on it (open-registration only, mostly disabled) + a per-instance token, which R11 (never create accounts) forbids and which destroys the "many instances cheap" leverage (each instance = one account + token + throwaway identity). Net: the families that allow anonymous outside posting (telegraph/paste-style) are the low-DA/zero-equity ones; the families with real dofollow blogs (WriteFreely/Ghost/WordPress) gate posting behind per-instance accounts. **Family-enum is not the cheap backbone it was assumed to be — the primary signal must be re-chosen (see below).**
- [Affects R1][User decision — RESOLVED 2026-06-01] **Primary signal = throwaway-account high-DA blog families** (quality over quantity); see R1.
- [Affects R1/R13][Scope — RESOLVED 2026-06-01] **Convert path.** Adapter generalization + per-instance throwaway-account bind are in scope so a GO becomes a live, publishable channel. Because the play is *few high-DA instances*, the per-family cap `N` is naturally small; discovery reports family-saturation and `N` is set conservatively in planning (R13a, not the deferred-enforcement R13b).
- [Affects R6/R7][User decision — RESOLVED 2026-06-01] **MVP accepts binary `site:` indexation as the ranking proxy.** No DA data source exists yet; a numeric DA cutoff is deferred until a DA source lands (could ride on the deferred competitor-mining data source). The tiered sort (R6) plus the value gate (R7) operate on the binary indexed signal for now — acceptable because the primary is hand-picked high-DA families, not a long low-DA tail.
- [Affects R8/R9][Technical — Deferred to Planning] **Physical form of the decided-store** (live-derived vs consolidated index file) + backfill of the prose-only CONDITIONAL-deferred verdicts (justpaste.it / teletype.in) into `retired-platforms/` with a `conditional` flag. Mechanical, not a product decision — planning picks the form; the build itself is R8's responsibility.

### Deferred to Planning
- [Affects R14][Security] Which `net_safety` API the probe adopts (`src/backlink_publisher/net_safety.py` vs `_util/net_safety.py`) — must match the production pipeline so SSRF posture can't diverge; confirm the egress environment (laptop vs cloud runner with a metadata endpoint).
- [Affects R5][Needs research] Whether the SPA headless-render verification reuses an existing browser path (`medium_browser` / velog recipe) or needs a new probe extension.
- [Affects R2][Needs research] Competitor backlink data source: paid API (Ahrefs / Semrush) vs scrape vs free (Common Crawl, OpenPageRank) — decides if/when signal (3) is viable, and could double as the DA data source for R7.
- [Affects R12][Technical] Whether the bad-neighborhood screen is a manual judgement or a net-new classifier.

## Next Steps
All blocking questions resolved (primary signal, convert scope, ranking proxy decided; spike done; store-form is a planning-mechanical). → `/ce:plan` for structured implementation planning.
