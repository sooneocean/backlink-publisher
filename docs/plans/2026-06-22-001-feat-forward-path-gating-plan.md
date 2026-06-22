---
title: "feat: Forward-path drift gating (publish-time nofollow suppression)"
type: feat
status: parked
date: 2026-06-22
origin: docs/plans/2026-05-27-006-feat-canary-publish-path-validation-plan.md
claims:
  paths:
    - src/backlink_publisher/cli/_publish_banner.py
    - src/backlink_publisher/canary/store.py
    - src/backlink_publisher/publishing/_manifest_types.py
    - docs/ideation/gate-verdicts.md
  shas:
    - 01cd29d
---

# feat: Forward-path drift gating (publish-time nofollow suppression)

> **Status: PARKED behind a falsification gate (R16).** This plan documents the
> *follow-up* gating phase that Plan 2026-05-27-006 explicitly deferred
> ("Gating (suppress nofollow posts) … deferred to a follow-up plan"). Per the
> gate-first governance rule (consol. R16, `docs/ideation/gate-verdicts.md`),
> **no build phase below may begin until Phase 0's falsification gate returns
> `GO`.** The advisory forward-path *recording* (Plan 2026-05-27-006 Units 1–3)
> already ships and is the data source the gate reads. This plan is the gate +
> the gated design — not an authorization to build.

## Overview

Today the publish path has two **disjoint** canary signals (see AGENTS.md
"Publish-path forward-path drift"):

- **Evergreen decay** (`canary-targets`): re-fetches *old* seeded canary posts to
  detect a platform retroactively rewriting live links to nofollow.
- **Forward-path drift** (`_record_publish_path`, Plan 2026-05-27-006): after each
  publish (fresh **and** `--resume`), reads the adapter's already-computed
  `link_attr_verification` and records a per-platform `link-alive`/`drift` verdict
  to the `_publish_path` sibling stream in `canary-health.json`. Surfaced as a
  `/ce:health` card + an epilogue `publish_path_drift` count.

Both are **advisory-only in v1** — they never gate publishing, never change exit
codes. "Forward-path gating" is the proposed Phase 2: let the publish path
**suppress / skip** a post (or a whole platform) when newly published posts are
already being injected with `rel=nofollow` or stripped — i.e. enforce the
`register(..., dofollow=True)` contract on *new* posts instead of merely logging
its violation.

## Why this is gated (the honest blocker)

This is a *publishing-behaviour machine*, not a pure-read refactor, so R16
applies. The neighbouring premises in the gate ledger were **explicitly
killed/parked as "unjustified at this scale"**:

| gate | premise | verdict |
|---|---|---|
| G2 | operator money pages silently decay at a build-justifying rate | INCONCLUSIVE (n=3; on-demand probe suffices — machine UNJUSTIFIED) |
| G3 | render paths preserve `referer` for referral attribution | KILL |
| G5 | footprint fingerprint dimensions survive into the crawled DOM | INCONCLUSIVE-terminal (re-fetch saturated) |

The forward-path **advisory recording exists precisely to gather the evidence**
for whether new-post drift is frequent enough to justify a *gate* rather than a
*log line*. Building the gate before that evidence exists would repeat the exact
anti-pattern this ledger was created to stop. The current events/canary corpus
is **test-data-dominated** (~5/14 readable per G1/G5 notes), so the evidence
almost certainly does not exist yet on this installation.

## Phase 0 — Falsification gate (ENTRY CONDITION, must return `GO`)

**Premise to falsify:** *Newly published posts are injected with nofollow /
stripped at a rate, and on platforms, where deterministic publish-time
suppression would prevent meaningful dofollow-equity loss that the existing
advisory signal + manual re-bind does not already cover.*

- **Evidence source (read-only, zero new network):** the already-recorded
  `_publish_path` stream (`canary.store.list_publish_path_all()`) plus
  `link.rechecked` / publish events in `events.db`. No new probe is required —
  the advisory machine is the instrument.
