---
title: "feat: Per-Channel Realized-Value Scorecard (GA4 referral + GSC discovery + unified vector)"
type: feat
status: completed
shipped: d7b0c757 (#362)
date: 2026-06-01
deepened: 2026-06-01
origin: docs/brainstorms/2026-06-01-per-channel-realized-value-scorecard-requirements.md
claims: {}
---

# feat: Per-Channel Realized-Value Scorecard

## Overview

Today every channel's "value" exists only as a **static hand-declared** kwarg in the
adapter registry (`referral_value="high"/"low"`, `dofollow=True/False/"uncertain"`) or as
an **isolated single-axis measurement** (liveness, source-page barrier, AI retrievability —
each in its own silo). No artifact can answer the operator's actual keep/prune question:
*"does this channel really send me real humans, and where does it rank across all value axes?"*

This plan builds the **two genuinely-uncovered gaps** (everything else is consumed, not
re-measured): **(1) per-channel referral attribution** (GA4, owned targets only) and
**(2) a unified per-channel scorecard** that places static declarations side-by-side with
*measured* signals as a **signal vector — never a composite magic-number**. It also adds the
**target-side GSC discovery** confirmation ("did Google register this backlink?").

**The first slice is a hard gate, not a pipeline.** A read-only probe (R0) measures realized
signal density on the operator's own targets *before* any attribution pipeline is built; if
the median per-channel attributable sessions or the GSC discovery hit-rate fall below an
operator-tunable floor, **axis B descopes** to a lightweight nofollow-UTM subset and the full
GA4 pipeline is never built.

GA4 Data API and GSC API are **net-new external surfaces in this repo** (zero existing code —
`gsearch-radar`/`fetch_gsc_data.py` are the operator's personal global skills, not project
source). This plan treats them under the same exception discipline the repo already applies to
the GEO LLM probe: optional credentials, no required env var, minimal read-only OAuth scopes,
advisory-only, human-reviewed output.

## ⚠️ Wave-0 Measurement Result (2026-06-01) — DESCOPE verdict for the GA4/GSC half

The R0 read-only probe (Unit 3) was **run for free against existing data** before building any
client, by querying the live `events.db` + `config.toml` (read-only). Result:

| Metric | Value |
|---|---|
| Owned money sites in config (`[targets.*]`/`[sites.*]`) | **1** (homepage + one sub-page, same host) |
| Articles in `events.db` (2026-01 → 2026-05) | 308 (`publish.confirmed` 287) |
| Placements whose target is the **owned** site | **2** |
| Placements whose target is a placeholder/test host (`example.com`) | **306** |
| **Owned-target share of real placements** | **≈ 0.6% (2/308)** |
| Channels with ≥1 owned-target placement (GA4/GSC axis would have data) | **2 of 7** (1 placement each) |

**Verdict — DESCOPE axes B & C now (per the plan's own R0a floor).** There is essentially **no
corpus of real owned-target placements** to attribute: the owned universe is a single site and the
event store is dominated by `example.com` test runs. Building the GA4 referral pipeline (Units 1–3,
6) + the GSC discovery pipeline (Unit 7) + the irreversible non-rotating HMAC-UTM machinery (Unit 5)
would attribute ~2 rows of real data. **Do not build the GA4/GSC half yet.**

**Re-sequenced near-term scope (this is what to build now):**
- **Phase 1 (build now): scorecard-MVP** — Unit 8's per-channel vector over **already-available
  signals only** (registry declared `dofollow`/`referral_value` + liveness/recheck + the offline GSC
  backlink CSV *if the operator already exports it*). **No** GA4 API, **no** service-account, **no**
  HMAC-UTM. This gives the operator a per-channel keep/prune view today and is the only part with data.
- **Phase 2 (deferred, conditional): the GA4/GSC attribution pipeline** (Units 1–3, 5, 6, 7) — build
  **only after** real owned-target placement volume grows. **Concrete re-trigger:** re-run this 5-line
  measurement; build the GA4 axis only when there are **≥ ~30 real placements to owned targets across
  ≥ a few channels** (operator-tunable) *and* a quick GA4 check shows non-zero referral. Until then the
  GA4 axis would measure nothing. **Runbook (the exact measurement + GO threshold):**
  `docs/runbooks/2026-06-01-channel-scorecard-phase2-retrigger.md`.
- **Irreversibility guard:** do **not** bake the non-rotating HMAC-UTM secret into published URLs
  (Unit 5) until Phase 2 is justified — it is a permanent decision serving an axis with no data.

The full Phase-2 design below is preserved as the validated blueprint for when the data exists;
the implementation units are re-labelled accordingly in the Phasing.

## Problem Frame

The machine optimizes proxy metrics end-to-end (how many published, how many alive, what
dofollow tier) but has **zero data on which channel actually drove a human to a target site**.
Two concrete blind spots (origin §Problem Frame):

1. **Referral attribution was never built.** Referrer is preserved on *some* render paths
   (`themed_gen` / `render_to_html` use `rel="noopener"` only) but stripped on others
   (`_format_anchor_html` default, zh-CN short, long-form Medium/Blogger all carry
   `noopener noreferrer`). On stripped paths GA4 cannot see the source — indistinguishable
   from "channel has no real traffic."
2. **No unified scorecard.** Signals are scattered across events.db / history_store / registry
   / ledger; the operator has no single comparable per-channel quality view to drive keep/prune.

**Ownership reality (decides which axes have data):** referral (GA4) and discovery (GSC) signals
exist **only for targets the operator owns** (xhssex / 51acgs …). Third-party targets are
**structurally data-less** on these two axes → they must degrade honestly to `unattributable`
(a displayed sentinel, **never** mis-reported as "zero value").

(see origin: docs/brainstorms/2026-06-01-per-channel-realized-value-scorecard-requirements.md)

## Requirements Trace

- **R0 / R0a / R0b** — Wave-0 read-only signal-density probe as the mandatory first slice; an
  operator-tunable quantified descope floor (median sessions < N or discovery hit < M% → axis B
  degrades); probe is read-only, never-raises, `--dry-run` offline, cost-capped, writes a report
  the operator gates by hand (does not auto-descope). → **Units 1, 2, 3**.
- **R1 / R2 / R3** — channel-derived tagging strategy: `dofollow=True` channels keep the target
  URL **completely clean** (never UTM — weight protection, registry-enforced, no publish-time
  override); `uncertain` + `False` channels carry a **deterministic, reversible, opaque** UTM
  (HMAC/opaque token, no PII, stable across re-publish). → **Unit 5**.
- **R4 / R5 / R6** — cron-safe never-raises `attribute-traffic` GA4 verb (owned targets only) →
  rolling-window per-channel real sessions/conversions in a new events.db kind; honest
  granularity labeling (per-link for UTM, channel-level for dofollow referrer); third-party →
  `unattributable` sentinel row (non-zero). Sole owner of the full GA4 pipeline (doc③ R8b is a
  consumer). → **Units 4, 6**.
- **R7 / R8 / R9** — target-side GSC dual-path discovery: **main** = semi-auto backlink-report
  CSV offline import (only direct per-channel discovery signal); **aux** = URL Inspection API
  auto (target-page indexation). Honest dual granularity, fail-open `unknown`, **no remote fetch**
  (safe parse + IDNA normalization, never fetch/resolve a remote URL). R9 per-link `site:` probe
  **not done** (default off). → **Units 4, 7**.
- **R10 / R11 / R12 / R13** — unified per-channel scorecard: declared vs measured side-by-side as
  a **signal vector** (no composite); graceful axis-inert degradation; sample-honesty + RG-kill
  stop-loss dual; output = ranking + downgrade/prune recommendation; **operator-confirm only, no
  auto-gating**. → **Unit 8**.
- **R14 / R15 / R16** — cross-cutting contracts: cron-safe / non-interactive / never-raises;
  `--dry-run` offline; metered cost with a hard cap + explicit stop-pay; new events.db kinds pass
  the `kinds.KINDS` + `REQUIRED_FIELDS` CI gate; untrusted remote-value discipline (length-cap,
  escape, parameterized SQL); inbound + outbound PII strip (query+fragment); minimal read-only
  credential discipline. → **Units 1, 2, 4** + System-Wide Impact.

## Scope Boundaries

- ❌ **No source-page indexability** — that is doc② (`source-indexability-detection`). This plan
  *consumes* its barrier signal if landed; it does not detect it.
- ❌ **No AI/GEO citation measurement** — that is doc③. This plan *combines* doc③'s per-channel
  AI-retrievability into the unified vector; it does not re-measure.
- ❌ **No new-channel discovery** — that is doc④ (`channel-discovery-funnel`).
- ❌ **No source-side GSC** / no claim that a source page "is indexed" (doc② exclusion preserved).
  Only **target-side** GSC for "was discovered" confirmation.
- ❌ **No auto-gating / publish path does not auto-consume** the scorecard (operator-driven).
- ❌ **dofollow=True links never carry UTM** (weight protection — non-negotiable, registry-enforced).
- ❌ **No PII** in attribution/UTM (outbound **and** inbound: GA4 referrer / GSC linking-page URLs
  get query+fragment stripped before persistence; raw GA4/GSC rows never persist past the
  aggregation layer).
- ❌ **No fetching / following / resolving** of any remote GSC/referrer URL (safe parse + IDNA
  normalization for matching is allowed; no new outbound network sink).
- ❌ **No single composite magic-number** (doc⑤ already vetoed it); scorecard defaults to a vector.
- ❌ **No change to article generation / anchor strategy** — only consume them. (The dofollow-path
  `noreferrer` strip that *would* enable dofollow referrer attribution is an out-of-scope,
  possibly-render-changing decision — see Deferred to Implementation.)

## Context & Research

### Relevant Code and Patterns

- **events.db kind contract** — `src/backlink_publisher/events/kinds.py` defines the `KINDS`
  frozenset (16 kinds) + `REQUIRED_FIELDS` floor (`kinds.py:61-133`). Closest template for a
  net-new kind written **directly** via `EventStore.append` (no projector Seam B) is
  `LINK_RECHECKED` (`kinds.py:53-57`, floor `{"verdict"}`) and `CITATION_OBSERVED`
  (`kinds.py:52,128`, floor `{"verdict","engine","query"}`, "parsed bounded fields only, raw
  trace never persisted"). Read-back latest-wins per identity: `recheck/events_io.py`
  `derive_decay_counts` (`:73-100`). Append-time floor enforcement quarantines-not-crashes and
  returns `-1` (`events/store.py:222-238`). `EventStore.query` is SELECT-only (`store.py:320-342`).
- **CI gates that block a new kind** — `tests/test_events_r9_required_fields.py` (floor coverage:
  `set(REQUIRED_FIELDS) == set(KINDS)`), `tests/test_events_r8_gates.py` (R8a literal-ban AST scan
  over `WRITER_MODULES`; a **new writer module must be added to `WRITER_MODULES`**; R8b reader gate
  with `ALLOWED_OMISSIONS`), `tests/test_events_kind_contract_gate.py` (only if projected).
- **Read-only advisory verb skeleton** — `cli/equity_ledger.py` (the thinnest, 66-line template:
  `import publishing.adapters` to prime registry → `load_config()` → `config_echo.emit_banner` to
  stderr → `write_jsonl` to stdout → exit 0; post-parse `UsageError` via `emit_error`,
  `[[argparse-choices-vs-usage-error]]`). For cron-safe **writer** verbs, mirror
  `cli/recheck_backlinks.py` (network gated behind `--probe`, `flock` `_single_run_lock`
  `:202-222`, never-raises batch, advisory exit 0). Facts-vs-verdict separation:
  `content/_preflight_fetch.py` ("the routine NEVER raises: every failure is recorded as a
  `reason`… returns facts only").
- **Equity ledger (the vector precedent + per-target join engine)** — `ledger/model.py`
  `LedgerRow` is *explicitly a signal vector with no composite index* (`model.py:5-6,25`) — exact
  match for D7. `ledger/aggregate.py` `build_ledger(...)` runs in-process for both CLI and WebUI;
  `ledger/sources.py` `build_target_buckets` joins events.db `articles` + `events` + history_store
  + anchor store keyed by canonical target URL. **It aggregates per-TARGET, not per-channel** —
  the per-channel pivot is genuinely new. `LinkRecord` (`sources.py:45-58`) has **no read path for
  destination receipts** — a discovery/referral column is a brand-new join.
- **Registry accessors (the static half of the scorecard)** — `publishing/registry.py`:
  `registered_platforms()` (`:414`), `dofollow_status(name)` (`:419`, returns
  `True|False|"uncertain"|None`), `referral_value(name)` (`:461`, `None` for dofollow tier),
  `dofollow_rationale(name)` (`:473`). `cli/cull_channels.py:69-78` already reads all four into one
  row per platform — near-exact template for the declared half.
- **Per-target config shape to mirror** — `config/types.py:367-373` `target_brand_aliases:
  dict[str, list[str]]`, parsed by the generic `_parse_target_string_list_field`
  (`config/parsers/target.py:50-88`), wired in `config/loader.py:191-193`, **round-tripped by the
  writer's `[targets.*]` regeneration loop** (`config/writer.py:93,141-144,203`). A new per-target
  GA4/GSC ID field must be added to **both** the loader parse **and** the writer loop + a
  round-trip test, or it is silently dropped on the next WebUI save (CLAUDE.md `save_config`
  caveat).
- **Render / `rel` paths (UTM-vs-referrer)** — `_util/markdown.py:231-252` `_format_anchor_html(url,
  anchor, *, rel="noopener noreferrer")` is the single hand-built anchor renderer. Work-themed path
  passes `rel="noopener"` (referrer-PRESERVING, `themed_gen.py:205-212`); Medium/Blogger/zh-CN long
  default to `noopener noreferrer` (STRIPPED). zh-CN short validator
  `validate_zh_short_payload` check #3 emits `missing_rel_noopener_noreferrer` (`markdown.py:306-307`)
  — a hard constraint the UTM work must not trip. UTM-injection seam = the `_format_anchor_html`
  call sites assembled from `ThreeUrlConfig` in `content/themed_gen.py` /
  `cli/plan_backlinks/_work_themed.py`.
- **Provenance / join key** — `events/schema.py:50-62` `articles.live_url` is `UNIQUE` and is the
  de-facto stable per-link identity (the ledger's join key). There is **no opaque per-link UUID
  minted at publish**; the existing `idempotency.DedupKey` (`idempotency/store.py:92-135`) is
  HMAC-SHA256 but keyed on **target**, not per-link.
- **Dashboard extension surface** — `webui_app/routes/health.py` `ce_health` (`GET /ce:health`,
  GET-only, fail-open) composes multiple read-only cards via `_g_cache(...)`; per-adapter table is
  section 3 of `templates/health.html`, fed by `health_metrics.py` `per_adapter()`
  (`GROUP BY json_extract(payload_json,'$.platform')`). **Extend this** with a
  `_g_cache("channel_scorecard", …)` card (D9 — not a 3rd dashboard). doc③'s GEO per-channel view
  **has not landed** (`citation.observed` is a declared kind with **no emitter**; no geo CLI/route)
  — the AI axis attaches here too.

### Institutional Learnings

- `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md` — the four-guardrail exception shape
  the repo applies to any external-API dependency: (1) explicitly operator-invoked, never on
  default/cron/`--replay`; (2) product runs with **no** credential (absent → clear error, rest
  unaffected); (3) output is a human-reviewed artifact, not auto-gating; (4) reuses opt-in
  primitives + redaction. Apply verbatim to GA4/GSC.
- `docs/solutions/best-practices/geo-probe-authorized-llm-exception-2026-05-29.md` — closest
  precedent for a net-new external API. **A value gate runs first** (measure whether the
  integration is worth building before building it — this is R0). No cross-key fallback; secrets
  never persisted (only parsed bounded fields).
- `docs/operations/recheck-backlinks-runbook.md` — cron-safe writer conventions: network off by
  default, exit 0 advisory, non-zero only behind an explicit `--fail-*` flag for *deterministic*
  failures, `flock` against overlapping runs, no auto-remediate, **open-loop honesty** (a steady
  advisory count ≠ "nobody acted").
- `docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md` —
  unknown upstream value → **quarantine-and-continue, never a silent `else`**; idempotent
  `dedup_key` (sha256 over the identity tuple, NULLs folded, `INSERT OR IGNORE`); **WAL
  nested-connection deadlock** — defer secondary writes until after the reducer transaction commits.
- `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`
  — the repo's #1 recurring trap ("missed one dispatch path"): a value serialized in two emitters
  (fresh `base.py` + resume `_resume.py`) wired into neither. Fix = one shared carry-helper called
  as the last step of *both*. **Directly governs UTM-at-publish** (Unit 5): enumerate fresh +
  resume + draft paths, one chokepoint, per-path presence **and** absence tests. Tier channels by
  `registry.dofollow_status(name) is True`, **not** `referral_value`.
- `content/_preflight_fetch.py` `_X_ROBOTS_MAX_LEN = 256` ("untrusted, display-only") — the named
  length-cap pattern for any persisted remote string. `events/scrubber.py` — secret scrubber
  (Bearer/JWT/`AIza…`) to run remote responses through before at-rest. `tests/test_citation_event_append.py`
  — the test shape to clone for both new kinds (round-trip, per-floor-key quarantine,
  no-secret-substring-at-rest, query-back).
- `docs/solutions/best-practices/credential-rotation-tests-cover-bootstrap-race-2026-05-19.md` —
  enumerate **every** credential mutation site (bootstrap, refresh, rotation, rebind), route through
  **one** lock, `threading.Barrier(2)` test each. `ga4-health` history already exposed credential
  expiry — credential lifecycle is a real ops item, not a stub.
- `docs/solutions/best-practices/self-doc-sanitization-leak-recurrence-2026-05-15.md` — never paste
  real operator domains / GA4 property IDs / GSC site URLs into `docs/`; use placeholder domains;
  `rg` the diff against the private-tokens list before commit.
- CLAUDE.md / test isolation — `PYTHONHASHSEED=0` required (footprint gate); four autouse conftest
  fixtures **block sockets** by default → all GA4/GSC network code lives behind the
  `real_ssrf_check` / `real_content_fetch` marker pattern, never in the default suite.

### External References

- **GA4 Data API v1** — source/medium are **separate session-scoped dimensions**
  (`sessionSource`, `sessionMedium`, `sessionChannelGrouping`); UTM campaign surfaces as
  `sessionCampaignName` / `manualCampaignName`; metrics `sessions` / conversion events. The API
  exposes **source/medium (host-level), not the full page-referrer URL** in session-scoped reports
  → confirms R5: dofollow channels join only at the **source domain** level, multiple dofollow
  links on the same host collapse to one row. (An event-scoped `pageReferrer` dimension exists but
  is noisy/event-level — deliberately not used.)
  Sources: [GA4 API schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema),
  [GA4 dimensions reference](https://www.digitalapplied.com/blog/ga4-dimensions-metrics-complete-reference).
- **Google Search Console API** — **there is no backlinks/links API**: the Web-UI "Links" section
  (external/internal links, linking domains) is not exposed by any Search Console API → forces the
  D3 dual-path (semi-auto CSV import as the only direct per-channel discovery signal). **URL
  Inspection API** quota = **2,000 QPD / 600 QPM per site** → the auxiliary auto path is quota-bounded.
  Sources: [Search Console API usage limits](https://developers.google.com/webmaster-tools/limits),
  [URL Inspection API](https://developers.google.com/search/blog/2022/01/url-inspection-api).

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| **D-A. Probe-first hard gate (R0) before any pipeline** | Mirrors the GEO-probe "value gate runs first" precedent and origin D6. Building a full GA4 attribution pipeline on channels whose real clicks trend to zero is the central waste risk; measure signal density on owned targets first, descope axis B if below an operator floor. |
| **D-B. Scorecard is a signal vector, never a composite** | Origin D7 + `LedgerRow` precedent ("deliberately no composite equity index"). A weighted index across heterogeneous axes (survival % vs sessions vs AI share) is dominated by whichever axis happens to have data, becomes a vanity metric, and erodes diagnostic trust. Default = per-axis vector; composite is deferred and must first rebut doc⑤. |
| **D-C. Tagging strategy derived from `registry.dofollow_status`, not chosen per-publish** | `dofollow=True` → clean URL (UTM would point at a *different* URL and dilute the weight the channel exists to pass); `uncertain`/`False` → opaque UTM (uncertain = "no proven weight to protect" → tag to gain attribution without betting on weight). Registry-enforced; no publish-time override (R3). |
| **D-D. UTM token = deterministic opaque HMAC, recomputable, no stored mapping** | The token (carried on the **session-scoped joinable** `utm_campaign` dimension — see Unit 5, **not** `utm_content`) is non-decodable (HMAC); determinism lets the GA4 reader **recompute** it from `(canonical target, channel)` and match without a stored PII lookup. **Over-constrained triangle (deepen):** per-link + recompute-only + no-stored-map — pick two. Recompute works for `(target, channel)` because both are enumerable at read time; true per-link would need a stored `seed → source_url` map (the deterministic per-article seed `sha256(main_domain:work_url:idx)` already exists at render time in `_work_themed.py:200` — so per-link is a *policy* choice costing one persisted column + the no-stored-map posture, **not** a technical impossibility). Default = per-`(channel, target, run)`, which is the granularity keep/prune is actually made at. **Threat scope (deepen):** the token only hides the *target URL*, **not** the channel set — `utm_source=<channel>` is plaintext on the wire, so D-D does **not** claim channel-fingerprint resistance. **Secret lifecycle (deepen):** the HMAC secret must be **shared deterministically across the publish path (Unit 5) and the read path (Unit 6), across machines and across time** (a 90-day-old token must still recompute) — the `idempotency/store.py` random-per-store secret is the **wrong** precedent. The secret is a Unit 1 deliverable (`0o600` sibling, atomic-write), **non-rotating by design** (it guards target-URL disclosure, not a high-value secret; rotation would silently zero historical attribution under never-raises) — documented as such, or a multi-secret read tried current+previous. |
| **D-E. GSC dual-path; backlink CSV is the only direct per-channel discovery signal** | Forced by the API reality (no backlinks API). Main = semi-auto CSV offline import (authoritative, no ToS conflict); aux = URL Inspection auto (target-page indexation, quota-bounded). Two different semantics, recorded separately. R9 per-link `site:` probe **default off** (needs a new ToS-gray Google-querying sink that collides with doc②'s "no `site:` scraping" exclusion). |
| **D-F. Per-target GA4/GSC mapping mirrors `target_brand_aliases`; non-secret IDs only** | Reuse the proven per-target config shape; store only property/site IDs in config, **never credentials**. Must wire loader parse + writer regeneration loop + round-trip test (else dropped on WebUI save). |
| **D-G. Extend `/ce:health` + add a read-only CLI verb; no 3rd dashboard** | Origin D9. The health route already composes read-only cards via `_g_cache`; the scorecard is one more card plus a JSONL verb mirroring `equity-ledger`. Avoids a third same-source surface. **Drift-prevention (deepen):** the scorecard engine MUST consume the existing three-store join (`ledger/sources.py` `build_target_buckets`, which already attaches `platform` per `LinkRecord` and whose `aggregate._classify(platform)` is already the channel-tier logic) and **re-key it by channel** — it must **not** re-query events.db/history/anchor independently (a parallel reader will not replicate the ledger's orphan-history and "attempted-targets" subtleties and *will* drift). Refactor `build_target_buckets` out of `ledger/` into a shared pivot-agnostic `sources` module; `build_ledger` (pivot=target) and `build_channel_scorecard` (pivot=channel) become two thin aggregators over the same buckets. The shared source layer — not the parallel engine — is the actual anti-drift mechanism. |
| **D-H. Credentials = read-only service account, under the no-runtime-LLM four-guardrail shape** | Optional, no required env var, no cross-key fallback, `0o600` + atomic-write, one credential-mutation lock Barrier-tested across bootstrap/refresh. **Credential type (deepen):** pick a **service-account key** (not OAuth-user token) — no interactive refresh, no `OAUTHLIB_INSECURE_TRANSPORT` exposure, scoped per-property; "minimal privilege" is then enforced by *which GA4 properties / GSC sites the service account is granted on*, and the `0o600` file is a private signing key (raises the stakes on the no-leak tests). The clients must **never** set `OAUTHLIB_INSECURE_TRANSPORT` (a documented repo foot-gun — assert it unset). |

## Open Questions

### Resolved During Planning

- **Composite vs vector (R10)** → signal vector, no magic-number (D-B; `LedgerRow` precedent).
- **GSC discovery mechanism (R7)** → confirmed no backlinks API; semi-auto CSV (main) + URL
  Inspection (aux) (D-E).
- **GA4 attribution granularity (R5)** → session `source`/`medium` (host) for dofollow referrer;
  `sessionCampaignName`/`manualCampaignName` (UTM) for the per-`(channel,target,run)` join. GA4
  gives no full-referrer URL → dofollow = channel-level only.
- **UTM opaqueness/reversibility (R2)** → deterministic recomputable HMAC token; no stored
  PII/mapping; no literal decode (D-D).
- **Config shape (R6)** → mirror `target_brand_aliases`, non-secret IDs, round-trip both
  loader + writer (D-F).
- **Scorecard surface (R11/D9)** → extend `/ce:health` + a `channel-scorecard` CLI verb (D-G).
- **dofollow-path `noreferrer` strip (R1)** → **out of scope** (origin default); the work-themed
  path already preserves referrer, the stripped paths stay unchanged.
- **R9 `site:` per-link advisory** → **not done** (default off; D-E).
- **Idempotency model (deepen)** → **snapshot-per-run + read-latest-snapshot** for the additive
  `traffic.attributed`; monotone `(target,channel,linking_domain,source)` for `link.discovered`. The
  reader never sums raw rows. (Replaces the original double-counting `(target,channel,run-date)` tuple.)
- **New-kind floors (deepen)** → `traffic.attributed` = `{channel, granularity}`; `link.discovered` =
  `{channel, source}`; **`target_url` excluded** (first-class column); `sessions` excluded (sentinels).
- **Scorecard read shape (deepen)** → a parallel per-channel engine that **re-keys the shared
  `build_target_buckets`**, not a `LedgerRow` extension and not an independent re-query (D-G).
- **Credential type (deepen)** → read-only **service-account key** (D-H); HMAC secret is shared &
  **non-rotating by design** (D-D).
- **SSRF framing (deepen)** → "parse + IDNA codec, never fetch/resolve"; reuse `_util/url.py`.

### Deferred to Implementation

- **N / M descope thresholds (R0a)** — the numeric floor for median sessions / discovery hit-rate.
  Deliberately deferred: the R0 probe's measured distribution sets them (operator-tunable config).
- **Exact GA4 `runReport` request** — dimension/metric/filter/date-range/pagination/sampling
  handling for `sessionSource`/`sessionMedium`/`sessionCampaignName` + `sessions`/conversions;
  resolved against a live property during Unit 6. (Note: `metric_date`/window bounds come **from the
  response in the property TZ**, never recomputed — the decision is fixed; only the request shape is
  deferred.)
- **Exact GSC artifacts** — the operator's backlink-report CSV column layout, and the URL Inspection
  request/response field mapping (`indexStatusResult`, `lastCrawlTime`).
- **Snapshot identity + `as_of` staleness window** — final snapshot-row columns and the "older than N
  runs → stale" threshold (the *model* is decided; the exact N is operator-tunable).
- **UTM truncation length `n`** — chosen against the operator's `(target×channel)` pair-count with a
  no-collision test (Unit 5).
- **Per-axis observation budget / RG-kill (R12/R14)** — time-box sizes, the
  sufficient-sample-but-signal≈0 stop-loss threshold, and the stop-pay condition.
- **Per-link UTM policy fork** — default is per-`(channel,target,run)`; true per-link is **possible**
  by persisting the render-stage per-article seed (`_work_themed.py:200`) into a stored map, at the
  cost of the no-stored-map posture. A policy decision, surfaced — not a technical impossibility.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation
> specification. The implementing agent should treat it as context, not code to reproduce.*

**Value chain — what this plan adds (blue) vs consumes (existing axes):**

```mermaid
flowchart TB
    PUB[publish path] -->|axis A · Unit 5| TAG{channel tag strategy<br/>from registry.dofollow_status}
    TAG -->|dofollow=True| CLEAN[clean URL · referrer-only · channel-level]
    TAG -->|uncertain / False| UTM[opaque HMAC UTM · per channel,target,run]

    CLEAN --> GA4[(GA4 · owned targets)]
    UTM --> GA4
    GA4 -->|axis B · Unit 6| TA[traffic.attributed<br/>events.db kind]

    GSCCSV[GSC backlink CSV<br/>semi-auto import] -->|axis C · Unit 7| LD[link.discovered<br/>events.db kind]
    URLINSP[GSC URL Inspection API] -->|aux| LD

    subgraph CONSUMED[consumed, not re-measured]
      LIVE[liveness · recheck/history]
      BARRIER[source indexability · doc②]
      AI[AI retrievability · doc③]
      DECL[registry: dofollow / referral_value]
    end

    TA --> SC[axis D · Unit 8<br/>per-channel scorecard<br/>declared vs measured · VECTOR]
    LD --> SC
    LIVE --> SC
    BARRIER -.if landed.-> SC
    AI -.if landed.-> SC
    DECL --> SC
    SC -.recommend + operator confirm.-> KP[keep / invest / prune<br/>NO auto-gating]

    classDef new fill:#1f6feb,stroke:#0b3d91,color:#fff
    class TAG,CLEAN,UTM,TA,LD,SC new
```

**Channel tag-strategy matrix (Unit 5):**

| Dimension | `dofollow=True` | `uncertain` | `nofollow` (False) |
|---|---|---|---|
| Target URL | **completely clean** (protect weight) | opaque UTM | opaque UTM |
| Attribution mechanism | GA4 referrer (**referrer-preserving render paths only**) | UTM campaign | UTM campaign |
| Granularity | **channel-level only** (source domain) | per-`(channel,target,run)` | per-`(channel,target,run)` |
| Why | UTM points at a different URL → dilutes weight | no proven weight to protect → tag for attribution | no weight to protect → tag fully |

**Implementation-unit dependency graph** (diamond: Unit 3 gates the axis-B units; Unit 4 fans into the readers):

```mermaid
flowchart TB
    U1[Unit 1 · config + creds + UTM secret] --> U2[Unit 2 · GA4/GSC read clients]
    U1 --> DT[utm.derive_token · pure fn]
    U2 --> U3{Unit 3 · R0 probe<br/>HARD GATE}
    U4[Unit 4 · 2 new events.db kinds]
    DT --> U5[Unit 5 · UTM/referrer tagging · axis A]
    DT --> U6[Unit 6 · attribute-traffic · axis B]
    U3 -->|GO: full B| U6
    U3 -->|GO or DESCOPE: nofollow-UTM| U5
    U3 -->|GO| U7[Unit 7 · attribute-discovery · axis C]
    U4 --> U6
    U4 --> U7
    U4 --> U8[Unit 8 · scorecard · re-keys shared buckets]
    U6 -.snapshot-latest, degrades gracefully.-> U8
    U7 -.degrades gracefully.-> U8

    classDef gate fill:#b8860b,stroke:#000,color:#fff
    class U3 gate
```

> Note the corrected edges (deepen): `derive_token` is a **fan-out** feeding both the publish-side
> tagging (U5) and the read-side recompute (U6) — U6 does **not** chain off U5's publish wiring. U5's
> nofollow-UTM subset is reachable in the **DESCOPE** branch too (the descope *is* U5). U4's floors are
> frozen **jointly** with U6/U7, not ahead.

## Implementation Units

> **⚠️ Re-sequenced by the Wave-0 measurement (2026-06-01).** The R0 gate already returned **DESCOPE**
> (see the Measurement Result above). **Build now = Unit 8 only**, restricted to already-available
> signals (registry + liveness/recheck + optional offline GSC CSV) — call this the **scorecard-MVP**.
> **Units 1, 2, 3, 5, 6, 7 (and the GA4/GSC half of Unit 4) are DEFERRED to Phase 2**, conditional on
> real owned-target placement volume growing (re-trigger in the Measurement Result). The unit specs
> below are unchanged and remain the validated Phase-2 blueprint; only their *ordering* and
> *build-now status* change.

### Phase 2 (DEFERRED — build only after the re-trigger) — Foundations + Value Gate

- [ ] **Unit 1: Per-target GA4/GSC config mapping + credential discipline**

**Goal:** The external-surface bedrock — per-target → property/site mapping in config (non-secret),
and a minimal-privilege, optional, never-leaking credential loader.

**Requirements:** R6, R14, R16.

**Dependencies:** None.

**Files:**
- Modify: `src/backlink_publisher/config/types.py` (add `target_ga4_property: dict[str,str]`,
  `target_gsc_site: dict[str,str]`), `config/parsers/target.py`, `config/loader.py`,
  `config/writer.py` (both parse **and** the `[targets.*]` regeneration loop)
- Create: `src/backlink_publisher/analytics/__init__.py`, `analytics/credentials.py` (load
  read-only **service-account** creds; optional; `0o600` + `safe_write.atomic_write`; one
  credential-mutation lock), and the **UTM HMAC-secret** primitive (a `0o600` sibling, atomic-write,
  loaded by both Unit 5 and Unit 6 — D-D's shared-secret requirement; **non-rotating by design**)
- Modify: `config.example.toml` (documented per-target `ga4_property` / `gsc_site`, placeholder IDs);
  `events/scrubber.py` (add a Google **refresh-token `1//…`** pattern; the existing patterns miss it
  and miss GA4 property IDs)
- Test: `tests/test_config_target_analytics_roundtrip.py`, `tests/test_analytics_credentials.py`,
  `tests/test_utm_secret.py`

**Approach:**
- Mirror `target_brand_aliases` exactly (single-string variant of `_parse_target_string_list_field`).
  Config stores only non-secret property/site IDs.
- Credentials follow the no-runtime-LLM guardrail shape: optional, **no required env var**, **no
  cross-key fallback**, a **service-account key** (D-H — read-only, per-property grant, no OAuth
  refresh flow), absent → clear error and the rest of the tool unaffected. Route every credential
  mutation (bootstrap, refresh, rotation) through one lock. Never set `OAUTHLIB_INSECURE_TRANSPORT`.
- **UTM HMAC secret** is a first-class deliverable here (not implied by Unit 5): same `0o600` +
  atomic-write discipline, shared by publish + read, documented non-rotating (rotation silently
  zeroes historical attribution under never-raises).

**Execution note:** Add the writer round-trip test first — the `save_config` `[targets.*]` drop is a
known recurring data-loss trap (characterization-first on the round-trip).

**Patterns to follow:** `config/types.py:367-373` + `parsers/target.py:50-88` + `writer.py:141-144`;
credential `0o600`/atomic-write per CLAUDE.md llm-settings rule; lock+Barrier per
`credential-rotation-tests-cover-bootstrap-race-2026-05-19.md`.

**Test scenarios:**
- Happy path: a `[targets."https://your-site.example"]` table with `ga4_property` / `gsc_site` →
  loaded into `Config.target_ga4_property` / `target_gsc_site` keyed by trailing-slash-stripped domain.
- Integration (round-trip): load → `save_config` → reload preserves both fields (this is the
  data-loss guard — assert the value survives, not just that save doesn't crash).
- Edge: malformed/missing field → skipped with a warning, other targets unaffected (tolerant parse).
- Error path: credential file absent → loader returns a typed "not configured" result, **no raise**,
  and an unrelated verb still runs.
- Edge: credential file (service-account key) present but `0644` → re-chmod to `0o600` on load (or
  refuse with a clear error), and the value never appears in logs/stderr.
- Edge (secret leak): the scrubber strips a Google **refresh token `1//…`** and a service-account
  private key from any free-text it's given, and `str()`-coerces non-`str` input instead of raising.
- Edge (env trap): with `OAUTHLIB_INSECURE_TRANSPORT` set in the environment, the client refuses to
  run (or the test asserts the code never sets it).
- Concurrency: `threading.Barrier(2)` over bootstrap-write vs refresh → no torn file, single winner.
- Edge (UTM secret): the HMAC secret is generated once, `0o600`, and the **same** secret recomputes
  the **same** token on a second load (determinism — the cross-path/cross-time requirement).

**Verification:** A target with analytics IDs survives a config round-trip; absent credentials never
crash any verb; no credential substring (incl. refresh token / property ID) appears in any log or
persisted artifact; the UTM secret is stable across loads.

---

- [ ] **Unit 2: Never-raises GA4 + GSC read clients (facts-only)**

**Goal:** Thin read clients that return **facts + a `reason` on failure**, never raise, never fetch
remote URLs, and strip PII before returning.

**Requirements:** R4 (fetch half), R7 (fetch half), R8 (string-match discipline), R14, R15, R16.

**Dependencies:** Unit 1.

**Files:**
- Create: `src/backlink_publisher/analytics/ga4_client.py` (GA4 Data API `runReport` wrapper),
  `analytics/gsc_client.py` (URL Inspection API wrapper, quota-aware), `analytics/gsc_csv.py`
  (offline backlink-report CSV parser), `analytics/_sanitize.py` (length-cap + userinfo/query/fragment
  strip + CSV-formula neutralizer; host normalization **reuses** `_util/url.py`)
- Test: `tests/test_ga4_client.py`, `tests/test_gsc_client.py`, `tests/test_gsc_csv.py`,
  `tests/test_analytics_sanitize.py`

**Approach:**
- GA4: request `sessionSource`/`sessionMedium`/`sessionCampaignName` + `sessions`/conversions
  (session-scoped); return bounded parsed rows only. URL Inspection: quota-metered (2,000 QPD /
  600 QPM/site), per-run cap. CSV: pure offline parse of the operator's GSC export.
- **SSRF boundary — corrected framing (deepen):** the safe line is **"invoke a URL parser + IDNA
  codec, never a fetcher/resolver,"** *not* "pure string matching" (punycode/IDNA normalization is
  not string manipulation and can't be hand-rolled safely). **Reuse `_util/url.py`**
  (`safe_hostname`, the IDNA path in `normalize_url_for_fetch`, `is_same_host`) — do **not** hand-roll
  a second host-normalizer in `_sanitize.py`. Reject non-http(s); open **zero sockets**.
- **Host-matching integrity (deepen):** IDN homoglyph is a *matching-correctness* threat, not SSRF —
  a homoglyph host could **false-match** a high-value channel and inflate its scorecard (an integrity
  attack on keep/prune economics). Match on the **IDNA-encoded (`xn--`) form** of both sides so
  visually-distinct-but-different hosts stay distinct.
- **PII strip (inbound) — completed (deepen):** query+fragment are **not** the only carriers.
  `_util/url.py:canonicalize_url` **deliberately preserves `user:pass@host` userinfo** → add an
  explicit `parsed.username/password → drop` step. Reduce **all** persisted remote URLs to **host-only
  where possible** (the dofollow path already does; apply uniformly — paths can carry PII like
  `/u/<email>` or `/reset/<token>`). Make "persisted referrer granularity = host, never full URL" a
  **tested invariant**, not an emergent property of which GA4 dimension was chosen.
- **Untrusted dimension *values* (deepen):** `sessionCampaignName` / GSC linking hosts are
  **attacker-settable** free text (anyone can append `?utm_campaign=<x>` to a link at an owned target).
  Length-cap (named `_MAX_LEN`) + scrub (`events/scrubber.py`) + escape **every** GA4/GSC string value,
  not just URLs. **Raw GA4/GSC response bodies are never persisted** — a tested invariant (the events
  row contains only floor keys, never a raw-response blob; Google 401 bodies can echo `key=`/headers
  that bypass the scrubber's named patterns).
- **CSV-formula neutralizer (deepen):** the GSC CSV is an untrusted file and the scorecard/probe
  produce operator-facing artifacts → any value written to a CSV/TSV/spreadsheet cell that starts with
  `= + - @ \t \r` must be neutralized (leading `'`); any remote-derived string in a Markdown report is
  rendered as **inert escaped text, never an autolink**.
- **CSV is an untrusted file — bound the parse (auto-fix):** the never-raises contract would *hide* an
  OOM. Enforce a hard **max-file-size + max-row cap** (reject-with-`reason` past the cap, not raise),
  **stream/iterate** rather than read-all-into-memory, and **no decompression** of the import (or a
  bounded guard). Treat the CSV like `_preflight_fetch` treats remote bytes: bounded, facts-only.
- All network behind `real_ssrf_check` / `real_content_fetch` markers (sockets blocked by default).

**Execution note:** `Execution target: external-delegate` once the response→facts contract is fixed —
these are pure transform functions with mockable inputs.

**Patterns to follow:** `content/_preflight_fetch.py` (facts-vs-verdict, `_X_ROBOTS_MAX_LEN`),
`_util/url.py` (`safe_hostname` / IDNA `normalize_url_for_fetch` / `is_same_host` — reuse, don't
re-roll), `events/scrubber.py`, the autouse socket-blocking conftest + `real_*` markers.

**Test scenarios:**
- Happy path: a mocked GA4 response → bounded per-channel rows with `sessions` populated.
- Happy path: a sample GSC CSV → per-channel discovery hits with linking domains.
- Edge: empty/zero-row GA4 response → empty facts, `reason="no_data"`, no raise.
- Error path: 401/403/quota-exceeded → facts empty + typed `reason`, **exit-0-safe** (no raise);
  no `Authorization`/`Bearer`/refresh-token substring in any returned/persisted field; the persisted
  row carries only floor keys (no raw-response blob).
- Edge (PII query/frag): a referrer URL `…?token=…#email=…` → persisted form is host(+path) only.
- Edge (PII userinfo): `https://user:pass@host/p` → persisted form has **no** userinfo (the
  `canonicalize_url`-preserves-userinfo gap is closed in `_sanitize`).
- Edge (PII path): a path-bearing URL → reduced to host (referrer granularity invariant asserted).
- Edge (untrusted value): a campaign name of `=cmd|…` / `<script>` / a 10KB string → capped, scrubbed,
  escaped before it reaches any returned field.
- Edge (SSRF): a malicious linking-page string (redirect chain, `file://`, IP-literal,
  `user:pass@`) → rejected by parse+IDNA normalization, **zero** sockets opened.
- Edge (homoglyph, split): (a) a legitimate Unicode host matches its `xn--` channel entry; (b) a
  Cyrillic-homoglyph host does **not** false-match a different channel.
- Edge (CSV formula): a linking domain `=HYPERLINK(…)` written to a CSV/TSV export is neutralized
  (leading `'`); rendered into Markdown it is inert escaped text, not an autolink.

**Verification:** Every failure mode returns facts+reason without raising; no remote URL is ever
fetched; no secret/PII/raw-response survives to a returned field; homoglyph hosts never false-match.

---

- [ ] **Unit 3: R0 read-only value probe (HARD GATE)**

**Goal:** Measure realized signal density on owned targets **before** building the attribution
pipeline, and emit a GO / DESCOPE report for axis B that the operator gates by hand.

**Requirements:** R0, R0a, R0b.

**Dependencies:** Units 1, 2.

**Files:**
- Create: `src/backlink_publisher/cli/attribute_probe.py` (read-only one-shot verb),
  `src/backlink_publisher/analytics/probe.py` (signal-density engine)
- Modify: `pyproject.toml` (console-script entry `attribute-probe`)
- Test: `tests/test_attribute_probe.py`

**Approach:**
- Compose Units 1+2: one GA4 referral snapshot (last 30d, per-channel attributable sessions) + one
  GSC backlink-CSV ingest (per-channel discovery hits) + the **owned-GA4/GSC-target share** of live
  targets (resolves the never-quantified ownership-ratio risk).
- **Report location — corrected (deepen):** the probe report contains the operator's **real**
  per-target numbers and owned-target shares **by necessity**, so the "placeholder domains only" rule
  cannot apply. Write it **outside the committable tree** — under the config dir (or a path that is
  `.gitignore`d) — **never** to `docs/discovery/` (a committable location). Add a CI/test guard that
  the report path is git-ignored. Remote-derived strings render as **inert escaped text, never
  Markdown autolinks** (operator-clicks-a-report vector). Report states, per the **operator-tunable**
  R0a floor, whether axis B is **GO** or **DESCOPE** (degrade to nofollow-UTM subset). **The probe
  does not auto-descope** — it reports; the operator decides (R0b/D5).
- never-raises, `--dry-run` = zero-network scope preview, cost-capped + explicit stop condition.
  Writes **no** events.db rows (it is a probe, not a pipeline).

**Execution note:** This is the value gate. Units 5/6/7 (full axis B) are **conditional on its GO
verdict** — build and run this, read the report, then proceed.

**Patterns to follow:** `cli/equity_ledger.py` (read-only skeleton), `cli/recheck_backlinks.py`
(`--probe`/`--dry-run` network gating), origin R0/R0a/R0b.

**Test scenarios:**
- Happy path: mocked signals above the floor → report renders GO for axis B with per-channel density.
- Happy path: signals below the floor → report renders DESCOPE with the specific failing metric.
- Edge: zero owned targets configured → report says "no owned targets — both new axes inert," exit 0.
- Edge: `--dry-run` → lists what *would* be queried, makes **zero** network calls, writes no report
  file (or a clearly-marked dry sample).
- Error path: GA4 reachable but GSC CSV missing → partial report with the missing half flagged
  `unknown`, exit 0.
- Edge (cost cap): the configured per-run cap is reached → probe stops, reports the stop, exit 0.
- Edge (leak guard): the report is written to the git-ignored/config-dir path (asserted), and remote
  hosts in it are inert text, not autolinks.

**Verification:** The operator can read a single report that quantifies per-channel signal density +
owned-target share and tells them whether the full axis-B pipeline is worth building — and that report
never lands in a committable path.

### Phase 2 (DEFERRED — conditional on Unit 3 GO *and* the Wave-0 re-trigger) — Attribution Pipeline

- [ ] **Unit 4: Two new events.db kinds — `traffic.attributed` + `link.discovered`**

**Goal:** Register both kinds against the CI-gated contract, written directly via
`EventStore.append`, idempotent across cron re-runs, persisting parsed bounded fields only.

**Requirements:** R15 (+ enables R4, R7).

**Dependencies:** None (can land in parallel with Phase 1).

**Files:**
- Modify: `src/backlink_publisher/events/kinds.py` (`KINDS` frozenset + `REQUIRED_FIELDS` floors),
  `tests/test_events_r8_gates.py` (`WRITER_MODULES` — add the new writer module(s))
- Create: `src/backlink_publisher/analytics/events_io.py` (append + latest-wins read helpers)
- Test: `tests/test_traffic_attributed_event.py`, `tests/test_link_discovered_event.py` (clone
  `tests/test_citation_event_append.py`)

**Approach:**
- Add `TRAFFIC_ATTRIBUTED = "traffic.attributed"` and `LINK_DISCOVERED = "link.discovered"` as
  `Final` constants. Append **directly** (no projector Seam B, like `link.rechecked`/`citation.observed`).
- **Floors — corrected (deepen):** **drop `target_url`** (it travels in the first-class
  `target_url`/`host`/`article_id` columns; in the floor it is redundant and would *quarantine
  well-formed rows* that pass it only as the column arg — see `kinds.py:130-132`). Floor on the
  load-bearing payload key with **no backing column**: `traffic.attributed` → `{channel, granularity}`
  (a row whose `granularity` — per-link vs channel-level — is unknown is un-interpretable);
  `link.discovered` → `{channel, source}` (`source` discriminates `csv` vs `url_inspection`, which the
  design says are "never conflated"). **Keep `sessions` out** of the floor so `unattributable`
  sentinel rows (which legitimately have no session count) are not quarantined.
- **Idempotency — corrected (deepen, load-bearing).** (This **replaces** an earlier
  `(target, channel, run-date)` identity that would have double-counted the rolling window ~30× on a
  daily cron.) `EventStore.append` has **zero** dedup (a bare INSERT), so safety comes from the
  **reader**, not the writer. The two kinds need **different** identities because one is additive and
  one is monotone:
  - `traffic.attributed` is an **additive measure over a rolling window** — latest-wins on a
    run-date bucket double-counts ~30× (every daily run re-reports the overlapping 30-day window).
    Use the **snapshot-per-run + read-latest-snapshot** model: each run writes one immutable snapshot
    `(target, channel, run_id, window_start, window_end, sessions, as_of)`; the reader takes the
    **latest snapshot per `(target, channel)`** and reports *that* window's number — **never summing
    across runs**. (Alternative: per-`metric_date` rows where `metric_date` is taken **verbatim from
    the GA4 response in the property's timezone, never recomputed from a UTC clock**, read by
    sum-of-**distinct**-days. Pick snapshot-per-run by default — simpler, no day-dedup footgun.)
  - `link.discovered` is **monotone** ("Google found this link") → identity
    `(target_url, channel, linking_domain, source)`, **no date bucket**; latest-wins.
- **Reader contract (hard):** every reader of these kinds is **latest-wins / snapshot-latest and
  never sums raw rows** — that, plus the Unit 6/7 `flock`, substitutes for the dedup `append` does not
  provide. **Do not** use pre-read-check-then-append (a check-then-act race for no gain).
- **No secrets / no raw response** at rest. **WAL — scoped (deepen):** the nested-connection deadlock
  bites **only** if the writer batches appends under one shared `conn` and a floor-miss tries to
  quarantine inline. Unit 6/7 **should** batch under one `conn` for run-atomicity → therefore they
  **must** thread `pending_quarantines` through every `append` and flush `write_quarantines` *after*
  commit (the `emit_recheck` shape). State the connection model explicitly; a test forces a mid-batch
  floor-miss to prove no deadlock and no lost quarantine.

**Execution note:** Test-first — clone `test_citation_event_append.py` for both kinds **before**
wiring the writers (the R2/R9 gates fail CI the moment a kind lacks a floor). Freeze the floors
**jointly with Unit 6/7 payload design**, not ahead of it.

**Patterns to follow:** `events/kinds.py:52-133` (floor convention: identity in columns, not floor),
`recheck/events_io.py:33-100` (`emit_recheck` batched-`conn` + post-commit `write_quarantines`;
`derive_decay_counts` latest-wins read), `tests/test_citation_event_append.py`,
`projector-silent-drop-…-2026-05-26.md` (quarantine + WAL).

**Test scenarios:**
- Happy path: append a full payload of each kind → one event row, query-back by `target_url`/`host`.
- Edge (floor): a payload missing a floor key (`channel`/`granularity`; `channel`/`source`) →
  `append` returns `-1`, **no** event row, a quarantine row written, **no exception**.
- Edge (no false-quarantine): a row passing `target_url` only as the column arg (not in payload) is
  **not** quarantined (confirms `target_url` is correctly out of the floor).
- Edge (rolling-window idempotency): two runs pulling overlapping windows → the reader reports the
  **latest snapshot's** number, **not** the sum (the ~30× double-count is provably absent).
- Edge (monotone): re-discovering the same `(target, channel, linking_domain, source)` → one row,
  latest-wins; a different `source` for the same link → a **distinct** row (paths not conflated).
- Edge (WAL): a forced mid-batch floor-miss under a shared `conn` → no deadlock, the quarantine is
  flushed post-commit (not lost).
- Edge (secret/PII): a payload with an embedded `Bearer …`/`AIza…`/`1//…` substring → scrubbed; a
  runtime-built no-secret-substring assertion over the stored row passes; the row carries only floor
  keys, no raw-response blob.
- Contract: `set(REQUIRED_FIELDS) == set(KINDS)` still holds; the R8a literal-ban AST scan passes
  with the writer module registered in `WRITER_MODULES`.

**Verification:** Both kinds round-trip; the `traffic.attributed` reader provably never double-counts a
rolling window; `link.discovered` is monotone; all events CI gates (R2/R8a/R8b/R9) stay green.

---

- [ ] **Unit 5: Channel-derived UTM/referrer tagging at publish (axis A)**

**Goal:** Auto-derive each channel's tag strategy from `registry.dofollow_status`; tag
`uncertain`/`False` channels with an opaque deterministic UTM through **one** shared helper across
**all** dispatch paths; never tag `dofollow=True`.

**Requirements:** R1, R2, R3.

**Dependencies:** the UTM secret (Unit 1) and the `utm.derive_token()` function. **Gating (deepen):**
the nofollow-UTM *subset* of this unit is reachable in **both** the GO and the DESCOPE branch (the R0
descope is defined as "lightweight nofollow-UTM subset" — that *is* this unit); only the full GA4
pipeline (Unit 6) is strictly GO-gated.

**Files:**
- Create: `src/backlink_publisher/publishing/utm.py` — split into `derive_token(canonical_target,
  channel)` (pure, also imported by Unit 6's reader) and `apply_channel_tag(url, channel)` (the one
  helper that decides clean-vs-UTM and applies it)
- Modify: the **plan-stage URL-assembly seam** — where `ThreeUrlConfig` URLs are finalized **before**
  reaching `_format_anchor_html` (which only escapes): `content/themed_gen.py:205-211` (all three
  slots: main / list / work), `cli/plan_backlinks/_work_themed.py`, the zh-short path
  (`_zh_short.py`) and `_payload.py`. **Seam-audit completeness (auto-fix):** `_format_anchor_html`
  has a **third** caller — `anchor/preflight.py` — that the seam audit must enumerate and classify
  (publish-path → route through `apply_channel_tag`; diagnostic-only → state the absence of tagging is
  intentional). The #1 recurring trap is "missed one dispatch path." (Resume/draft **replay a stored
  payload** — they do not re-render — so the real invariant is "UTM is frozen into the payload at
  render," not "every publish emitter re-applies it.")
- Test: `tests/test_channel_utm_tagging.py`, `tests/test_utm_render_seam.py`

**Approach:**
- Strategy: `dofollow_status(channel) is True` → return URL **unchanged** (clean, registry-enforced,
  no override). `False`/`uncertain`/**`None`** → tag (deepen: name the `None`/unregistered branch
  explicitly = treat like uncertain → tag; inherit the ledger's `unknown ≠ nofollow` discipline —
  better to over-tag an unknown channel than dilute a dofollow you forgot to declare). UTM =
  `utm_source=<channel>`, `utm_medium=backlink`, and the recomputed token on the **session-scoped
  joinable** dimension — **`utm_campaign=blp.<derive_token(canonical_target, channel)>`** (→ GA4
  `sessionCampaignName`/`manualCampaignName`), **not** `utm_content` (auto-fix: `utm_content`/term are
  not reliably surfaced at session scope in `runReport`; the join would silently never match —
  **validate against a live property** which session-scoped dimension actually carries the token
  before committing the field). Opaque, deterministic, recomputable, no PII; truncation length `n`
  justified against the `(target×channel)` pair-count with a no-collision test. Granularity =
  per-`(channel,target,run)`.
- **Seam — corrected (deepen):** the chokepoint is the **plan-stage** URL assembly, not the
  publish-stage "fresh/resume/draft emitters" (that triplet is a publish concept; the UTM is already
  baked into the content payload by then). Apply the tag to the URL **before** `_format_anchor_html`,
  covering every URL slot (main/list/work + zh-short + payload). The real test is "**UTM frozen into
  the rendered payload; replay never re-derives**," not "all dispatch paths call the helper."
- **Dedup invariant (deepen):** a UTM-tagged target and its clean form **must** resolve to the same
  `idempotency.DedupKey` — true today only because `_util/url.py:canonicalize_url` strips `utm_*`
  (`:243`); add a regression test so a future canonicalizer change can't silently start double-publishing.
- **Footprint safety:** vary the UTM across footprint dimensions so no constant byte-marker reaches
  ≥95% prevalence; run the `footprint` extractor in the test.
- Do **not** change `rel` / strip `noreferrer` (out of scope); honor the zh-CN short validator
  `missing_rel_noopener_noreferrer`.

**Execution note:** Single chokepoint at the render seam; assert UTM presence on UTM channels **and**
byte-clean absence on dofollow channels, and that a replayed payload re-publishes identical bytes
(per `dofollow-canary-verdict-dropped-at-publish-output-seam`, retargeted from publish-stage to
render-stage).

**Patterns to follow:** `registry.dofollow_status` (tier predicate, `is True` only),
`content/themed_gen.py:205-211` (URL-assembly seam), `idempotency/store.py:key_digest` (HMAC shape —
but **shared, not random**, secret per D-D), `_util/url.py:canonicalize_url` (UTM-stripping dedup
invariant), `footprint.py` (prevalence check).

**Test scenarios:**
- Happy path: an `uncertain` channel → URL gains the deterministic UTM frozen into the payload;
  re-render yields the **same** token (stable, recomputable).
- Happy path (hard rule): a `dofollow=True` channel → rendered URL is **byte-identical** to clean
  (no UTM).
- Edge (None branch): an unregistered/`None` channel → tagged (treated as uncertain), explicitly.
- Integration (replay): a stored payload re-published via resume/draft emits **identical** bytes — the
  UTM is not re-derived, not dropped, not changed.
- Edge (dedup invariant): a UTM-tagged target and its clean form yield the **same** `DedupKey`.
- Edge (opaque/PII): the token is not literal-decodable (HMAC); the UTM carries only `channel` + token,
  no email/id/PII.
- Edge (collision): two distinct `(target, channel)` pairs in the operator's set never collide at the
  chosen truncation `n`.
- Edge (footprint): across N posts the UTM dimensions do not produce a ≥95%-prevalence byte signature.
- Edge (zh-CN short): a zh-CN short payload still passes `validate_zh_short_payload` (rel unchanged).

**Verification:** dofollow links ship byte-clean; non-dofollow links carry a stable opaque UTM frozen
at render; replay re-publishes identical bytes; tagged and clean forms dedup identically; footprint
gate stays green.

---

- [ ] **Unit 6: `attribute-traffic` GA4 verb (axis B)**

**Goal:** A cron-safe never-raises verb that pulls GA4 referral + UTM-campaign data per owned target,
joins to placed-link provenance, and writes `traffic.attributed` over a rolling window.

**Requirements:** R4, R5, R6.

**Dependencies:** Unit 2 (GA4 client), Unit 4 (kind + snapshot model), `utm.derive_token` (the
**function** — deepen: Unit 6 recomputes the token; it does **not** depend on Unit 5's publish
wiring, so the two are a fan-out off `derive_token`, not a chain). Full pipeline is GO-gated (Unit 3).
**Real-world latency (deepen):** Unit 6 returns **empty** until Unit 5 has been live long enough for
tagged links to publish and accrue sessions (weeks) — a data-latency dependency, not a code one;
state it so empty early output isn't read as a bug.

**Files:**
- Create: `src/backlink_publisher/cli/attribute_traffic.py`, `analytics/attribute_traffic.py`
  (join engine)
- Modify: `pyproject.toml` (`attribute-traffic` entry)
- Test: `tests/test_attribute_traffic.py`

**Approach:**
- For each owned target (config-mapped GA4 property): pull session source/medium + campaign over a
  configurable lookback (e.g. 30/90d, **not** all-time). Join UTM channels by **recomputing**
  `utm.derive_token(canonical_target, channel)` and matching the **session-scoped** campaign
  dimension that carries it (`sessionCampaignName` / `manualCampaignName` — the `utm_campaign`
  field, per Unit 5; **not** `utm_content`); dofollow channels join at the **source-domain** level via
  `sessionSource` (multiple dofollow links on a host collapse to one row). The join engine enumerates
  `(target, channel)` pairs from the **shared `build_target_buckets`** (channel lives only in
  history_store) before recomputing tokens; an empty history_store → `no_channels`/`unknown`, **never**
  persist zero-session rows that read as real zeros.
- **Honest granularity label** on every row (`per-link` vs `channel-level` — a floor key, Unit 4).
- **Persist as snapshot-per-run** (Unit 4 model): one immutable snapshot row per `(target, channel)`
  per run with `window_start/window_end/sessions/as_of`; the reader takes the latest snapshot,
  **never sums**. `metric_date`/window bounds are taken **verbatim from the GA4 response in the
  property's timezone**, never recomputed from a UTC clock (TZ-split double-count guard).
- **Sentinel honesty (deepen):** distinguish two **distinct persisted** values — `unattributable`
  (third-party target, structurally no GA4 property → row still **displayed**, never zero-green) vs
  `no_data` (owned target, GA4 returned zero this window). Collapsing them loses the keep/prune
  signal. Give the sentinel a stable identity so cron re-runs don't grow unbounded `unattributable`
  rows.
- **Zombie-metric guard (deepen):** every row carries an `as_of` freshness stamp; if an owned target
  is later un-mapped (GA4 property removed from config), latest-wins would otherwise serve the
  last-good number **forever** — the reader marks/drops rows whose `as_of` is older than N runs
  (the inverse of the decay case, where staleness *is* the signal; here staleness is a lie).
- Cron-safe: `flock` single-run, exit 0 advisory, cost cap + RG-kill stop-pay (R12 budget box),
  `--dry-run` offline. Sole owner of the GA4 pipeline (doc③ reads from here).

**Execution note:** Keep the join logic test-first; the fetch half (Unit 2) is already
external-delegate-friendly.

**Patterns to follow:** `cli/recheck_backlinks.py` (flock, network gating, never-raises, exit 0),
`recheck/events_io.py` (`emit_recheck` batched-`conn` + latest-wins), origin R4-R6.

**Test scenarios:**
- Happy path (UTM): a mocked GA4 row with a matching recomputed token → a `granularity="per-link"`
  snapshot with correct sessions.
- Happy path (dofollow): a referrer from a dofollow channel's host → a `granularity="channel-level"`
  row, two links on the same host collapsing into one.
- Edge (rolling window): two daily runs over overlapping windows → the reader reports the latest
  snapshot, **not** the sum (no ~30× inflation).
- Edge (third-party): a target with no GA4 property → an `unattributable` row that **appears** (not
  filtered, not zero), distinct from `no_data`.
- Edge (no data): owned target, GA4 returns nothing → a `no_data` row (≠ `unattributable`), exit 0.
- Edge (zombie): a previously-mapped target removed from config → its stale row is marked/dropped by
  the `as_of` staleness check, not served forever.
- Edge (TZ): a GA4 day boundary in the property's timezone is taken from the response, not recomputed
  from UTC (no split/merge double-count across midnight).
- Error path: GA4 auth/quota failure → facts empty, verb still exit 0; re-run does not double-write.
- Edge (budget): RG-kill — sufficient sample but signal≈0 across all channels → stop-pay triggers,
  reported.

**Verification:** Per owned target the operator gets per-channel real sessions with honest granularity
labels; rolling-window re-runs never inflate; `unattributable` ≠ `no_data`; stale rows don't zombie.

---

- [ ] **Unit 7: `attribute-discovery` GSC dual-path verb (axis C)**

**Goal:** Confirm whether Google discovered each backlink via two paths with distinct semantics,
writing `link.discovered` + publish→discovery latency.

**Requirements:** R7, R8 (R9 explicitly not done).

**Dependencies:** Units 2, 4, 3 (GO).

**Files:**
- Create: `src/backlink_publisher/cli/attribute_discovery.py`, `analytics/attribute_discovery.py`
- Modify: `pyproject.toml` (`attribute-discovery` entry)
- Test: `tests/test_attribute_discovery.py`

**Approach:**
- **Main path:** ingest the operator's GSC backlink-report CSV (Unit 2 parser) → per-channel
  discovery hits ("Google found N links from medium.com"); compute publish→discovery latency
  (per-link discovery latency). **Aux path:** URL Inspection API on owned target pages →
  target-page indexation / last-crawl (the lightweight `destination-decay` touch-in); record the two
  semantics **separately**.
- **fail-open `unknown`** (unmatched ≠ "not discovered"). **Pure string host-matching** (no remote
  fetch). Write `link.discovered`. **R9 per-link `site:` not built** (default off — ToS-gray sink,
  collides with doc② exclusion).
- Cron-safe / never-raises / exit 0 / cost-capped, same as Unit 6.

**Patterns to follow:** `cli/recheck_backlinks.py`, Unit 2 clients, origin R7-R9; the
`canary-targets` "advisory, no cron baked in" stance.

**Test scenarios:**
- Happy path (CSV): a backlink CSV with a known linking domain → per-channel discovery hit +
  `link.discovered` row + latency.
- Happy path (URL Inspection): an indexed target → `indexed` aux signal, distinctly labeled from the
  CSV path.
- Edge (fail-open): a placed link absent from the CSV → `unknown`, **not** "not discovered."
- Edge (string-match): host normalization (case/punycode) matches correctly; a path-only difference
  doesn't false-match; **zero** network fetch of any linking URL.
- Error path: CSV malformed / URL Inspection quota exceeded → partial result, flagged, exit 0.

**Verification:** The operator sees per-channel "registered in Google?" signal + discovery latency,
with the two path semantics never conflated and no remote URL ever fetched.

### Phase 1 (✅ BUILD NOW — the scorecard-MVP) — Scorecard Integration

> **Build-now scoping (Wave-0):** ship Unit 8 over **already-available signals only** — registry
> declared `dofollow`/`referral_value` + liveness (history/recheck) + the offline GSC backlink CSV
> *if already exported*. The GA4 referral (`traffic.attributed`) and auto-GSC discovery columns render
> **`axis inert / not landed`** until Phase 2 builds them — which the graceful-degradation design
> already handles natively. This delivers the per-channel keep/prune view today with **zero** external
> API surface, **zero** credentials, **zero** HMAC machinery.

- [x] **Unit 8: Unified per-channel scorecard (axis D)** — ✅ landed (Wave-0 MVP, branch `feat/per-channel-scorecard-mvp`): `scorecard/` engine re-keys the shared `build_target_buckets` by channel + reuses ledger `_classify`/`_link_liveness` (no drift); `channel-scorecard` JSONL verb + `/ce:health` card; GA4/GSC/AI axes inert; signal vector, advisory-only. Full suite green (7678).

**Goal:** A read-only per-channel view placing static declarations beside measured signals as a
**signal vector** (no composite), surfaced both as a JSONL CLI verb and a `/ce:health` card, with a
ranking + prune recommendation that the operator confirms.

**Requirements:** R10, R11, R12, R13.

**Dependencies:** Unit 4 (kinds), registry, ledger; consumes Units 6/7 (degrades gracefully if absent).

**Files:**
- Create: `src/backlink_publisher/cli/channel_scorecard.py`,
  `src/backlink_publisher/scorecard/engine.py` (`build_channel_scorecard` — re-keys the shared buckets
  by channel), `scorecard/model.py` (`ChannelScoreRow` — vector, no composite)
- Refactor: lift `build_target_buckets` out of `ledger/sources.py` into a shared pivot-agnostic
  `sources` module (it already attaches `platform` per `LinkRecord` — note `platform`/`channel` exists
  **only** in history_store, joined by canonical `live_url`; the events.db `articles` table has no
  platform column) — `build_ledger` (pivot=target) and `build_channel_scorecard` (pivot=channel)
  become two thin aggregators over the **same** buckets. **Refactor cautions (auto-fix):** the
  channel-tier classifier `_classify` lives in `ledger/aggregate.py:35` (not `sources.py`) — decide
  where it lands; **preserve the deliberate lazy `from webui_store import history_store` import** (an
  import-cycle guard); verify no new `scorecard → ledger → publishing.adapters` cycle; add a
  characterization test that `build_ledger` output is byte-identical before/after the lift
- Modify: `webui_app/routes/health.py` (add `_g_cache("channel_scorecard", …)`),
  `webui_app/templates/health.html` (new card), `pyproject.toml` (`channel-scorecard` entry) —
  **note (auto-fix):** `webui_app/` is **repo-root-relative**, not under `src/`; the new card imports
  the engine from the installed package (`from backlink_publisher.scorecard.engine import
  build_channel_scorecard`) and `_g_cache` from `webui_app/helpers/`
- Test: `tests/test_channel_scorecard_engine.py`, `tests/test_channel_scorecard_cli.py`,
  `tests/test_health_channel_scorecard_card.py`

**Approach:**
- **Drift-prevention (deepen, highest-value):** the engine **consumes the shared `build_target_buckets`
  join and re-keys it by channel** — it **must not** re-query events.db `articles`/`events` / history /
  anchor independently (a parallel reader will not replicate the ledger's orphan-history and
  "attempted-targets" subtleties and **will** drift; "reads only existing stores" was too loose). The
  shared source layer — not the parallel engine — is the anti-drift mechanism. Measured axes join in as
  new columns: liveness (history/recheck), source-indexability (doc②, if landed), AI-retrievability
  (doc③, if landed), GSC discovery (`link.discovered`), GA4 referral (`traffic.attributed`, snapshot-
  latest). **No re-computation of any upstream axis.**
- `ChannelScoreRow` = a **vector**: declared fields beside measured fields, each row showing
  **declared vs measured divergence**. **No composite magic-number** (D-B). **Graceful degradation:**
  a missing/RG-killed axis renders `axis inert / not landed`, never a lowered overall judgment.
- **Sample-honesty + RG-kill dual:** low-sample = `insufficient data` (not "zero value");
  sufficient-sample-but-signal≈0 = `measured empty → descope this axis`. Wording marks
  **correlation, not causation**.
- Output = per-channel ranking + downgrade/prune **recommendation**; **operator-confirm only, no
  auto-gating, publish path does not consume.** Surface (a) JSONL CLI verb (mirror `equity-ledger`)
  and (b) a `/ce:health` card (extend, not a 3rd dashboard — D-G).
- **Access control (auto-fix):** the card surfaces the operator's **real** per-channel attribution +
  owned-target identities. `/ce:health` is GET-only with no CSRF; confirm it is **loopback-only by
  default** and document that `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` exposes this sensitive data
  off-loopback (require a reverse-proxy/auth gate, or suppress the card, in that mode).
- **Honesty caveat (auto-fix):** GA4-attributed sessions are an **unauthenticated, replayable** signal
  — the opaque UTM ships plaintext on every tagged link, so an attacker can replay a campaign value to
  inflate a channel's measured referral (an integrity attack on keep/prune, sibling of homoglyph
  false-match). Label attributed counts as adversarially-inflatable (alongside sampling / UA-cloaking /
  attribution-model caveats); the monotone `link.discovered` (GSC) axis is the cross-check before any
  prune. "Realized value" is a signal, **not** ground truth.

**Patterns to follow:** `ledger/model.py` (vector-not-composite), `ledger/aggregate.py` (in-process
engine shared by CLI+WebUI), `cli/cull_channels.py:69-78` (read all registry accessors per platform),
`webui_app/routes/health.py` `_g_cache` card composition + `health_metrics.per_adapter`.

**Test scenarios:**
- Happy path: channels with mixed signals → one vector row each; declared `referral_value="high"` vs
  measured near-zero referral renders as a visible **divergence**.
- Edge (axis inert): doc②/doc③ not landed → those columns render `axis inert`, and the channel's
  other columns are unaffected (not dragged down).
- Edge (sample honesty): a low-sample channel → `insufficient data`, **not** zero/prune.
- Edge (RG-kill): a sufficient-sample channel with signal≈0 → `measured empty → descope axis`.
- Edge (no composite): the output contains **no** single blended score field (assert absence).
- Integration (parity): the CLI JSONL rows and the `/ce:health` card derive from the **same** engine
  (numbers match by construction); the health route stays GET-only and fail-open (never 500s).
- Integration (no drift): for the same data, the scorecard's per-channel link counts reconcile with
  `equity-ledger`'s per-target counts re-pivoted by channel (proves the shared-bucket reuse, not a
  divergent re-query).
- Edge (inert render): an attacker-controlled linking host in a channel row renders as escaped inert
  text in the card/CLI, never a clickable autolink.
- Edge (advisory): a prune recommendation never mutates the registry or any publish path.

**Verification:** From one view the operator can rank channels and spot keep/prune candidates with
declared-vs-measured divergence visible; missing axes degrade honestly; nothing auto-gates.

## System-Wide Impact

- **Interaction graph:** Unit 5 is the one write-side change in an otherwise read-only feature — the
  highest-blast-radius edit. The chokepoint is the **plan-stage URL-assembly seam** (where
  `ThreeUrlConfig` URLs are finalized before `_format_anchor_html`), **not** the publish-stage
  fresh/resume/draft emitters (resume/draft replay a stored payload). The `dofollow=True` clean-URL
  guarantee must hold byte-for-byte at render, and a UTM-tagged target must dedup identically to its
  clean form (`canonicalize_url` strips `utm_*`).
- **Error propagation:** every external call (GA4/GSC) is facts-vs-verdict separated — the client
  never raises, the verb applies the verdict and stays exit-0. A non-zero exit is reserved for an
  explicit opt-in `--fail-*` flag on deterministic failures only.
- **State lifecycle risks:** the two new kinds have **different** identities — `traffic.attributed` is
  additive (snapshot-per-run + read-latest-snapshot, reader never sums; `as_of` staleness guards the
  zombie-metric case), `link.discovered` is monotone (`(target,channel,linking_domain,source)`, no
  date bucket). `EventStore.append` has no dedup, so the **reader** (latest-wins, never-sums) + the
  Unit 6/7 `flock` carry idempotency. The WAL deadlock applies **only** under batched-`conn` inline
  quarantine → use the `emit_recheck` post-commit-flush shape.
- **API surface parity:** new console scripts (`attribute-probe`, `attribute-traffic`,
  `attribute-discovery`, `channel-scorecard`) join the CLI contract; the two new event kinds join the
  CI-gated `KINDS` vocabulary; new per-target config fields join the `save_config` round-trip
  surface. Each is a contract with its own gate/test.
- **Integration coverage:** CLI-verb output and the `/ce:health` card must derive from one in-process
  engine (the ledger CLI/WebUI parity pattern) so the numbers can't drift.
- **Unchanged invariants:** article generation, anchor strategy, and the render `rel` policy are
  **not** changed (the `noreferrer` strip is explicitly out of scope). `dofollow=True` link bytes are
  unchanged. The scorecard is read-only and never feeds the publish path. doc②/doc③ remain the sole
  owners of their axes — this plan only reads them.

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| dofollow channels' real clicks ≈ 0 + sparse GSC discovery → referral axis largely empty | High | High | **R0 probe gates this before building** (Unit 3); discovery-latency is the more reliable "registered in Google" signal; referral framed as meaningful mainly for nofollow/high-exposure channels. |
| Most placed links point at **third-party** targets → both new axes inert for most rows | Med | High | R0 measures owned-target share explicitly; honest `unattributable` (≠`no_data`) labeling; scorecard degrades inert axes instead of mis-scoring. |
| **Rolling-window double-count** — daily cron over a 30-day window inflates sessions ~30× | Med | High | **Snapshot-per-run + read-latest-snapshot**; reader **never sums raw rows**; `metric_date` from GA4 response in property TZ (Unit 4/6). |
| **Zombie metric** — an un-mapped target's last-good number served forever under latest-wins | Med | Med | Per-row `as_of` stamp; reader marks/drops rows older than N runs (Unit 6). |
| Scorecard becomes a **second source of truth** that drifts from the ledger | Med | Med | Engine **consumes the shared `build_target_buckets`** and re-keys by channel; a no-drift reconciliation test vs `equity-ledger` (Unit 8). |
| UTM **dropped/altered on replay** (resume/draft re-publish a stored payload) | Med | High | Freeze UTM into the payload at the **plan-stage render seam**; replay re-publishes identical bytes; one chokepoint; tested (Unit 5). |
| UTM-tagged URL **double-publishes** if dedup key diverges from clean form | Low | High | `canonicalize_url` strips `utm_*`; a regression test pins "tagged & clean → same `DedupKey`" (Unit 5). |
| Constant UTM marker becomes a cross-page **footprint signature** | Med | Med | Vary UTM across footprint dimensions; assert no ≥95% prevalence with the `footprint` extractor. |
| **HMAC secret rotation** silently zeroes historical attribution under never-raises | Low | Med | Secret **non-rotating by design**, documented; shared across publish+read; (or multi-secret read) (D-D/Unit 1). |
| **Credential leak** — refresh token `1//…` / service-account key / property ID bypass the scrubber | Med | High | Service-account read-only key; `0o600`+atomic-write; add `1//…` scrubber pattern; **raw response never persisted** (tested: row = floor keys only); never set `OAUTHLIB_INSECURE_TRANSPORT`; one mutation lock + Barrier test; named rotation owner. |
| **PII** persisted — query/fragment **and** userinfo (`user:pass@`) **and** path **and** attacker-set campaign names | Med | High | Strip query+fragment **+ userinfo**; reduce to **host** where possible (tested invariant); cap+scrub+escape **every** GA4/GSC string value, not just URLs; parameterized SQL. |
| **SSRF (fetch)** via following a remote GSC/referrer URL | Low | High | Parse + IDNA codec, **never fetch/resolve/redirect**; reject non-http(s); reuse `_util/url.py`; socket-blocked default tests. |
| **Operator-proxy SSRF / link injection / phishing** — attacker-controlled host rendered into a report a human opens | Med | Med | Render remote-derived strings as **inert escaped text, never Markdown autolinks**; reports written outside the committable tree (Unit 2/3/8). |
| **CSV / formula injection** — attacker-controlled linking domain executes when an export is opened in a spreadsheet | Med | Med | Neutralize leading `= + - @ \t \r` on any CSV/TSV/spreadsheet cell (Unit 2). |
| **GSC-CSV parse DoS** — oversized/pathological import OOMs the host, hidden by never-raises | Med | Med | Hard max-file-size + max-row cap (reject-with-reason); streaming parse; no decompression (Unit 2). |
| **`/ce:health` card exposes real attribution off-loopback** (GET-only, no CSRF/auth) | Low | High | Loopback-only by default; document `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` requires a reverse-proxy/auth gate or suppresses the card (Unit 8). |
| **UTM replay/forgery** — plaintext campaign value replayed to inflate a channel's measured referral | Med | Med | Label attributed sessions as unauthenticated/replayable; cross-check against monotone GSC `link.discovered` before prune (Unit 8). |
| **Homoglyph false-match** inflates a channel's scorecard (integrity attack on keep/prune) | Low | Med | Match on the IDNA-encoded (`xn--`) form of both sides; split false-miss/false-match tests (Unit 2). |
| **Probe/discovery report leaks** real domains + GA4 property / GSC site IDs (match no scrubber pattern) | Med | High | Write reports **outside the committable tree** (config-dir / gitignored); CI guard the path; **not** the placeholder rule (Unit 3). |
| **WAL nested-connection deadlock** — only if batching appends under one `conn` and quarantining inline | Low | Med | Adopt the `emit_recheck` batched-`conn` + `pending_quarantines` + post-commit flush; test a mid-batch floor-miss (Unit 4). |
| Cron job **burns API quota forever** in a "never enough data" state | Med | Med | RG-kill stop-loss + hard cost cap + explicit stop-pay condition (R12/R14). |
| New events.db kinds **break a CI gate** (R2/R8a/R8b/R9) | Med | Low | Test-first clone of `test_citation_event_append.py`; add the writer to `WRITER_MODULES`; floors declared with the kinds (and `target_url` kept **out** of the floor). |
| New per-target config field **dropped on WebUI save** (`save_config` `[targets.*]` caveat) | Med | Med | Wire writer regeneration loop + a round-trip test first (Unit 1). |

## Documentation / Operational Notes

- Add an operations runbook for the GSC backlink-CSV semi-auto import (the operator's manual export
  cadence) and the credential rotation owner/cadence (mirror `recheck-backlinks-runbook.md`).
- All new verbs are **externally scheduled** (no in-repo scheduler) with `flock`; document the cron
  invocation and the cost-cap / stop-pay knobs.
- **Two distinct leak controls (deepen):** *plan/solution/doc text* uses **placeholder domains** + the
  private-token `rg` gate over the diff before commit (self-doc-leak lesson). *Generated probe /
  scorecard reports* contain the operator's **real** domains + GA4 property / GSC site IDs **by
  necessity** (and those IDs match no scrubber pattern) → they are written **outside the committable
  tree** (config-dir or a `.gitignore`d path), **never** committed; a CI guard asserts the path is
  ignored.
- Honesty labeling: "realized value" is an **attribution signal, not ground truth** (sampling,
  attribution model, UA cloaking) — state the blindnesses in every report, advisory-only.

## Alternative Approaches Considered

- **Composite per-channel score (single number).** Rejected (D-B): doc⑤ already vetoed a composite
  equity index; across heterogeneous axes it is dominated by whichever axis has data and erodes
  diagnostic trust. Deferred — must first rebut doc⑤.
- **Full GA4 pipeline up front (skip the R0 gate).** Rejected (D-A): dofollow channels likely have
  near-zero real clicks; building the pipeline before measuring signal density risks large wasted
  engineering. The probe is cheap and may descope half the feature.
- **GSC backlinks via API.** Impossible — no backlinks API exists; the Web-UI Links section is not
  exposed by any Search Console API. Forced to the semi-auto CSV main path (D-E).
- **True per-link UTM.** An over-constrained triangle: per-link + recompute-only + no-stored-map — pick
  two. Reachable by persisting the render-stage per-article seed (`_work_themed.py:200`, which already
  exists at render time) into a stored `seed → source_url` map, at the cost of the no-stored-map
  security posture. A **policy** choice, not a technical impossibility; default is
  per-`(channel,target,run)`, which is the granularity keep/prune is made at.
- **New third dashboard for the scorecard.** Rejected (D-G): extend `/ce:health` + a JSONL verb to
  avoid a third same-source surface.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-01-per-channel-realized-value-scorecard-requirements.md](../brainstorms/2026-06-01-per-channel-realized-value-scorecard-requirements.md)
- Sibling axes (consumed): `2026-06-01-seo-outcome-indexability-loop-requirements.md` (②),
  `2026-06-01-geo-ai-traffic-channel-retrievability-requirements.md` (③),
  `2026-05-25-backlink-equity-ledger-requirements.md` (⑤),
  `2026-05-29-backlink-lifecycle-closed-loop-requirements.md` (① liveness).
- Related code: `events/kinds.py`, `events/store.py`, `ledger/{model,aggregate,sources}.py`,
  `publishing/registry.py`, `cli/{equity_ledger,recheck_backlinks,cull_channels}.py`,
  `config/{types,parsers/target,loader,writer}.py`, `_util/markdown.py`, `content/themed_gen.py`,
  `webui_app/routes/health.py`, `webui_app/health_metrics.py`.
- Related learnings: `docs/solutions/best-practices/{no-runtime-llm,geo-probe-authorized-llm-exception,credential-rotation-tests-cover-bootstrap-race,self-doc-sanitization-leak-recurrence}-*.md`,
  `docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md`,
  `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`,
  `docs/operations/recheck-backlinks-runbook.md`.
- External docs: [GA4 Data API schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema),
  [Search Console API limits](https://developers.google.com/webmaster-tools/limits),
  [URL Inspection API](https://developers.google.com/search/blog/2022/01/url-inspection-api).
