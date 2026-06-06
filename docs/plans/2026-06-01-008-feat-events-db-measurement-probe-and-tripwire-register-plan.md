---
title: "feat: events.db measurement probe + scale-tripwire register"
type: feat
status: completed
date: 2026-06-01
origin: docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md
claims: {}
---

# feat: events.db Measurement Probe + Scale-Tripwire Register

## Overview

The operator asked to "optimize the database structure." A verified scan
established that `events.db` is tiny — 450 `events` rows, 308 `articles` rows,
~561 KB, `quarantine_log` empty — so every read pattern (including the
`json_extract` GROUP-BYs on the health dashboard) completes in well under a
millisecond. Pure latency optimization now is premature and adds carrying cost.

This plan ships the **measure-and-defer** deliverable the brainstorm converged
on: a one-shot, read-only **probe script** that makes the DB's current state and
query plans visible in one run, and a durable **scale-tripwire register** that
maps each deferred optimization to a measurable trigger so the right work lands
when evidence justifies it — not on a hunch. An additive index is included only
as a **conditional** unit, gated on probe evidence; its realistic outcome at
current scale is "add nothing."

Net production code shipped: effectively zero — a throwaway `scripts/` probe
(untested by repo precedent) and a documentation register. The optional index is
the only path that touches production code, and only if EXPLAIN proves it.

## Problem Frame

`events.db` is the read-side SQLite projection. The projector
(`events/projector.py`) incrementally projects the project's JSON state files
(checkpoint, `publish-history.json`, `draft-queue.json`) into it via `flush_for`
— it is **not** a JSONL event log, and there is **no `bp-events-rebuild` tool**
(the name appears only in a docstring and two error strings). The equity ledger,
health dashboard, footprint, and recheck all read from it. The operator's
"optimize structure" request was explicitly confirmed (origin doc) to mean
measure-and-defer: ship no structural change now. (see origin:
docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md)

## Requirements Trace

- R1. One-shot, read-only, stdlib-only probe printing row counts, `events.db` +
  WAL file sizes, and `EXPLAIN QUERY PLAN` for the recurring read queries. Must
  not mutate the DB or bump the schema. EXPLAIN output is mandatory (R5 depends
  on it). → Unit 1
- R2. Coarse single-pass wall-time (no benchmark fixture) for the heaviest read
  paths — the `build_health()` 4-connect render, the two `json_extract`
  GROUP-BYs, and `derive_decay_counts` — to anchor R3 thresholds. → Unit 1
- R3. A register enumerating each deferred optimization with a measurable
  trigger, the access pattern it serves, cheapest implementation, and carrying
  cost. → Unit 2
- R4. Register lives in a durable, discoverable location, links back to the
  brainstorm, and states triggers are checked by manually re-running the probe
  (no continuous monitoring). → Unit 2
- R5. Add an index **only if** a seeded-large-DB EXPLAIN shows the planner
  switching from `idx_events_kind_ts` to a new `idx_events_kind_article_ts`
  **AND** coarse timing shows a material wall-time win — via the idempotent
  no-version-bump slot. (The query already SEARCHes via `idx_events_kind_ts` at
  every scale, so the signal is a timing delta, not SCAN-vs-SEARCH.) Realistic
  outcome: ships nothing. → Unit 3 (conditional)

## Scope Boundaries

- No column denormalization (`verdict`, `platform`, `error_class`).
- No connection-reuse refactor; no projector batch-insert refactor; no FTS5;
  no building `bp-events-rebuild`.
- Event-kind vocabulary contract is out (owned by
  `2026-05-25-events-db-kind-contract`).
- Dual-store consolidation is out (owned by
  `2026-05-28-history-store-events-db-migration`).

## Context & Research

### Relevant Code and Patterns

- **Probe precedent:** `scripts/probe_indexability.py` — stdlib-only,
  read-only, "never raises", `#!/usr/bin/env python3`, module docstring with a
  provenance line + `Usage:` block, `from __future__ import annotations`,
  hand-parses `sys.argv` (no argparse), opens `sqlite3.connect` **directly** (not
  `EventStore`), resolves the DB via `BACKLINK_PUBLISHER_CONFIG_DIR` (default
  `~/.config/backlink-publisher`), prints human-readable to stdout / diagnostics
  to stderr, `main(argv) -> int` with `raise SystemExit(main(sys.argv))`. Mirror
  this shape exactly.