- **Verdict protocol (four states, per the ledger):** first run is a
  **calibration pass** → `INCONCLUSIVE` by construction; record the threshold and
  rerun. `KILL` if forward-path drift is ~absent on real (non-test) channels
  (advisory log already suffices). `GO` only with a confirmed evidence sample of
  build-justifying drift on ≥1 real dofollow-tier platform.
- **Threshold (to calibrate, not pre-decided):** candidate = "≥ X% of new posts
  on a real dofollow platform recorded `drift` over a sample of ≥ N publishes."
  X and N are read off the first calibration sample, then pinned in
  `gate-verdicts.md`.
- **Evidence hygiene:** aggregate / host-stripped reason counts only — never raw
  operator money-page URLs (the no-operator-domain rule applies to
  `docs/ideation/`).
- **Output:** one hand-curated row in `docs/ideation/gate-verdicts.md`
  (`GO`/`KILL`/`INCONCLUSIVE`). A `KILL` permanently parks Phases 1–3 below.

**No Phase below starts until this row reads `GO`.**

## Phase 1 — Opt-in, default-OFF Policy lever (BLOCKED until Phase 0 = GO)

Add a declarative, **default-OFF** policy flag to the manifest so the capability
exists without changing any default publish behaviour:

- `Policy.suppress_on_forward_path_drift: bool = False` in
  `publishing/_manifest_types.py`. Default `False` preserves the v1
  "never gate publishing" contract for every existing platform.
- A pre-publish check in the dispatch / publish loop: when the active platform's
  policy flag is `True` **and** `canary.store.is_publish_path_degraded(platform)`
  (the existing debounced ≥ `QUARANTINE_AFTER_N` consecutive-drift signal — *not*
  a single transient drift), skip the publish with a first-class
  `publish.suppressed_forward_path_drift` checkpoint + event, and continue with
  the next row (never a hard crash, never a new exit code unless explicitly
  spec'd in Phase 3).
- Reuse the **existing debounce** (`is_publish_path_degraded`) so a single noisy
  verdict can never suppress — symmetry with the evergreen quarantine/re-arm.

## Phase 2 — Coverage for fetch/SSRF-sensitive platforms (BLOCKED)

Plan 2026-05-27-006 notes blogger/ghpages/telegraph "need extra fetch/SSRF
handling" for forward-path verification. Phase 2 extends `_record_publish_path`'s
`link_attr_verification` intake to those platforms behind the project's
`net_safety` SSRF guard, so their forward-path verdicts are trustworthy before
any of them opts into the Phase 1 lever.

## Phase 3 — (optional, separately gated) exit-code semantics

Only if an operator wants CI/cron to *fail* on suppression: add a
`--fail-on-forward-path-drift` flag mirroring `recheck-backlinks --fail-on-dead`
(advisory exit 0 by default). Deferred; do not build with Phase 1.

## Non-goals

- Changing the **default** publish behaviour for any platform (Phase 1 is opt-in).
- Gating on **evergreen** decay (that is `canary-targets`' domain, separately).
- Re-opening the G3 `referer`/referral KILL — orthogonal premise.

## Risks

- **False suppression**: a cloaked or transient nofollow could skip a good post.
  Mitigated by reusing the debounced `is_publish_path_degraded` (≥ N consecutive),
  never a single verdict, and by default-OFF opt-in.
- **Evidence never materialises**: if Phase 0 stays `INCONCLUSIVE`/`KILL` on a
  real corpus, this plan is correctly parked indefinitely — that is a *success*
  of the gate, not a failure of the plan.

## References

- `docs/plans/2026-05-27-006-feat-canary-publish-path-validation-plan.md` (the
  advisory recording this gates on).
- `docs/ideation/gate-verdicts.md` (R16 governance + the gate row this plan owes).
- `src/backlink_publisher/cli/_publish_banner.py::_record_publish_path` (recorder).
- `src/backlink_publisher/canary/store.py` (`record_publish_path_verdict`,
  `get_publish_path_health`, `is_publish_path_degraded`, `list_publish_path_all`).
- `src/backlink_publisher/publishing/_manifest_types.py::Policy` (the opt-in lever site).
