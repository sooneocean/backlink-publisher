---
date: 2026-06-01
topic: gate-verdicts
type: synthesis
status: active
plan: docs/plans/2026-06-01-005-feat-gate-first-validation-and-deficit-overlay-plan.md
---

# Phase-0 Falsification-Gate Verdict Ledger

> **Soft-fold note.** This is the interim single decision surface for the Phase-0
> gate verdicts. When the architecture-suite A2 ledger (`docs/ideation/SYNTHESIS.md`,
> plan 2026-06-01-003) ships, the owner of that plan folds these rows into its
> **Tried / Open** tables and leaves a back-reference here. Until then this file
> is authoritative (consol. R3 — reuse the ideation ledger, don't build a new
> file system; the soft-fold honors that without a hard dependency on unshipped A2).

## Governance rule (consol. R16) — read before adding any build-out plan

> **A "build a Phase 1–N machine" brainstorm may not enter `/ce:plan` until its
> cheap falsification gate returns `GO`.** Pure-read detection / probes / refactors
> are exempt. A `KILL` permanently parks the downstream Program stage; an
> `INCONCLUSIVE` must resample (never default to GO); a `BLOCKED` (Tier-2
> credentials unavailable) parks the stage until credentials exist.

## ⛔ KILLED premises (do not revive)

| Premise | Why killed | Date |
|---|---|---|
| `entropy-budget-footprint-diversification` | Operator `<a>` bytes never reach the crawled page — footprint diversification measures bytes a crawler never sees. Premise falsified *after* drafting; the exact cost of a missing front gate. | 2026-06-01 |

## Verdict protocol

- **Four states:** `GO` (premise validated → downstream build unblocked) · `KILL`
  (premise false → don't build) · `INCONCLUSIVE` (can't confirm → resample;
  `terminal` when the premise is structurally unverifiable, e.g. G5 re-fetch
  saturation) · `BLOCKED` (Tier-2 credentials unavailable → stage parked).
- **First run per gate is a calibration pass** → `INCONCLUSIVE` by construction
  (a verdict needs a threshold, and the threshold is read off the first sample).
  Record the threshold + its rationale in the row, then rerun to reach GO/KILL.
- **No GO without a confirmed evidence sample.** Evidence cells carry aggregate
  rates / host-stripped reason counts — **never raw operator money-page URLs**
  (the no-operator-domain rule applies to `docs/ideation/`, not only
  `docs/solutions/`).

## Gate verdicts

| gate | tier | premise | verdict | rate / evidence | sample-n | date | downstream-blocked |
|---|---|---|---|---|---|---|---|
| G1 | T1 | source/host pages carrying our backlinks go noindex/blocked at a "false-success" rate | INCONCLUSIVE | detection shipped (plan 002 completed); verdict needs a live `recheck-backlinks` over a real corpus (writes events.db); current corpus test-dominated + saturated (cf. G5 5/14) → unmeasurable | — | 2026-06-02 | seo-indexability build-out — pending a non-test published corpus; not run here (avoids events.db mutation); resample when corpus is real |
| G2 | T1 | the operator's own money pages silently decay (noindex/4xx/soft-404/off-host) at a build-justifying rate | INCONCLUSIVE | decay 1/3=0.33 (calib, no thr); readable 3/3; lone failure=transient http_503; n=3 too small | 3 | 2026-06-01 | destination-decay machine (D1/D2) — UNJUSTIFIED at n=3 (on-demand probe suffices); resample when universe grows OR a definitive noindex/404 decay appears |
| G3 | T2 | any channel ever delivers a real referral session; render paths preserve `referer` | KILL | strip 1/2 = 0.50 (thr 0.50); preserving=work_themed; referral=absent | 2 | 2026-06-01 | Program B (GA4 referral attribution) PARKED |
| G4 | T2 | adult-site channel articles are surfaced/cited by AI engines (RG-kill) | BLOCKED | probe-citations CLI deferred (geo-ai-citation plan status:parked, U5–U8); no AI-citation tooling/creds available | — | 2026-06-02 | GEO machine PARKED (cf. geo-ai-citation plan decision); resume trigger = AI-citation corpus volume non-trivial |
| G5 | T1 | footprint's pre-publish fingerprint dimensions survive into the crawled live DOM | INCONCLUSIVE (terminal) | survival 0% terminal; readable 5/14=0.36 < 0.50 floor; rel_survived 0/5 (anchor_stripped=4, rel_rewritten=1) | 14 | 2026-06-01 | orchestrator footprint-gate (Phase 1b) — argues-against-build (premise unverifiable by re-fetch; cf. entropy-budget); terminal, do not resample |

<!-- Rows are filled by hand-curating each `gate-probe` run's JSONL verdict
     (Unit 5). G1/G4 verdicts are transcribed from plans 002/004, not machine-read. -->

## Recorded thresholds & rationale

### G3 — `strip_threshold = 0.50` → KILL (2026-06-01)

- **Measured (calibration pass):** `gate-probe --gate g3` → `strip_referer = 1/2` (0.50), `sample_n=2`.
  The two render paths through `_format_anchor_html` are `render_zh_short_article`
  (default `rel="noopener noreferrer"` → strips) and `themed_gen` main/list/work
  (`rel="noopener"` → preserves). Verified against the live call sites: there is **no** separate
  long-form `render_*` anchor path (the earlier plan note to that effect was stale).
