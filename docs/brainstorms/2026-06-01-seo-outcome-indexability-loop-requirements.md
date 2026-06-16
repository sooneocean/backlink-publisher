---
date: 2026-06-01
topic: seo-outcome-indexability-loop
---

# SEO Outcome Closed Loop — Source-Page Indexability (Deterministic Layer)

## Problem Frame

The pipeline already closes a loop on link **survival**: `recheck-backlinks` produces a
5-verdict liveness taxonomy (`alive` / `host_gone` / `link_stripped` / `dofollow_lost` /
`probe_error`), `equity-ledger` aggregates per-target live-dofollow counts, and the
deficit-driven re-plan verb turns weak targets back into seed JSONL.

But survival is not yield. The SEO value chain is:

```text
published → link present + dofollow → [host page indexable?] → [equity passes] → [target ranks]
   ✅ mature          ✅ mature              ❌ BLIND              (downstream)     (downstream)
```

`recheck` stops at "the link is present and still dofollow." A link can be `alive` on a
host page that Google will **never index** — because the platform injected
`<meta robots noindex>` on a free/new-account post, returned `X-Robots-Tag: noindex`, or the
path is robots-disallowed. An unindexed host page passes **zero** equity. Today the tool
reports that link as a healthy `alive` dofollow asset, and `equity-ledger` counts it at full
weight. (Validated 2026-06-01: the operator's one real published page —
`redredchen02.livejournal.com/574.html` — is exactly this, `noindex,nofollow,noarchive` +
robots-disallowed, HTTP 200.)

This is the project's signature **silent false-success** bug class, one tier up the value
chain from where the recheck taxonomy currently guards. The operator can run the whole
pipeline, see all-green, and gain nothing — and nothing tells them.

**Ownership constraint (why this is deterministic and offline).** Google Search Console
cannot inspect these host pages: they live on third-party platforms (medium.com,
telegraph.ph, …) the operator does not own. GSC is therefore out of scope for the source
side. But indexability **barriers** are deterministically detectable from the page itself,
and the project **already implements the hardest parts** of that detection — `content/_preflight_fetch.py`
parses `<meta robots noindex>` (`_meta_noindex`) and `X-Robots-Tag` (`_x_robots_value` /
`_has_noindex_directive`) into `PreflightFacts`. No external API, no LLM, no Google scraping.

**Grounding correction (post-review).** The naive "it all rides the existing recheck fetch
for free" framing is wrong and was corrected after document-review traced the real code:

- The recheck source-probe path (`probe_liveness → inspect_target_anchor →
  _fetch_body_via_preflight`) returns only `(body, reason)` and **discards response headers**,
  so `X-Robots-Tag` (an HTTP header) does **not** ride along — it requires either reusing the
  `PreflightFacts` fetch or plumbing headers up. Meta-robots and canonical are in the captured
  body; the header is not. Implementation strategy is therefore **reuse the existing
  `_preflight_fetch` detection**, not reimplement it in the recheck probe (a second noindex
  parser would re-introduce the verifier-divergence bug class the project explicitly guards against).
- `robots.txt` (R3d) is genuinely net-new (no fetcher/parser/cache exists today).

## Core Concept: Indexability Is Orthogonal to Liveness

Indexability is a **separate axis** from the liveness verdict, not a sixth verdict value.
A link has both a liveness verdict *and* an indexability state. The codebase already has the
exact precedent: `anchor_drift` is recorded as orthogonal probe metadata that never mutates
the liveness verdict (Plan 2026-05-29-004 R3). Indexability follows the same shape.

Honest naming: we detect indexability **barriers** (hard negatives), we do **not** claim a
page **is** indexed (unprovable offline). "No barrier found" ≠ "indexed" — it means "nothing
is stopping it."

| Liveness verdict | Indexability | Operator meaning |
|---|---|---|
| `alive` | `ok` | Healthy — link present, dofollow, no barrier to indexing |
| `alive` | `blocked` | **Silent false-success** — link present + dofollow but host page passes zero equity |
| `alive` | `unknown` | Link present; indexability undetermined (probe limits) — fail-open, not penalized |
| `dofollow_lost` | `ok` | Drift — already handled by existing taxonomy |
| `host_gone` / `link_stripped` | n/a | Dead — already handled |
| `probe_error` | `unknown` | Indeterminate — never penalized |

## Requirements

**Source-page indexability detection (extends the existing probe)**

