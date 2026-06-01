---
date: 2026-06-01
topic: events-db-optimization-tripwire
type: brainstorm
status: ready-for-plan
trigger: "/ce-brainstorm '幫我優化我的資料庫結構'"
related:
  - 2026-05-25-events-db-kind-contract-requirements.md   # kind/vocab contract — OUT of scope here
  - 2026-05-28-history-store-events-db-migration-requirements.md   # dual-store consolidation — OUT of scope here
  - 2026-06-01-optimization-backlog-consolidation-requirements.md   # "60% never ship" — premise for defer-by-default
---

# events.db Optimization — Measure-First & Scale-Tripwire Register

## Problem Frame

`events.db` is the read-side SQLite **projection**. The projector
(`events/projector.py`) reads the project's existing JSON state files —
checkpoint, `publish-history.json`, `draft-queue.json` — and writes rows into
events.db incrementally via `flush_for`. It is the emerging state-of-truth that
the equity ledger, health dashboard, footprint, and recheck all read.

> **Architecture caveat (verified):** there is currently **no `bp-events-rebuild`
> command** — the name appears only in a docstring (`events/store.py`) and two
> error-message strings (`events/schema.py`). The projection is rebuildable *in
> principle* (the source JSON files are retained), but the one-shot rebuild tool
> does not exist yet. Earlier framing of "truth = JSONL, rebuildable via
> bp-events-rebuild" was wrong on both counts and is corrected here.

The operator asked to "optimize the database structure." A direct, re-measured
scan established the decisive fact: **the database is tiny** — 450 `events` rows,
308 `articles` rows, ~561 KB on disk, `quarantine_log` empty (measured
2026-06-01). Existing indexes (`events(kind,ts_utc)`, `events(host,kind)`,
`events(article_id,kind)`, `articles(host,published_at_utc)`,
`articles(run_id)`, `articles.live_url UNIQUE`) already cover the dominant
access shapes.

At this scale every read pattern — even the `json_extract` GROUP-BY queries
below — completes in well under a millisecond. Pure latency optimization (column
denormalization, connection reuse, batch inserts, FTS5) would save single-digit
milliseconds while adding real carrying cost: schema migrations, backfills,
writer coupling. That is textbook premature optimization, and the project's own
`2026-06-01-optimization-backlog-consolidation` doc already warns that ~60% of
brainstorms never ship because the bottleneck is **execution/convergence, not
ideation**. Shipping a large optimization plan now would be that anti-pattern.

So this brainstorm deliberately inverts the usual shape. Instead of designing
optimizations to build now, it: (1) runs a cheap, **throwaway** measurement
probe, (2) records each candidate optimization in a **scale-tripwire register**
behind a measurable trigger, and (3) names explicit **non-goals**. Each
optimization then lands when evidence justifies it — not on a hunch today.

> **Correction note (review pass):** an exploratory sub-agent first surfaced
> these `json_extract` GROUP-BY queries; a later verification pass wrongly
> dismissed them as fabricated because its `grep` was scoped to `src/` only —
> `webui_app/` lives at the repo root, not under `src/`. The queries are **real**
> (verified at `webui_app/health_metrics.py:138` and `:176`). The only
> genuinely-absent item from that earlier pass was a `failure_type` *column /
> index* — `failure_type` is stored inside `raw_payload_json`, not as a column.

## Verified current-state facts

- **`per_adapter()` / `error_distribution()`** (`webui_app/health_metrics.py`)
  run `GROUP BY json_extract(payload_json, '$.platform')` (line 138) and
  `'$.error_class'` (line 176) respectively — a real, shipped consumer exercised
  on **every** `/health` dashboard render.
- **`build_health()`** (`webui_app/health_metrics.py`) fans out ~4
  `EventStore.query()` calls per render (`success_rate`, `per_adapter`,
  `error_distribution`, `decay_counts`). Each `query()` opens a **fresh
  connection** + 4 PRAGMAs + `maybe_upgrade_schema`, then closes. A real
  multi-query route today, not hypothetical.
- **`derive_decay_counts`** (`recheck/events_io.py`) reads all `link.rechecked`
  events (`WHERE kind=? AND article_id IS NOT NULL`) and deserializes
  `payload_json` row-by-row in Python to find the latest `verdict` per
  `article_id`. An index alone cannot remove the Python-side deserialization —
  `verdict` lives inside the JSON payload.
