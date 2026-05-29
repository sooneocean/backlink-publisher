---
date: 2026-05-29
topic: round12-fresh-pass
focus: open-ended (fresh pass)
---

# Ideation: Round 12 Fresh Pass

## Codebase Context

**Project shape**: Python 3.11/3.12 SEO backlink automation. 8 CLI entrypoints over a JSONL
Unix-pipe pipeline: `plan-backlinks → validate-backlinks → publish-backlinks` + `report-anchors`,
`equity-ledger`, `footprint`, `phase0-seal`, `generate-backlink-text`. Flask WebUI (~20 route
modules + 5 services + `webui_store/` JSON singletons + `events.db` SQLite). ~30 publisher adapters
via a manifest-driven registry (UiMeta/BindDescriptor/Policy; `register()` auto-wires CLI+schema+WebUI).
~32.7k SLOC, ~3300 tests. 20+ `bp-*/` git worktrees.

**Hard constraints honored**: no runtime LLM in the plan/validate/publish/anchor/content kernel
(only the opt-in human-reviewed `generate-backlink-text`); pure plan/validate engines (network only
via opt-in flags / standalone diagnostic verbs); no nofollow platform additions (a nofollow channel
is negative value); new UI = sibling pages, not monolith retrofits; no big-bang rewrites of the
mature B+ JSONL pipeline. Signature bug class = **silent failure / false-success** — ideas that
*close* such a gap score well; ideas that *add* a swallow path are worthless.