- R1. `probe_liveness` computes an indexability state for any page it successfully reads,
  recorded as a new orthogonal field on the probe output — call it `indexability` (the same
  name flows to the `link.rechecked` payload in R5) — alongside `verdict` and `anchor_drift`.
  The liveness verdict is unchanged by this field.
- R2. Indexability is one of `ok` / `blocked` / `unknown`, mirroring the dofollow
  `True/False/uncertain` discipline. `blocked` requires a deterministic barrier; anything
  indeterminate is `unknown` (fail-open — never false-positive a barrier on transient or
  anti-bot conditions, consistent with the `probe_error` "never false-positive a death" rule).
- R3. Detected barriers (any one ⇒ `blocked`). R3a–R3b reuse the existing `_preflight_fetch`
  detection helpers (do **not** write a second parser); R3d is net-new.
  - R3a. `<meta name="robots">` / `<meta name="googlebot">` containing `noindex` (or `none`).
    Reuse `_preflight_fetch._meta_noindex`.
  - R3b. `X-Robots-Tag: noindex` (or `none`) HTTP response header. Reuse
    `_preflight_fetch._x_robots_value` / `_has_noindex_directive`. **Note:** the recheck
    source-probe path currently discards headers — satisfying R3b means reusing the
    `PreflightFacts` fetch (preferred) or plumbing headers through the probe.
  - ~~R3c. canonical-away~~ — **DROPPED** (decision 2026-06-01). Cross-URL `rel=canonical` is
    the normal, equity-**preserving** syndication mechanism, and the project's own Medium adapter
    sets a cross-domain `canonicalUrl` (`medium_api.py:215`) deliberately — so treating
    "canonical ≠ live_url" as a barrier is a false-positive factory that would penalize the
    operator's own intentional syndication. The real hard barriers (R3a/R3b/R3d) stand without it.
  - R3d. `robots.txt` disallows the page path for `*` or `Googlebot`. **Security/feasibility
    invariants (net-new code):** the derived `scheme://host/robots.txt` URL is built only from
    the **SSRF-validated final host** of the page fetch (never the pre-redirect/pre-canonical
    host) and must **independently** pass the scheme gate + pre-fetch SSRF check (not inherit
    the guard transitively); the cache is **in-memory, per-recheck-batch only** (never persisted),
    keyed strictly on the validated host; the body is byte-capped (reuse the existing
    body-prefix cap); fetch failure / oversize / parse-failure ⇒ stays `unknown`. **Honesty
    caveat:** robots-disallow is a *crawl* directive, not an *index* directive — a disallowed
    URL can still be indexed via external links, so treat it as the **weakest** barrier
    (consider advisory rather than hard-`blocked`).
- R3e. **UA-cloaking caveat (applies to all of R3a/R3b/R3d).** Every signal is read from what
  the canary's distinct UA is served; a platform can cloak (serve a clean page to the probe and
  noindex to Googlebot, or vice-versa). R3 detects a *contract-drift signal from what our UA
  sees*, **not a guarantee of what Googlebot sees** — mirror the existing `inspect_target_anchor`
  R13 honesty note.
- R4. The recheck CLI and the WebUI manual recheck share this one indexability primitive
  (same single-source-of-truth contract that R1/U2 already enforce for liveness), so the two
  surfaces can never disagree about the same URL. This single-source rule extends to the
  `_preflight_fetch`-vs-recheck noindex parsers (one shared detector, per the grounding
  correction above).

**Persistence and reporting**

- R5. The indexability state persists on the `link.rechecked` event payload as a new field,
  additive to the existing `verdict` field. Readers that don't know the field ignore it
  (forward-compatible; consistent with `advances_age_cursor` treating unknown strings as
  non-definitive).
- R6. `recheck`/report surfaces expose an `alive`-but-`blocked` count distinctly from healthy
  `alive`, so the false-success state is visible rather than hidden inside the `alive` bucket.
- R6b. **Report the `unknown`-rate per channel.** Because fail-open (R2/R7) means `unknown`
  is counted at full equity, a channel where most probes land `unknown` (e.g. Medium behind
  Datadome, JS-rendered meta) is one where the loop is **silently a no-op** — the exact
  false-success this feature hunts. Surface the per-channel `unknown` fraction so an
  all-`unknown` run is visibly inert, not falsely green. Above a configurable rate, flag the
  channel as "indexability-unverifiable by simple fetch" (candidate for the Playwright liveness
  path instead).