- **Existing `events` indexes:** `idx_events_kind_ts(kind, ts_utc)`,
  `idx_events_host_kind(host, kind)`, `idx_events_article_kind(article_id, kind)`.
- **Idempotent no-version-bump migration slot** exists in `maybe_upgrade_schema`
  (`_ensure_quarantine_dedup_key`, run on every connect). The only zero-risk
  place to add an index.
- Schema is v3; the FTS5 virtual-table slot (`maybe_create_fts5`) is reserved but
  intentionally unimplemented.

## Requirements

**Measurement (do now — read-only, throwaway probe)**
- R1. Provide a **one-shot, read-only probe script** (mirroring the existing
  `scripts/probe_indexability.py` precedent: stdlib-only, never mutates,
  never raises) that prints, in one run: row counts (`events`, `articles`,
  `quarantine_log`), `events.db` + WAL file sizes, and `EXPLAIN QUERY PLAN`
  output for the recurring read queries — **`per_adapter` and
  `error_distribution`** (the `json_extract` GROUP-BYs), **`derive_decay_counts`**,
  the **`ledger/sources` article scan**, and **recheck selection**. It is a
  disposable probe, **not** a maintained/re-runnable production harness, and must
  not mutate the database or bump the schema. EXPLAIN output is **mandatory** (R5
  depends on it).
- R2. In the same probe, capture a **coarse single-pass wall-time** (median of a
  few runs, no benchmark fixture) for the heaviest currently-exercised read
  paths — name them explicitly: the **`build_health()` 4-connect render**, the
  two `json_extract` GROUP-BYs, and `derive_decay_counts` — to anchor the R3
  thresholds in real numbers. Drop any timing that is not trivially cheap to
  collect inside the probe; the thresholds are order-of-magnitude, so coarse is
  sufficient.

**Scale-Tripwire Register (the durable artifact)**
- R3. Produce a register enumerating each deferred optimization with: a
  *measurable* trigger threshold, the access pattern it serves, the cheapest
  implementation that satisfies it, and its carrying cost. Initial contents in
  the table below.
- R4. The register lives somewhere durable and discoverable (an `AGENTS.md`
  section or a standalone `docs/` file) and links back to this brainstorm.
  Triggers are evaluated by an operator **manually re-running the R1 probe** and
  comparing — there is **no continuous monitoring / tripwire instrumentation** in
  scope. Two triggers are *not* measured by the probe and are checked on demand:
  rebuild wall-time (the `bp-events-rebuild` tool does not exist yet) and disk
  size (trivially read from the filesystem).

**Guard index (optional — gated strictly on R1 evidence)**
- R5. Add an index **only if** `EXPLAIN QUERY PLAN` (R1) shows the planner doing
  a `SCAN` *that persists even with the existing `idx_events_kind_ts` and
  `idx_events_article_kind` present* — the sole candidate, `events(kind,
  article_id, ts_utc)`, is an overlapping superset of both, so it is only
  justified if the planner provably ignores both for a confirmed recurring query.
  If justified, add it via the existing idempotent no-version-bump slot. Its
  benefit is doubly bounded: `derive_decay_counts` still deserializes `verdict`
  from `payload_json` in Python regardless. **Realistic outcome at 450 rows: the
  planner picks a full scan and R5 ships nothing** — which is a valid, expected
  result, not a failure.

### Scale-Tripwire Register (initial draft)

| Deferred optimization | Trigger (measurable) | Serves | Cheapest impl | Carrying cost |
|---|---|---|---|---|
| Denormalize `verdict` → column | `link.rechecked` rows > ~10k **OR** `derive_decay_counts` > ~100 ms | dashboard decay banner; push GROUP BY into SQL | schema bump + backfill from `payload_json` + writer update | verdict in two places; migration |
| `events(kind, article_id, ts_utc)` index | EXPLAIN shows `SCAN` persisting despite existing two indexes **AND** events > a few k | `derive_decay_counts` / recheck reads | one additive `CREATE INDEX` (no bump) | tiny write cost; overlaps two existing indexes |
| Denormalize `platform` / `error_class` → columns | events > ~50k (the consumer half is **already satisfied** — see below) | `per_adapter()` / `error_distribution()` in `webui_app/health_metrics.py` — **ship today, run on every `/health` render** | schema bump + backfill | columns in two places |
| Reuse one connection per request | route p95 > ~300 ms **AND** profiling blames connect+PRAGMA churn | `build_health()` / `webui_app/routes/health.py` — **the existing ~4-connect render** | thread one `conn` through builders / Flask `g` | connection-lifecycle complexity |
| Batch projector inserts (`executemany`) | full rebuild wall-time > ~2 s (**requires building `bp-events-rebuild` first** — does not exist) | projector / rebuild path | not a drop-in: per-row `append()` does R9 required-field validation + per-row quarantine routing, so this needs a validate-then-bulk two-pass refactor | reducer restructure |
| FTS5 on `articles.body` / `events.payload_json` | a real full-text search consumer ships | search feature | reserved `maybe_create_fts5` slot | index maintenance on every write |
| `VACUUM` / pruning policy | `events.db` > ~50 MB **OR** `quarantine_log` > ~10k rows | disk hygiene | scheduled maintenance task | operational cadence |