**Excluded (shipped / queued / in-flight, do not duplicate)**: events.db authoritative state (R10 #1,
plan-007 in flight); Manifest Full-Fidelity (R10 #2); Manifest Drift-Proof property test (R11 #2);
Run Inbox (R11 #3); Silent-Failure Divergence Sentinel (R11 #4); recheck-backlinks survival monitoring
+ closed-loop backlink lifecycle (in brainstorm, `2026-05-29-backlink-lifecycle-closed-loop-requirements.md`);
plan-claims→coverage gate; unified health JSON; worktree turf gate; per-adapter dry-run fidelity;
config snapshot secret scrub; equity ledger; events projector dedup; real-publish verification;
retry+backoff; OAuth preflight; dofollow canary closeout (gate deliberately rejected for runbook);
checkpoint/resume; Dual-State Divergence Auditor (#238); exit-code 0–6 contract (already locked by
`test_exit_code_contract.py` + `test_cli_exit_code_literals.py` + `test_cli_exit_code_contract.py`).

**Method**: 5 ideation frames (operator-pain / unmet-capability / inversion-automation /
assumption-breaking / leverage-edge) × ~8 raw candidates → 40 raw → merge/dedupe + 4 cross-frame
syntheses → 30 unique candidates → 2-critic adversarial pass (overlap/redundancy + groundedness/value,
both verifying strongest claims against real code) → **6 survivors**. Two candidates were killed as
verified code-misreads; five were killed for overlap with shipped/in-flight work or out-of-scope
product expansion.

## Ranked Ideas

### 1. Language-gate correctness harness — NFC parity + calibrated ko threshold ⭐ Synthesis (#7 + #25)
**Description:** Insert NFC normalization at the entry of the *active* validate-time anchor-language
gate (`anchor/lang.py` `check_anchor_language` / `_has_hangul`), and share one normalize-then-check
contract across all three script-checkers (`anchor/lang.py`, `anchor/resolver.py`, `linkcheck/language.py`)
with a parity property test. Fold in a checked-in golden corpus (ko/zh/Hanja-mixed shapes) that pins
the currently-uncalibrated `_MIN_KO_HANGUL_RATIO = 0.30` (`resolver.py:100`, `linkcheck/language.py:65`,
both carry an open `TODO(ko-corpus-calibration)`).
**Rationale:** `anchor/lang.py` — the gate that *actually runs at validate time* — does **no** Unicode
normalization, while `resolver.py:142` normalizes NFC. macOS pastes Hangul as NFD (decomposed Jamo,
U+1100–U+11FF), which falls outside the U+AC00–U+D7AF syllable range the gate checks. A real Korean
operator pasting a valid anchor gets it **silently rejected** as "missing Hangul" (false-failure), or
worse, an NFD anchor passes the resolver but the gate disagrees — a cross-module divergence. One shared
normalize boundary closes the gap for every current and future language; the calibrated corpus turns a
magic constant guarding a silent fallthrough into a permanent regression guard (and de-risks future
ja/zh-TW additions). Cheapest, highest-confidence closer in the set.
**Downsides:** Calibration needs a small judgment call on real corpora; must keep the three checkers
genuinely sharing one boundary (not three copies).
**Confidence:** 90% → retracted
**Complexity:** Low
**Status:** Superseded — brainstorm 2026-05-29 + document-review found the core bug **already fixed** in
Unit 6: `cli/_validate_payload._nfc_normalize_in_place` (`:242`) NFC-normalizes content + every
`link["anchor"]` before the gate (`:275`) and detector (`:256`); regression test
`test_validate_backlinks.py:561` (`test_nfd_ko_anchor_normalized_and_passes`) already passes. The
function-level reads (lang.py/resolver.py don't normalize) were true, but the production-reachability
claim was false (the caller normalizes first). Residual real-but-narrow gap: `branded_pool` /
`anchor_keywords` are NOT NFC-normalized at config-load (no NFC anywhere in `config/`) — see brainstorm doc.

### 2. Load-bearing live-link verdict at publish (advisory → first-class, config-gated)
**Description:** Promote the per-post link verdict that `_publish_helpers.py` *already computes* at
publish time — `target_missing` / `target_nofollow` / `target_rewritten` from
`verify_link_attributes` — from advisory-log-only to a first-class publish outcome (e.g.
`status="published_unverified"` or a non-zero exit), gated behind a config flag so the default stays
back-compat. Scoped strictly to the **synchronous at-publish verification**: explicitly *not* the
over-time recheck (closed-loop lifecycle owns that) and *not* a per-link rel column in the ledger
(brainstorm D4 deliberately declined the composite).
**Rationale:** This is the project's signature bug class in its purest, most-grounded form:
`_publish_helpers.py:585` literally reads "Advisory only: never raises, never changes exit code" —
the post's own required dofollow backlink can be missing or nofollowed on the live page and the run
still exits success. The detection is **already paid for**; the only missing piece is making the
verdict load-bearing. Highest leverage on the explicitly-named "honesty of the success signal" theme.
**Downsides:** Some platforms inject nofollow on new accounts by policy — must stay config-gated so it
doesn't become a default false-failure. Must not drift into the closed-loop recheck's territory.
**Confidence:** 82% → retracted
**Complexity:** Low-Medium
**Status:** Superseded — grounding (2026-05-29) found this **substantially already built**. `is_drift`
includes `target_missing` (`_publish_helpers.py:598`); `_record_publish_path` records the verdict to the
canary forward-path stream (`:608`), WARN-logs offending URLs (`:619`), and the count feeds the summary.
Consecutive confirmed drifts → platform **quarantined** (debounced, `canary/store.py:56`) →
`is_quarantined` + `hard_skip` config gates future publishes (`_publish_helpers.py:166-173`). The verdict
is NOT inert. Only residual: per-row *immediate* exit/status effect for the current run — which runs
against the project's deliberate debounce-don't-overreact design. The "advisory only: never raises"
docstring (`:585`) is leaf-true but the consumer layer makes it load-bearing.

### 3. Per-row reachability failure attribution in validate
**Description:** Change `validate/engine.py`'s strict reachability path so a network failure in the
pooled URL set is attributed back to the specific row(s)/URL(s) that own it and surfaced as per-row
errors, instead of raising one opaque `ExternalServiceError` that exits 4 and aborts the entire batch.
**Rationale:** `validate_rows` pools **every** URL across **all** rows into one set, then calls
`check_urls_strict` once (`engine.py:119–145`). Under power-user scale (hundreds of targets), a single
dead link or one flaky host raises `ExternalServiceError` → exit-4 → the whole run aborts with no
indication of which of 300 rows is the culprit, and the per-row partial-success design (which already
works for the non-network validation case right below it) is defeated for the network case. Attribution
turns a batch-killer into a targeted, actionable per-row signal and lets the operator tell a real dead
backlink from transient flakiness.
**Downsides:** Network is opt-in in validate (`--no-validate-url-check` boundary) — must preserve
engine purity; attribution must map pooled results back without re-fetching.
**Confidence:** 80% → downgraded
**Complexity:** Medium
**Status:** PARTIAL (integration sweep 2026-05-29) — per-row reachability attribution **already exists at
publish time** (`_publish_helpers.py:93-127` `_check_row_reachability`, default-on, per-row skip with the
offending URL). The validate-time all-or-nothing abort is a *deliberately preserved* contract (plan
2026-05-14-001 R6/R8; tests assert it). Residual = a narrow validate-CLI diagnostics gap
(`linkcheck/http.py:169` raises on `failures[0]` only, no row index), bounded because publish re-checks
per-row anyway. Not the structural "partial-success defeated" claim.

### 4. Destination-decay monitor (the operator's own money page going noindex/404 over time)
**Description:** Extend `preflight-targets` so its per-target receipts (reachable / noindex / soft-404 /
redirect) persist to `events.db` as a lifecycle signal, enabling a "destination decay" view: a target
page that was healthy at publish time but later went noindex or 404 silently nullifies *every* backlink
pointing at it.
**Rationale:** `preflight-targets` is a pure one-shot diagnostic that exits 0 and forgets (it makes no
durability claim). The in-flight closed-loop survival work watches the **source** link's survival; the
**destination** side has *no* survival signal at all. A target money-page going noindex/404 after links
are built is a catastrophic, totally-silent yield-killer — every surviving dofollow backlink to it is
worthless and nothing reports it. The check primitive already exists; only persistence + trend is new.
**Downsides:** Network-touching (must stay diagnostic / opt-in, never in the plan/validate kernel).
Needs a target-identity convention for the time series.
**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

### 5. Deficit-driven re-plan verb (close the plan → publish → blind loop)
**Description:** A read-only verb that reads `equity-ledger` output and emits `plan-backlinks`-compatible
seed JSONL prioritized by each target's **live-dofollow deficit** (targets with the fewest surviving
dofollow links first), so the next planning batch is driven by observed portfolio state instead of
re-typing seeds blind.
**Rationale:** The pipeline is plan→validate→publish, then the operator goes **blind** back to
hand-authoring `seeds.jsonl` with zero feedback from what already landed. `equity-ledger` already emits
the per-target live-dofollow breakdown (`cli/equity_ledger.py:30`) but nothing turns that asymmetry into
the next plan. This is distinct from the **rejected** yield-weighted plan *selection* (which needs
unobserved yield data): this is deficit-driven plan *generation* using only already-observed liveness,
no new yield model, fully pure/offline. Single biggest missing verb in the operator's actual job loop.
**Downsides:** Live-dofollow accuracy improves once the closed-loop recheck lands — early output is
best-effort. Needs a deterministic seed schema mapping from ledger rows.
**Confidence:** 76% → verified REAL (sweep)
**Complexity:** Low-Medium
**Status:** Explored — integration-verified (no ledger→seed bridge anywhere; ledger pre-sorts weak-first
at `aggregate.py:150`; minimal URL-only seeds already supported via `urls_to_seed_rows`) → brainstorm
2026-05-29.

### 6. Cross-adapter footprint coupling metric
**Description:** Extend footprint analysis from per-corpus to **cross-adapter**: a concentration metric
that detects when the same article skeleton / anchor text / banner / link-ordering is reused across N
platforms tightly enough to form one detectable link network. Scoped to a single cross-corpus
concentration signal, not a new engine.
**Rationale:** `footprint.py` analyzes one JSONL corpus per invocation (per-corpus self-fingerprint by
design). But link-network deindexation triggers on **cross-site sameness**, not single-site — the very
Penguin cluster key the module's own docstring describes but does not implement across corpora. Each
adapter passing its own footprint gate while the fleet shares one fingerprint is a false-success at the
fleet level — the gate measures the wrong unit. Directly on the anti-penalty theme the footprint kernel
exists to serve.
**Downsides:** Needs a corpus-joining convention; a naive metric can be noisy — must pick a concrete,
defensible concentration signal (e.g. shared-anchor-across-platforms ratio) rather than a fuzzy score.
**Confidence:** 74%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 12 | Honest zero-work verdict line | **Misread**: `success_count=0` does NOT exit 0 — `_publish_helpers.py:756-767` already emits exit-5 ("no payloads published") / exit-3 (all held) when not dry-run and not successful. Premise false. |
| 28 | Exit-code contract CI harness | **Misread + already cut**: contract is enforced by THREE tests (`test_exit_code_contract.py`, `test_cli_exit_code_literals.py`, `test_cli_exit_code_contract.py`); the literal scanner caught the `exit_code=45` bug. R11 already cut this (A5). |
| 2 | Dofollow drift sentinel (registry static vs live) | R11 explicitly rejected (B2, "front-runs events.db #1"); closed-loop R3 already owns live dofollow→nofollow drift → events.db. |
| 4 | Live-reality anchor profile reconciliation | R11 rejected (B6, anchor-text drift = noisy/high-FP); closed-loop R3 owns anchor-drift detection over time. |
| 19 | Dofollow-canary verdict → flip-PR scaffold | Conflicts with a settled review decision: both canary-closeout plans deliberately chose runbook+issue over a gate/automation ("forcing is illusory" at N=2). |
| 24 | Mid-run config-drift precise re-bind verdict | The AuthExpired path is already precise (`_publish_helpers.py:567` emits real `error_class`); the residual token-drift exit-3 already tells the operator to re-run. Low value. |
| 18 | Shadowban / soft-ban detector | Scope creep — requires a per-account public-visibility crawling subsystem; no visibility primitives to build on; beyond CLI-first single-operator scope. |
| 21 | Evidence-pack (proof-of-publish bundle) | Scope creep — client-reporting / multi-tenant deliverable; no client/reporting abstraction in the codebase; adjacent to equity-ledger's enumeration. |
| 22 | comment_outreach → equity bridge | Speculative cross-subsystem join of three young modules; sequencing-blocked on live-dofollow-deficit data. |
| 23 | Per-identity config sharding | Genuine anticipated gap (dedup key has an `account` slot config can't express), but a sizable config-model retrofit with no current single-operator pain — design note, not a Round-12 build. |
| 30 | Equity ledger forward-forecast with decay | Conflicts with the ledger's deliberate "no composite equity index" decision (plan R4); decay model speculative without data. |
| 3 | Pre-flight readiness aggregate verb | Thin wrapper — binding freshness, target reachability, and config blockers each already have separate verbs (OAuth preflight, preflight-targets, validate); composition only. |
| 13 | --explain run disposition ledger | RECON already emits `dropped_ids` by reason class; only a thin presentation layer over existing data. |
| 14 | Next-action taxonomy + audit-state --remediate | Remediation taxonomy partly exists (`_REMEDIATION`); the auto-execute `--remediate` adds a mutating action surface the silent-failure ethos treats warily. |
| 10 | events.db lossiness honesty flag | Risks front-running plan-007 (history_store demote changes the very completeness semantics); reconcile already distinguishes gap vs degraded. |
| 16 | Self-describing tier/backfill from adapter | The backfill string-map drift test already exists (`test_every_live_adapter_string_is_mapped`); only the `ROUTE_TIER_MATRIX` default-`c` fail-closed bit is unguarded — too narrow to stand alone. |
| 9 | Dedup 'done' correctness probe | Real gap (reconcile self-admits "coverage NOT correctness") but sequencing-blocked: overlaps the closed-loop recheck's liveness signal — defer behind it. |
| 8 | resolve-uncertain verdict + publish gate | The "force a verdict" loop already exists via the canary-closeout runbook; the publish-gate half was the part deliberately rejected as over-built (see #19). |
| 20 | Dedup-forecast (net-new yield preview) | Building blocks already let an operator reconstruct it (`--list-affected` / `--list-uncertain` + dry-run dedup verdicts). |
| 15 | Config scaffold generator | Convenience, not a correctness/groundedness gap; deprioritized vs the survivor set. |
| 17 | Anchor-text human-review boundary | **Borderline cut**: resolver's opt-in LLM anchor fallback (`resolver.py:201-217`, invoked in the plan loop) genuinely has no human-review surface unlike the body path — but it is opt-in-gated (constraint-compliant on the default-path axis) and `generate-backlink-text` infra largely covers the need. Held back to keep the bar at 6; reconsider if anchor-LLM usage grows. |
| 26→ | Cross-adapter footprint (survived, scoped) | Kept as survivor #6 but scoped down from "new engine" to a single cross-corpus concentration metric. |
| 5,6,7,11,25,1 | (survived) | Promoted to survivors. |
| 27 | Concurrent dedup soak + fault-injection matrix | Highest-risk seam (DB lease) already has coverage (`test_events_store_lease.py`); the uncovered mid-RMW-crash windows are real but no incident drives it — defer. |
| 29 | Plan re-validation ticket | Publish already re-checks reachability + token-drift; the only uncovered assumption (still-dofollow) overlaps #1/#2/closed-loop. |

## Session Log
- 2026-05-29: Round 12 fresh pass (open-ended). 5 frames × ~8 = 40 raw candidates, 30 unique after
  dedupe + 4 cross-frame syntheses. 2-critic adversarial pass (overlap/redundancy + groundedness/value)
  with live code verification of the strongest claims. Killed #12 and #28 as verified code-misreads
  (both premises false); killed #2/#4/#19 for overlap with R11 rejections + in-flight closed-loop work;
  killed #18/#21/#22 for out-of-scope product expansion. Merged #7+#25 into one language-gate
  correctness survivor; scoped #1 to the at-publish verdict only (away from ledger-feed D4 and the
  over-time recheck), #26 to a concentration metric. **6 survivors** reported honestly.
- 2026-05-29: #1 Language-gate correctness harness (NFC parity + ko threshold calibration) selected
  for brainstorm → ce:brainstorm.
- 2026-05-29: #1 brainstorm + document-review **superseded the premise** — the NFD silent-failure is
  already closed at the validate boundary (Unit 6 R13 `_nfc_normalize_in_place`, test at
  test_validate_backlinks.py:561). Only narrow residual (config-pool NFC) remains. Pivoting to another
  Round-12 survivor for brainstorm. Brainstorm doc marked superseded.
- 2026-05-29: #2 grounding **also superseded** — publish-time drift (incl. target_missing) is already
  recorded + debounced + drives the `is_quarantined`/`hard_skip` publish gate (Plan 2026-05-27-006
  Unit 3). Only residual is per-row immediacy, which fights the deliberate debounce design.
- 2026-05-29: **META-FINDING** — two consecutive top survivors (#1, #2) collapsed under
  integration-level verification. Root cause: ideation sub-agents + critics verified LEAF-function
  claims (docstrings literally say "advisory only: never raises"; lang.py literally lacks NFC) but did
  NOT trace the CALLER/CONSUMER layer where the gap was already closed. Round-12's groundedness is
  leaf-level, not integration-level. Recommend an integration-reachability re-pass on remaining
  survivors (#3/#4/#5/#6) BEFORE brainstorming any of them.
- 2026-05-29: **Integration-reachability sweep** on #3/#4/#5/#6 (4 parallel agents tracing
  callers/consumers). Verdicts: **#3 PARTIAL** (per-row reachability attribution already at publish-time
  `_publish_helpers.py:93-127`; validate-time abort deliberately preserved — downgraded);
  **#4 REAL** (nothing fetches/persists destination-page health over time; all signals are source-side;
  closed-loop defers destination as non-goal); **#5 REAL** (no ledger→plan-seed bridge anywhere; ledger
  already sorts weak-first at `aggregate.py:150` so the verb is mostly a format-transform);
  **#6 REAL** (footprint is per-corpus; cross-site `anon_concentration.py` was planned in
  2026-05-25-001 R14 but never built — grep zero hits). Sweep confirmed 3/4 genuinely open and caught
  #3's overstatement — the integration-level method works.