**Deficit closure (the loop)**

- R7. `equity-ledger` excludes links whose latest indexability state is `blocked` from its
  `live_dofollow` count. `unknown` is **not** excluded (fail-open — never penalize what we
  couldn't verify). A `blocked` dofollow link is not a live SEO asset. **Grounding correction
  (post-review):** this is *not* a free denominator tweak. `build_ledger` computes
  `live_dofollow` from `_link_liveness`, which reads only `verified_at` / `verify_error` from
  the history-store `LinkRecord` (`ledger/sources.py`) — it has **zero** connection to the
  `link.rechecked` verdict stream where R5 persists indexability. Excluding `blocked` therefore
  requires net-new code: (a) a new `indexability` field on `LinkRecord`, and (b) a new read/join
  in `build_target_buckets` that brings the latest `link.rechecked` indexability into the bucket
  (or writes it back into the history-store fields the ledger already reads). This bridge must
  be a first-class implementation unit, not an assumed no-op.
- R8. The deficit re-plan **verb** itself needs no change: once R7's bridge lands, the ledger's
  existing weak-first `live_dofollow` sort surfaces blocked-equity targets and the re-plan verb
  reads that sort unmodified — closing published → indexable → re-plan. (The "no new code"
  claim applies only to the re-plan verb, **not** to the ledger read-side, which R7 changes.)
- R9. Opt-in `--fail-on-unindexable` flag on `recheck-backlinks` (parallel to the existing
  `--fail-on-dead`), default **off** for back-compat. When set, a confirmed `blocked` link
  produces a non-zero exit so cron wrappers can alert. `unknown` never trips it.

**Destination-decay companion — SPLIT OUT (decision 2026-06-01)**

The former R10/R11 (target/money-page decay) are a **separate axis** (the operator **owns** the
target domain, so GSC URL Inspection *is* available there; different primitive `preflight-targets`
vs `probe_liveness`; different failure mode) and are moved to their own brainstorm:
**`docs/brainstorms/2026-06-01-destination-decay-monitor-requirements.md`**. R1–R9 here ship and
deliver the source-indexability goal with zero dependency on them.

## Success Criteria

- A real `alive` + (`noindex` | robots-blocked) source page is detected and reported as a
  **distinct, actionable** state — never folded into the healthy `alive` bucket.
- For a target with N alive-dofollow links of which K are `blocked`, `equity-ledger` reports
  `live_dofollow = N − K` (via the R7 ledger bridge), the target sorts proportionally weaker,
  and the unmodified deficit re-plan **verb** emits seeds for it.
- Zero false-positive barriers: transient/anti-bot/probe-error conditions land `unknown` (never
  excluded from equity, never trip `--fail-on-unindexable`). (Canonical-away is no longer a
  barrier — R3c dropped — so legitimate syndication is never penalized.)
- Per-channel `unknown`-rate is reported, so a channel where the probe can't read the page is
  visibly inert rather than falsely green.
- `--fail-on-unindexable` exits non-zero only on confirmed `blocked` links; default-off run
  behavior is byte-identical to today.
- "No barrier" is never reported as "indexed" anywhere in output or docs.

## Scope Boundaries

- **No Google querying.** No `site:` scraping, no SERP checks — unreliable, rate-limited,
  ToS-gray. We detect indexability *barriers* and never claim a page *is* indexed.
- **No GSC integration** for the source side (ownership wall). The owned-domain target side
  (where GSC URL Inspection *is* available) is split into the destination-decay brainstorm.
- **No canonical-away barrier (R3c dropped).** Cross-URL canonical is normal equity-preserving
  syndication; treating it as a barrier false-positives the operator's own intentional cross-posts.
- **No target/destination side here.** Money-page decay is split to its own brainstorm.
- **Not a new liveness verdict value.** Indexability is orthogonal metadata, not a 6th enum.
- **No auto-republish.** The re-plan path stays read-only seed generation; the operator runs
  publish. Detection feeds the deficit; it does not act on it.
- **No anchor-text-drift behavior change.** Already advisory; untouched.
- **No runtime LLM** anywhere in the probe/ledger path (kernel constraint).

## Key Decisions

- **Indexability is orthogonal metadata, not a verdict value** — follows the `anchor_drift`
  R3 precedent; preserves the fact that a link can be simultaneously `alive` and worthless.
- **Detect barriers, never claim "indexed"** — three-state `ok`/`blocked`/`unknown` mirrors
  the dofollow `True/False/uncertain` honesty discipline.
- **`unknown` fails open** — never excluded from equity, never trips the gate. Mirrors the
  `probe_error` "never false-positive a death" rule; keeps the signal trustworthy.
- **Loop closes via ledger correctness, not a new action** — excluding `blocked` from
  `live_dofollow` keeps the human in the loop (no auto-republish; respects the Round-12 #14
  auto-remediate caution). *Corrected:* this still requires a net-new ledger read-side bridge
  (R7) — it is not zero-code; only the downstream re-plan verb is untouched.
- **Reuse existing detection, don't reimplement** — R3a/R3b reuse `_preflight_fetch`'s
  meta/X-Robots parsers (one shared detector, no verifier divergence). Meta/canonical are in the
  body the probe already captures; the `X-Robots-Tag` header is **not** retained today, so R3b
  rides the `PreflightFacts` fetch rather than "the same fetch for free." Only `robots.txt`
  (R3d) is a genuinely new per-host fetch.

## Dependencies / Assumptions

- **Verified (post-review):** the recheck source-probe path (`_fetch_body_via_preflight`)
  returns only `(body, reason)` and **discards response headers** — so R3b needs the
  `PreflightFacts` fetch (which retains them), not the current recheck fetch. The meta-noindex
  + X-Robots detection helpers already exist in `content/_preflight_fetch.py`.
- **Verified (post-review):** `equity-ledger`'s `live_dofollow` is sourced from history-store
  `verified_at`/`verify_error`, **not** from `link.rechecked` — R7 requires a new ledger
  read-side bridge (new field + join), not a one-line filter.
- `link.rechecked` payloads are additively extensible (`REQUIRED_FIELDS` pins a minimum,
  readers ignore unknown keys) — R5 is genuinely additive. ✓
- The exit-code contract is locked (0–6, three tests). `--fail-on-unindexable` must reuse
  `--fail-on-dead`'s code or fit the locked contract — a planning decision, not a new code.

## Outstanding Questions

### Resolved (decisions 2026-06-01)
- **R3c dropped** — canonical-away is too false-positive-prone (normal syndication; operator's
  own Medium adapter sets cross-domain canonical). Removed from the barrier set.
- **Destination-decay split out** — R10/R11 moved to
  `docs/brainstorms/2026-06-01-destination-decay-monitor-requirements.md`.
- **Premise partially validated** — prototype detector caught a real `noindex,nofollow` +
  robots-blocked barrier on the operator's one real published page, so the barrier class is
  proven real and **detection-visibility (R1–R6, R6b) is justified now**.

### Resolve Before Planning
- **[Gates R7/R8/R9 only] Run the probe on the real dofollow corpus.** The equity-exclusion
  half (R7/R8/R9) should be confirmed against the operator's real published medium/telegraph/
  velog/hatena/substack/notion/ghpages post URLs before building:
  `BACKLINK_PUBLISHER_CONFIG_DIR=<prod> python scripts/probe_indexability.py --from-events`.
  If dofollow-channel barriers are absent/rare, ship **detection-first** (R1–R6b + the opt-in
  R9 gate) and defer the ledger bridge (R7/R8). This does **not** block planning the
  detection-visibility scope — it only sequences the equity-loop unit.

### Deferred to Planning
- [Affects R7][Technical] Exact ledger-bridge design: new `LinkRecord.indexability` field +
  join in `build_target_buckets` reading latest `link.rechecked` indexability, vs. writing it
  back into the history-store liveness fields the ledger already consumes.
- [Affects R9][Technical] Exact exit code for `--fail-on-unindexable` within the locked 0–6
  contract — reuse `--fail-on-dead`'s, or assign within contract?
- [Affects R3d][Technical] `robots.txt` parser + path-glob matcher + per-batch in-memory cache;
  Allow/Disallow precedence; empty/404/5xx handling (all ⇒ allow-or-`unknown`, never `blocked`).
- [Affects R6b][Technical] Should anti-bot channels (Medium/Datadome) route indexability through
  the existing Playwright liveness path instead of the simple fetch?

## Next Steps
The detection-visibility scope (R1–R6b + opt-in R9 gate) is **plan-ready** — barrier class
proven real, R3c/split decisions made. The ledger bridge (R7/R8) is sequenced behind the
real-corpus probe. `→ /ce:plan` for the detection-first scope; run the probe before committing
the equity-loop unit.
