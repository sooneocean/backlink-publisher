---
date: 2026-05-29
topic: round11-fresh-pass
focus: open-ended (raise-the-bar)
---

# Ideation: Round 11 Fresh Pass

## Codebase Context

**Project shape**: Python 3.11/3.12 SEO backlink automation. 8 CLI entrypoints (JSONL pipeline):
`plan-backlinks → validate-backlinks → publish-backlinks` + `report-anchors`, `equity-ledger`,
`footprint`, `phase0-seal`, `generate-backlink-text`. Flask WebUI (~20 route modules + 5 services +
`webui_store/` JSON singletons + `events.db` SQLite). ~30 publisher adapters via manifest-driven registry
(UiMeta/BindDescriptor/Policy). ~32.7k SLOC, ~3300 tests. 20+ `bp-*/` git worktrees.

**Hard constraints honored this round**: no runtime LLM in the plan/validate/publish kernel; pure
plan/validate engines (no stdout/stderr/SystemExit/network — network is opt-in/diagnostic only); never
smoke-test live `/save-*` endpoints; new UI surfaces are sibling pages, not monolith retrofits. The
project's signature bug class is **silent failure / false-success** — ideas that *close* such gaps score
well; ideas that *add* a swallow path are worthless.

**Excluded (shipped / Round-10 survivors, do not duplicate)**: events.db authoritative state (#1),
Manifest Full-Fidelity (#2), plan-claims→coverage gate (#3), unified health JSON (#4), worktree turf
gate (#5), per-adapter dry-run fidelity (#6), config snapshot secret scrub (#7); equity ledger, health
dashboard, events projector dedup, real-publish verification, retry+backoff, OAuth preflight, secret-file
0600, dofollow canary gate, checkpoint/resume, Dual-State Divergence Auditor (#238).

**Method**: 5 ideation frames × ~8 raw candidates → 39 raw → merge/dedupe + cross-frame synthesis →
2-critic adversarial pass → 7 survivors → **raise-the-bar re-evaluation** (final-bar critic with the
added test "must beat the already-queued Round-10 survivors for this sprint") → **4 survivors**.

## Ranked Ideas

### 1. recheck-backlinks --probe (Backlink Survival Re-Probe) ⭐ Synthesis, re-scoped
**Description:** A new `recheck-backlinks` CLI that re-runs liveness verification against every
previously-published `live_url` stored in events.db `articles` (today `linkcheck/verify.py:verify_published`
runs exactly **once** at publish time and is never re-checked). Network is gated behind an explicit
`--probe` flag, mirroring the validate-stage opt-in-network boundary; the engine stays out of the
plan/validate kernel. Reuses the existing `link_attr_verifier` (nofollow detection already exists) and the
manifest `liveness_probe_sec` field. **Re-scoped to clear the raised bar:** emit results to stdout / a
flat report only — do **not** add an events.db `link.checked` stream or `/portfolio` view yet, because the
append-only stream front-runs Round-10 #1 (events.db authoritative). Those become a follow-up once #1
lands and owns the authoritative store.
**Rationale:** Fills the single biggest real blind spot in the tool — after publish it is blind to whether
the link survived, got stripped, or lost dofollow. SEO yield is the *surviving dofollow set over time*,
which no current entrypoint owns. Re-probe data is the substrate for future attrition reporting and
replacement planning. The verify primitive already exists, so cost is moderate.
**Downsides:** Network-touching (must stay strictly a `--probe`-gated CLI, never invoked by plan/validate).
Full survival history (event stream, portfolio view, attrition→replacement planner) deliberately deferred
to avoid colliding with the events.db-authoritative migration.
**⚠️ 2026-05-29 update — re-scope premise retracted:** During brainstorm this idea was found to be
already covered, more completely, by `docs/brainstorms/2026-05-29-backlink-lifecycle-closed-loop-requirements.md`
(full closed-loop survival monitoring). The "stdout-only, do NOT write events.db (to avoid front-running
Round-10 #1)" re-scope above is **wrong**: the active migration plan
`docs/plans/2026-05-28-007-refactor-history-store-events-db-migration-plan.md` includes recheck as its
**Unit 3** ("Recheck liveness write → new event kinds → EventStore"), so writing recheck results to
events.db is the *sanctioned, planned* target — not front-running. The correct sink is events.db
lifecycle kinds (`publish.verified` / `publish.verify_failed`); what must be avoided is direct
history_store writeback (history_store is being demoted to a no-op shim). Real relationship to Round-10 #1
is a **sequencing dependency** (recheck CLI depends on plan-007 U1+U3), not an architectural conflict.
The brainstorm decisions (CLI selection model, exit-code, no-probe dry preview, 5-verdict set) were folded
into the closed-loop doc (R11-R14, D7). Proceed via that doc, not a separate narrow slice.

**Confidence:** 80%
**Complexity:** Medium
**Status:** Explored — brainstorm 2026-05-29 (reconciled into closed-loop-requirements doc)

### 2. Manifest Drift-Proof Property Test
**Description:** A property test asserting, for every registered adapter, that the runtime-used constant
equals the manifest-declared Policy value (`throttle_band`, `language_whitelist`, `retry_id`,
`liveness_probe_sec`) — today medium/velog hardcode jitter constants that duplicate/ignore their manifest
values — plus a completeness assertion that every registered adapter has a non-stub Policy. Verified gap:
`test_manifest_contract.py` checks visibility/bind/template but has **zero** runtime-constant==manifest
assertion and is not a completeness fail-gate.
**Rationale:** Cheapest real silent-divergence closer in the set. The existing contract test *looks* like
coverage but provably isn't, so an adapter can silently drift from its declared manifest today. Ships
independently of all Round-10 substrate and de-risks the eventual Manifest Full-Fidelity (#2) migration by
turning "manifest is documentation" into "manifest is checked contract" incrementally.
**Downsides:** Needs a small map of which runtime constant corresponds to which manifest field; must be
kept in sync as new Policy fields are added.
**Confidence:** 88%
**Complexity:** Low
**Status:** Unexplored

### 3. Run Inbox — surfacing resumable runs (+ run receipt as its data source)
**Description:** `bp runs` CLI + a `/runs` sibling WebUI page that surface checkpoint runs with their
status (pending/failed/done counts) and the exact resume command, with one-click resume reusing the
shipped `publish-backlinks --resume <run_id>` path. Verified gap: `cli/_resume.py` + `checkpoint.py` exist
but there is **no `runs`/`resume` console script** in `pyproject.toml` — the shipped capability is
dead-in-practice. Fold the salvaged `--receipt` idea in here: a machine-readable end-of-run JSON summary
becomes the data source `bp runs` reads, rather than a standalone feature.
**Rationale:** An operator whose cron job died at item 13/20 currently has no way to discover the
recoverable run except reading cache dirs. Closes a confirmed silent-capability blind spot no Round-10
survivor addresses. Read-only listing; resume reuses an already-safe path.
**Downsides:** Needs a stable run-id discovery convention; the `/runs` page must stay a sibling page, not a
monolith retrofit.
**Confidence:** 82%
**Complexity:** Low-Medium
**Status:** Unexplored

### 4. Silent-Failure Divergence Sentinel (narrowed)
**Description:** A `bp-health --assert-no-divergence` mode wired as a CI tripwire, covering only the seams
the shipped Dual-State Divergence Auditor (#238) does **not** cover: canary verdict count vs publish-output
count, projector cursor freshness, and `quarantine_log` growth rate. Exits non-zero on any gap; runs in CI
against a seeded fixture DB and as a post-publish self-check.
**Rationale:** #238 is a read-only, on-demand *diagnosis*; nothing currently trips CI on cursor staleness,
quarantine growth, or canary-vs-output count drift. Converts the project's most expensive recurring failure
mode (silent drop at classification seams) from per-incident discovery into a standing guard. Every new
seam added in future inherits one divergence contract.
**Downsides:** Must be held strictly to the non-#238 seams or it duplicates the shipped auditor. Seeded
fixture maintenance required.
**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | B3 Deindexation probe | Depends on `site:`/search-engine scraping — ToS risk + fragile signal; prior rounds rejected the same class |
| 2 | B6 Anchor-text drift on live pages | fetch-and-diff has high false-positive rate (pages legitimately reformat anchors); noisy |
| 3 | B8 Removal-kind discriminator | Premature classification before the basic recheck loop exists; folds under #1 later |
| 4 | B2/B5/B7 dofollow sentinel / link.checked stream / portfolio view | Front-run Round-10 #1 (events.db authoritative); deferred into #1's follow-up scope |
| 5 | D6 Browser-recorded adapters (`bp record`) | Recorded recipes silently rot when sites change = new silent-failure surface; high maintenance |
| 6 | D1 Kill JSONL internal contract | Big-bang rewrite of a mature B+ pipeline; no incident driving it; hides the edge contract that catches divergence |
| 7 | D8 Collapse 8 CLIs into bp do/see | Large UX re-architecture; churns exit-code contracts for zero functional gain |
| 8 | D2 Reframe product as Backlink Survival | Correct strategic theme but a slogan, not a deliverable; realized concretely as #1's wedge |
| 9 | D3 Tier the 30 adapters | Rides entirely on Manifest Full-Fidelity (#2); not independent |
| 10 | D4 / E8 Retire ideation ritual / self-audit ledger | Pure process meta-work; doesn't touch the product |
| 11 | D5 Yield-weighted plan selection | Blocked on measured backlink-yield data the tool can't yet observe (the very gap #1 hasn't closed) |
| 12 | D7 Delete events.db sibling JSON write paths | Aggressive end-state of Round-10 #1; contradicts its deliberate staged demote-to-cache sequencing |
| 13 | C1 Auto-generate monolith budget rationale | Round-10 already rejected; per-file granularity + 80-char rationale is deliberate discipline |
| 14 | C2 / C6 Promote manifest stubs / auto-derive bind forms | Verbatim parts (a)/(c) of Round-10 #2 |
| 15 | C3 Collapse 3 login CLIs | Modest DRY; front-runs the manifest work it depends on |
| 16 | C4 Retire spike graveyard | Housekeeping ceremony; delete manually if needed, no system warranted |
| 17 | C5 Auto-claim worktree turf via git hook | Duplicates Round-10 #5 and repeats round-5 H6's own rejection (git-hook-across-N-worktrees foot-gun) |
| 18 | C7 audit_state self-delete | Round-10 #1 already states audit_state "becomes redundant" — duplicate |
| 19 | E1 Solution-doc index + guard linter | Overlaps round-7 shipped "Solutions-as-Lint"; index half is doc ceremony |
| 20 | E2 events.db retention/compaction | Real but no current scale pain; defer until events.db growth is felt |
| 21 | E4 Concurrent-agent DB lease gate | Strictly better than Round-10 #5 but is "reconcile that survivor", not a new add |
| 22 | E7 Scale fixture + perf floor | No evidence of perf pain at current size; nice-to-have |
| 23 | A1/A4/A6/A7/A8 operator-UX miscellany | Ergonomics polish or folded into #1/#3; no silent-failure gap closed |
| — | A5 Cron receipt + exit-code test (cut at final bar) | Exit-code invariant already locked by `test_exit_code_contract.py`; `--receipt` salvaged into #3 |
| — | A3 Blast-radius preview (cut at final bar) | Reconstructable from existing `dry_run` + `audit-state` + `plan-check`; weaker than the Round-10 queue |
| — | E3 Adapter golden-master harness (cut at final bar) | Self-admittedly the test backbone of Round-10 #6; nothing to freeze until #6's per-adapter validation lands — defer behind #6 |

## Session Log
- 2026-05-29: Round 11 fresh pass (raise-the-bar). 5 frames × ~8 = 39 raw candidates, 22 unique after
  dedupe + 2 cross-frame synthesis additions, 7 survivors after 2-critic adversarial pass.
- 2026-05-29: Raise-the-bar re-evaluation requested. Final-bar critic (added test: beat the queued
  Round-10 survivors) cut 7 → 4. Cuts: A5 (exit-code test already shipped; receipt folded into #3),
  A3 (reconstructable from existing tools), E3 (blocked behind Round-10 #6). Survival Loop re-scoped to
  `recheck-backlinks --probe` flat report (event stream + portfolio view deferred behind Round-10 #1).
  4 survivors reported honestly rather than padding to 5-7.
- 2026-05-29: #1 `recheck-backlinks --probe` (re-scoped Survival Loop) selected for brainstorm → ce:brainstorm.
- 2026-05-29: Brainstorm found #1 already covered (more completely) by the same-day
  `2026-05-29-backlink-lifecycle-closed-loop-requirements.md`, and surfaced active migration plan-007
  whose Unit 3 already plans the recheck→events.db write. Re-scope premise ("no events.db") retracted;
  conflict reconciled to a sequencing dependency. Brainstorm CLI decisions folded into the closed-loop doc
  (R11-R14, D7); R6 re-targeted from history_store writeback to events.db kinds. No separate narrow doc created.