> The `platform`/`error_class` row is deferred **purely on row count** (450 «
> 50k), not on absence of a consumer — the consumer ships today. Be honest about
> *why* it waits.

## Success Criteria
- The next "should we optimize events.db?" is answered in under 2 minutes by
  re-running the R1 probe and comparing against R3 thresholds — no fresh
  investigation.
- Net **production** code shipped by this work is **zero** beyond what the
  register documents: R1/R2 is a throwaway probe script (like
  `scripts/probe_indexability.py`, not maintained production code), plus at most
  one evidence-justified index (R5), whose realistic outcome at current scale is
  "add nothing." No schema bump, backfill, or denormalization ships now.
- Every deferred optimization that was *considered* is recorded with its trigger
  and its current-consumer reality, so nothing is silently dropped, nothing is
  built prematurely, and no register row misstates whether its consumer exists.

## Scope Boundaries
- **IN SCOPE NOW:** the R1/R2 throwaway probe, the R3/R4 register, and (only if
  R1 EXPLAIN justifies it) one additive index.
- **OUT OF SCOPE NOW:** column denormalization (`verdict`, `platform`,
  `error_class`); connection-reuse refactor; projector batch-insert refactor;
  FTS5; building `bp-events-rebuild`.
- Event-kind vocabulary / classification contract is **out** — owned by
  `2026-05-25-events-db-kind-contract`.
- Dual-store consolidation (`history_store` ↔ events.db) is **out** — owned by
  `2026-05-28-history-store-events-db-migration`.

## Key Decisions
- **Defer-by-default behind measurable triggers**: at ~hundreds of rows,
  optimization carrying cost exceeds benefit, and the project's bottleneck is
  execution, not ideas.
- **R1 is a throwaway probe, not a maintained harness**: a durable harness would
  itself be speculative build-ahead-of-need; the disposable probe answers every
  current question at near-zero carrying cost.
- **Reuse the idempotent no-version-bump migration slot** for any index, keeping
  changes zero-risk.
- **Measure before, not instead of, optimizing**: the register's thresholds are
  only credible once anchored to R1/R2 numbers.

## Dependencies / Assumptions
- events.db stays an incrementally-projected read-side view of the JSON state
  files; the source files are retained, so the projection is reconstructible even
  though the one-shot `bp-events-rebuild` tool is not built.
- Usage stays **single-operator / low-concurrency**; revisit the connection-reuse
  trigger if WebUI concurrency rises.

## Outstanding Questions

### Resolve Before Planning
- *(none — resolved 2026-06-01)* The "優化我的資料庫**結構**" ask was explicitly
  confirmed by the operator to mean **measure-and-defer**: ship no structural change
  now (no denormalization, including the `platform`/`error_class` candidate whose
  consumer ships but is still sub-ms at 450 rows). Revisit only when a register
  trigger fires.

### Deferred to Planning
- [Affects R1][Technical] Probe form — a standalone `scripts/` script (matching
  `probe_indexability.py`) vs a `pytest` block. Lean: a `scripts/` script, to keep
  it out of maintained production code.
- [Affects R4][Technical] Register home — an `AGENTS.md` section vs a standalone
  `docs/` file (AGENTS.md couples a per-DB table to the contributor guide and may
  go stale).
- [Affects R5][Needs research] Does SQLite's planner choose `(kind, article_id,
  ts_utc)` over the existing `idx_events_kind_ts` / `idx_events_article_kind` at
  projected scale? Validate with `EXPLAIN QUERY PLAN` against a *seeded large* DB
  — production data (~450 rows) will never exercise it.

## Next Steps
→ `/ce:plan` for structured implementation planning (after confirming the
  Resolve-Before-Planning reframing question).