- **Threshold rationale (0.50):** the stripping path `render_zh_short_article` is the **primary
  backlink-article renderer** (1 main + 1–2 secondary backlinks per article); the preserving path is
  the secondary themed-content one. So GA4 channel→money-page **referral attribution is structurally
  blind for the bulk of published backlinks**, regardless of any GA4/GSC setup — the static audit
  alone is decisive (no Tier-2 credentials needed).
- **Corroboration:** the owned money-page universe is tiny, so there is no referral-attribution corpus
  to begin with (operator-side signal). Two independent reasons to park Program B.
- **Scope of the KILL:** this kills the **GA4-referral-attribution build-out under the current render
  paths**. It does **not** decide the separately-deferred question "change the render path to preserve
  `referer` vs. degrade to `unattributable`". Adopting a referer-preserving `rel` on the backlink path
  would re-open G3 → rerun `gate-probe --gate g3` to recalibrate.
- **Reproduce:** `gate-probe --gate g3` then `gate-probe --gate g3 --strip-threshold 0.5`.

### G2 — money-page decay calibration → INCONCLUSIVE (2026-06-01)

- **Measured (calibration, no threshold):** `gate-probe --gate g2` → decay 1/3 (0.33), `sample_n=3`,
  all 3 readable; the single failure is a **transient `http_503`** (not a definitive noindex/4xx/soft-404).
- **Verdict rationale:** stays INCONCLUSIVE — `n=3` is below any meaningful sample floor and the lone
  "decay" is a transient server error. Forcing GO/KILL on this calibration run would violate the
  "partial/transient sample ≠ confident verdict" discipline (consol. R11).
- **Build implication:** the destination-decay **machine** (persist receipts to an events.db KIND +
  LedgerRow field + trend; former D1/D2) is **not justified at this scale** — the zero-cost on-demand
  `gate-probe --gate g2` already covers a 3-URL owned universe. **Resample trigger:** the owned money-page
  universe grows materially, **or** a definitive (noindex/404/soft-404/off-host) decay appears.
- **Operational note (act now, separate from the gate):** one money page is currently returning
  `http_503` — worth a manual check; backlinks pointing at it yield nothing while it is down.

### G5 — footprint survival → INCONCLUSIVE-unmeasurable (terminal, 2026-06-01)

- **Measured (calibration):** `gate-probe --gate g5` → re-fetch readable **5/14 (0.36) < 0.50 saturation
  floor** → **terminal** INCONCLUSIVE-`unmeasurable`. Among the 5 readable, `rel_survived=0`
  (`anchor_stripped=4`, `rel_rewritten=1`); failure reasons network_error=6, unreachable=2, invalid_url=1.
  (events.db is test-data-dominated; only a few links are real channels.)
- **Verdict rationale:** the footprint-gate premise — *do pre-publish fingerprint dimensions survive into
  the crawled live DOM?* — is **unverifiable by canary re-fetch**: most published-page hosts are
  anti-bot / unreachable to the verifier UA. Terminal (do **not** resample) by the saturation-floor protocol.
- **Build implication:** an unmeasurable premise **argues against building** the orchestrator
  footprint-gate (Phase 1b) — the same conclusion entropy-budget reached, by a different route. The signal
  that *did* come through (`rel_survived=0/5`) points the same way. Treat Phase-1b footprint-gate as parked.

### G1 — source-page indexability → INCONCLUSIVE (2026-06-02)

- **State:** the detection shipped — plan `2026-06-01-002-source-indexability-detection` is `completed`
  (`recheck/indexability.py` adds `indexability ∈ ok|blocked|unknown` as orthogonal metadata on the
  recheck probe, never changing the liveness verdict).
- **Why INCONCLUSIVE (not run here):** producing the verdict requires a live `recheck-backlinks` pass
  over a **real published corpus** — which **writes `link.rechecked` events to events.db** (not a pure
  read-only probe like `gate-probe`). Mutating events.db while the recheck→deficit-overlay→re-plan loop
  is live is a side effect not worth triggering for a gate calibration. At the current corpus (the same
  test-data-dominated, ~5/14-readable set G5 saw) the `unknown` rate would be near-total → unmeasurable,
  which plan 002's own Phase-0 GO/NO-GO criterion already flags as "do not build yet."
- **Resample trigger:** a non-test published corpus of real channel pages exists; then run
  `BACKLINK_PUBLISHER_CONFIG_DIR=<prod> recheck-backlinks …` and read the `blocked` / `unknown` rates.

### G4 — GEO / AI-citation → BLOCKED (2026-06-02)

- **State:** the `probe-citations` CLI that would emit G4's verdict is **deferred** — the
  `2026-05-29-006-geo-ai-citation-closed-loop` plan is `status: parked` (U1–U4 shipped #331; U5–U8
  deferred behind an internal credit-gate → `probe-citations` dependency).
- **Why BLOCKED (not INCONCLUSIVE):** G4 is Tier-2 and its probe tooling/credentials do not exist yet, so
  per the four-state protocol the gate is `BLOCKED` (parked), not a resample-able INCONCLUSIVE. The GEO
  machine stays parked — this transcribes the geo plan's *existing* PARK decision, it is not a new kill.
- **Resume trigger (from the geo plan):** backlink/AI-citation corpus volume becomes non-trivial
  (attribution has no signal at current owned-target volume). Resumes at the geo plan, not a re-brainstorm.