- **Read queries to EXPLAIN:**
  - `recheck/events_io.py` `derive_decay_counts` — `SELECT article_id,
    payload_json, ts_utc FROM events WHERE kind = ? AND article_id IS NOT NULL`
    (verdict deserialized in Python — index can't remove that).
  - `webui_app/health_metrics.py:138` `per_adapter` — `GROUP BY
    json_extract(payload_json, '$.platform')`.
  - `webui_app/health_metrics.py:176` `error_distribution` — `GROUP BY
    json_extract(payload_json, '$.error_class')`.
  - `ledger/sources.py` article scan; recheck selection (`recheck/selection.py`).
- **No-version-bump index slot:** `events/schema.py`
  `maybe_upgrade_schema` runs `_ensure_quarantine_dedup_key(conn)`
  unconditionally on every connect (lines ~161–167), explicitly commented "NO
  SCHEMA_VERSION bump … single, cheap, targeted statements … runs on every
  connect." Existing `events` indexes are declared in `_DDL_STATEMENTS`
  (`idx_events_kind_ts`, `idx_events_host_kind`, `idx_events_article_kind`).
- **Register home:** `docs/architecture/` holds durable, undated reference docs
  (`deterministic-planning-principle.md`). Cleaner than a net-new section in the
  538-line `AGENTS.md`.

### Institutional Learnings

- `docs/solutions/best-practices/probe-then-pivot-when-api-unverifiable-2026-05-20.md`
  — the repo's canonical "cheap probe first; a None/no-op result is a valid
  documented outcome, not a failure." Directly licenses R5's "ships nothing" as
  success.
- `docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md`
  — WAL nested-connection deadlock: a *second write* connection inside a held
  reducer transaction loses the write. A pure **read** probe under WAL is safe
  (WAL allows a concurrent reader + writer); the actionable rule is the probe
  must stay read-only and never call `maybe_upgrade_schema`.
- `docs/solutions/workflow-issues/plan-claims-gate-net-new-files-opt-out-2026-05-26.md`
  — plan docs need a `claims:` block; net-new-file plans use `claims: {}` to
  opt out (exit 8 missing block / exit 7 drift). Applied in this plan's
  frontmatter.
- `docs/solutions/best-practices/brainstorm-review-defers-to-plan-grounding-2026-05-19.md`
  — grep-verify every claim against the real repo at plan time. The origin
  brainstorm was burned twice by `src/`-scoped greps; all facts above were
  re-verified against the live tree this session.

### External References

- None. SQLite `EXPLAIN QUERY PLAN` is well-understood and a local probe
  precedent exists; external research was intentionally skipped.

## Key Technical Decisions

- **Probe is a throwaway `scripts/probe_*.py`, not maintained production code or
  a CLI entrypoint.** Rationale: a durable harness would itself be the premature
  build-ahead-of-need the brainstorm warns against; the disposable probe answers
  every current question at near-zero carrying cost. Naming it `probe_*` (not
  `check_*`) keeps it outside the `tests/test_no_orphaned_guard_scripts.py` gate,
  matching `probe_indexability.py`.
- **Probe opens `sqlite3` directly, never `EventStore`.** Rationale:
  `EventStore.connect()` runs `maybe_upgrade_schema` (a write path) on every
  connect; a measurement probe must not mutate or migrate the file it measures.
- **Register lives at `docs/architecture/events-db-scale-tripwire-register.md`.**
  Rationale: matches the established home for durable undated reference docs and
  avoids coupling a per-DB table to the long contributor guide.
- **Index (Unit 3) is conditional and likely ships nothing.** Rationale (EXPLAIN
  verified at 450 and 50k rows): the candidate `events(kind, article_id, ts_utc)`
  is a column-extension of `idx_events_kind_ts(kind, ts_utc)` on its leading
  `kind` column, and is unrelated to `idx_events_article_kind(article_id, kind)`
  (different leading column) — it is **not** a superset of either. The
  `derive_decay_counts` query already does an indexed `SEARCH` via
  `idx_events_kind_ts` at every scale (never a full scan), and `payload_json` is
  fetched from the table regardless, so the new index's benefit is marginal. Only
  evaluable against a seeded large DB via a measured timing delta. Defer-by-default.

## Open Questions

### Resolved During Planning

- Probe form? → A standalone `scripts/probe_events_db.py` script (research
  confirmed the precedent and that it needs no test/CI wiring).
- Register home? → `docs/architecture/events-db-scale-tripwire-register.md`
  (established convention; `docs/runbooks/` is the runner-up if framed as an
  operator procedure).
- plan-claims-gate? → `claims: {}` in frontmatter (net-new probe file).

### Deferred to Implementation

- The index timing verdict: the query already SEARCHes via `idx_events_kind_ts`
  at production scale, so the open question is whether the new index yields a
  *material wall-time win* on a *seeded large* `events.db` — only assessable at
  scale via the Unit 3 gate's timing comparison. The implementer builds the
  seeded DB per Unit 3's seeding constraint and may use the probe's `--db <path>`
  arg to point at it.
- Exact coarse-timing method inside the probe (`time.perf_counter` around a
  representative call vs. a few repeats and a median) — pick the simplest that
  stays "cheap" per R2.

## Implementation Units

- [x] **Unit 1: events.db measurement probe script** — DONE: `scripts/probe_events_db.py`, compiles, runs read-only (mtime unchanged), exit 0.

**Goal:** A one-shot, read-only, stdlib-only probe that prints the current
state of `events.db` and the query plans for the recurring read paths, so any
future "should we optimize?" is answered by re-running it.

**Requirements:** R1, R2

**Dependencies:** None

**Files:**
- Create: `scripts/probe_events_db.py`

**Approach:**
- Mirror `scripts/probe_indexability.py` structure: shebang, module docstring
  (one-line summary + provenance line referencing this plan + `Stdlib only.
  Read-only. Never raises.` + `Usage:` block), `from __future__ import
  annotations`, stdlib-only imports (`os`, `sqlite3`, `sys`, `time`, `pathlib`),
  `main(argv) -> int`, `raise SystemExit(main(sys.argv))`.
- Resolve the DB path from `BACKLINK_PUBLISHER_CONFIG_DIR` (default
  `~/.config/backlink-publisher`) + `events.db`; support an optional positional
  `--db <path>` so Unit 3 can point it at a seeded large DB. If the file is
  absent, print a clear message to stderr and return non-zero — never raise.
- Open read-only via `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` so
  read-only is a structural guarantee, not just discipline. **Do not** call
  `maybe_upgrade_schema` or any writing PRAGMA; close in a `finally`. (The
  precedent uses a plain connect; `mode=ro` is a cheap hardening over it.)
- Print three blocks to stdout: (a) row counts for `events`, `articles`,
  `quarantine_log`; (b) `events.db`, `-wal`, `-shm` file sizes; (c) for each
  named query, the literal SQL followed by its `EXPLAIN QUERY PLAN` rows.
- Named queries to EXPLAIN (all five, matching the Context section):
  - `derive_decay_counts` — `recheck/events_io.py` — `SELECT article_id,
    payload_json, ts_utc FROM events WHERE kind = ? AND article_id IS NOT NULL`
  - `per_adapter` — `webui_app/health_metrics.py:138` — `GROUP BY
    json_extract(payload_json,'$.platform')`
  - `error_distribution` — `webui_app/health_metrics.py:176` — `GROUP BY
    json_extract(payload_json,'$.error_class')`
  - `ledger/sources` article scan — `ledger/sources.py`
  - recheck selection — `recheck/selection.py`
  Bind placeholder params with representative literals so EXPLAIN is realistic.
- R2 timing: wrap each EXPLAINed query's *actual* execution in `time.perf_counter`
  **once** (no median/repeat machinery) and print a coarse `~Nms`. At sub-ms
  scale this is an order-of-magnitude anchor only, not a precise benchmark. Keep
  it inside the same run; no benchmark fixture.

**Execution note:** Throwaway diagnostic; no test by repo precedent.

**Patterns to follow:** `scripts/probe_indexability.py` (shape, DB-path
resolution, never-raises discipline, stdout/stderr split).

**Test scenarios:**
- Test expectation: none — throwaway operator probe with no behavioral contract
  consumers depend on; mirrors the untested `scripts/probe_indexability.py`
  precedent. Manual verification below substitutes.

**Verification:**
- Running `python scripts/probe_events_db.py` against the live
  `~/.config/backlink-publisher/events.db` prints non-empty counts (events≈450,
  articles≈308), file sizes, and a readable `EXPLAIN QUERY PLAN` block per named
  query, and exits 0.
- Running it against a missing path prints a clear stderr message and exits
  non-zero **without** a traceback.
- The `events.db` file mtime is unchanged after a run (proves no schema upgrade
  fired). The `-shm` shared-memory sidecar may be touched by any WAL reader —
  that is expected and does not mutate logical DB state.

- [x] **Unit 2: scale-tripwire register document** — DONE: `docs/architecture/events-db-scale-tripwire-register.md` with the 7-row table, 2026-06-01 baseline snapshot, and the Row 2 gate verdict.

**Goal:** A durable register that records every considered optimization, its
measurable trigger, the access pattern it serves, the cheapest implementation,
and its carrying cost — so deferral is explicit and the question isn't
re-investigated each time.

**Requirements:** R3, R4

**Dependencies:** Unit 1 (the register's threshold column references the probe;
seed the "current value vs trigger" notes from a probe run)

**Files:**
- Create: `docs/architecture/events-db-scale-tripwire-register.md`

**Approach:**
- Carry over the register table from the origin brainstorm verbatim as the
  starting content — exactly these 7 rows:
  1. Denormalize `verdict` → column
  2. `events(kind, article_id, ts_utc)` index
  3. Reuse one connection per request
  4. Batch projector inserts (`executemany`)
  5. Denormalize `platform` / `error_class` → columns
  6. FTS5 on `articles.body` / `events.payload_json`
  7. `VACUUM` / pruning policy
- Apply the corrected current-consumer reality: `per_adapter` /
  `error_distribution` and `build_health` ship today; row 5's deferral is on row
  count, not absence of a consumer. Row 2's gate is a timing delta, not
  SCAN-vs-SEARCH (see Unit 3).
- State explicitly: triggers are evaluated by an operator **manually re-running
  `scripts/probe_events_db.py`** and comparing — there is **no continuous
  monitoring / tripwire instrumentation**. Note the two triggers the probe does
  not measure: rebuild wall-time (tool doesn't exist) and disk size (read from
  the filesystem).
- Link back to the origin brainstorm and to this plan per R4.
- Record the current measured baseline (from Unit 1's run) as a dated snapshot
  block so the next reader sees "current value" beside each numeric trigger.

**Patterns to follow:** `docs/architecture/deterministic-planning-principle.md`
(durable, undated reference-doc tone and structure).

**Test scenarios:**
- Test expectation: none — documentation artifact.

**Verification:**
- The doc exists at the stated path, contains the 7-row table with a measurable
  trigger per row, the manual-recheck protocol, the dated baseline snapshot, and
  back-links to the brainstorm + this plan.
- The `platform`/`error_class` row states its consumer ships today and is
  deferred only on row count (no "none exist today" wording).

- [x] **Unit 3 (CONDITIONAL): guard index via no-version-bump slot** — GATE RAN, FAILED → no code change. Seeded 50k events / 12% link.rechecked: planner adopts the index but win is only 4.16→3.76ms (~10%, not material; payload_json still table-fetched). Verdict recorded in the register; `schema.py` untouched.

**Goal:** Add `events(kind, article_id, ts_utc)` **only if** probe evidence
justifies it. Expected outcome at current scale: do not add it; record the
EXPLAIN result in the register instead.

**Requirements:** R5

**Dependencies:** Unit 1 (its EXPLAIN, run against a *seeded large* DB), Unit 2
(to record the verdict)

**Gate (must pass before writing any code):** on a *seeded large* `events.db`,
`EXPLAIN QUERY PLAN` for the `derive_decay_counts` query shows the planner
**switching** from `idx_events_kind_ts` to the new `idx_events_kind_article_ts`,
**AND** a coarse `time.perf_counter` comparison (with vs. without the index)
shows a **material wall-time win**. Do NOT gate on SCAN-vs-SEARCH: the `kind = ?`
equality always lets the planner do an indexed `SEARCH` via `idx_events_kind_ts`
at every scale — a SCAN never appears, so a SCAN-based gate is unsatisfiable.
Because `payload_json` is fetched from the table regardless, the index is not
covering and the expected timing win is negligible. If the gate fails (the likely
case), STOP — record "planner already SEARCHes via idx_events_kind_ts; new index
yields no material timing win at scale X" in the register and close the unit with
no code change.

**Seeding constraint:** the seeded DB MUST be created with the full existing
index set — run `events.schema.initialize_schema(conn)` on a temp file, then bulk
-insert synthetic rows — so the EXPLAIN/timing compares **new-index-vs-existing-
indexes**, not index-vs-none. Do NOT seed via `EventStore.append` (slow) or a
bare `CREATE TABLE` without the indexes (invalidates the gate).

**Files (only if the gate passes):**
- Modify: `src/backlink_publisher/events/schema.py`
- Test: `tests/test_events_schema.py`

**Approach (only if the gate passes):**
- Add `CREATE INDEX IF NOT EXISTS idx_events_kind_article_ts ON events(kind,
  article_id, ts_utc)` in **both** places: append it to `_DDL_STATEMENTS` (so
  fresh DBs get it) and add a single idempotent call in the always-run additive
  slot beside `_ensure_quarantine_dedup_key` (so existing v3 DBs get it on next
  connect). **Do not** bump `SCHEMA_VERSION`. Optionally wrap in a private
  `_ensure_events_kind_article_ts(conn)` helper to match house style.

**Execution note:** Gate-first — do not modify schema.py until the seeded-DB
EXPLAIN gate passes.

**Patterns to follow:** `_ensure_quarantine_dedup_key` in
`events/schema.py` (idempotent additive-migration shape, hot-path comment).

**Test scenarios (only if the gate passes):**
- Happy path: after `maybe_upgrade_schema` on a **fresh** DB, querying
  `sqlite_master` shows `idx_events_kind_article_ts` exists.
- Integration: opening an **existing v3** DB (one created without the index)
  through `maybe_upgrade_schema` back-fills the index via the always-run slot —
  assert the index appears and `SCHEMA_VERSION` did **not** change.
- Edge case: a second `maybe_upgrade_schema` on the same connection is a no-op
  (idempotent `CREATE INDEX IF NOT EXISTS`) and does not raise.

**Verification (only if the gate passes):**
- `tests/test_events_schema.py` asserts the index exists on both fresh and
  upgraded-v3 DBs with no schema-version change; full events/schema suite stays
  green.

## System-Wide Impact

- **Interaction graph:** Unit 1 and Unit 2 touch no production code path. Unit 3
  (if it ships) adds one `CREATE INDEX IF NOT EXISTS` to the always-run slot,
  which runs on **every** `EventStore` connect (the project-on-read hot path) —
  idempotent no-op once created, single cheap statement.
- **State lifecycle risks:** The probe is strictly read-only and never calls
  `maybe_upgrade_schema`, so it cannot mutate or migrate `events.db`. Under WAL,
  a read probe coexists safely with a concurrent projector writer.
- **Unchanged invariants:** `SCHEMA_VERSION` stays 3; no table, column, or
  existing-index change; no writer or reader query is modified. The deferred
  optimizations remain deferred — this plan changes documentation + one
  conditional index, nothing else.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Probe accidentally mutates/migrates the DB it measures | Open raw `sqlite3` (not `EventStore`); never call `maybe_upgrade_schema` or any writing PRAGMA; verify mtime unchanged. |
| Timing at 450 rows can't detect an index win (all reads sub-ms) → false index decision | Unit 3 gate runs against a *seeded large* DB built with the existing index set, and rests on a measured timing delta (not SCAN-vs-SEARCH); default expected outcome is "ship nothing", recorded in the register. |
| New index adds per-connect cost on the hot path | Only added if the gate passes; single idempotent `CREATE INDEX IF NOT EXISTS`; no-op after first creation. |
| Register goes stale / re-investigation creeps back | Triggers are anchored to a re-runnable probe and a dated baseline snapshot; the register names the exact probe command to re-run. |
| plan-claims-gate rejects the plan doc | `claims: {}` opt-out for net-new files (per institutional learning). |

## Documentation / Operational Notes

- The register **is** the durable doc; no other docs need updating. Optionally add
  a one-line pointer from `AGENTS.md` to the register if discoverability proves
  weak (not required now).

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md](docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md)
- Probe precedent: `scripts/probe_indexability.py`
- Index slot: `src/backlink_publisher/events/schema.py` (`maybe_upgrade_schema`, `_ensure_quarantine_dedup_key`)
- Read queries: `src/backlink_publisher/recheck/events_io.py` (`derive_decay_counts`), `webui_app/health_metrics.py:138,176`
- Register home convention: `docs/architecture/deterministic-planning-principle.md`
- Learnings: `docs/solutions/best-practices/probe-then-pivot-when-api-unverifiable-2026-05-20.md`, `docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md`, `docs/solutions/workflow-issues/plan-claims-gate-net-new-files-opt-out-2026-05-26.md`
