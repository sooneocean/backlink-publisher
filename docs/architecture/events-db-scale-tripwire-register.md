# events.db Scale-Tripwire Register

**Status:** Advisory — a deferral ledger, not enforced by CI or tests.

> **Principle:** `events.db` is tiny. Defer every latency optimization behind a
> *measurable* trigger, and re-decide by re-running the probe — not by re-investigating.

Origin: [docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md](../brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md)
· Plan: [docs/plans/2026-06-01-008-feat-events-db-measurement-probe-and-tripwire-register-plan.md](../plans/2026-06-01-008-feat-events-db-measurement-probe-and-tripwire-register-plan.md)

---

## How to use this register

`events.db` is a read-side SQLite projection (the projector reads checkpoint /
`publish-history.json` / `draft-queue.json` and projects into it). At a few
hundred rows, every read completes in well under a millisecond, so optimizing it
now is premature. Each row below is **deferred** until its trigger fires.

- Triggers are evaluated by an operator **manually re-running the probe** and
  comparing against the snapshot — there is **no continuous monitoring / tripwire
  instrumentation**. Re-run: `python scripts/probe_events_db.py`.
- Two triggers the probe does **not** measure: *rebuild wall-time* (no
  `bp-events-rebuild` tool exists yet) and *disk size* (read from the
  filesystem). Check those on demand.
- "Ship nothing" is a valid, expected outcome for a row whose trigger has not
  fired — record the measurement and move on.

---

## Deferred optimizations

| # | Deferred optimization | Trigger (measurable) | Serves | Cheapest impl | Carrying cost |
|---|---|---|---|---|---|
| 1 | Denormalize `verdict` → column | `link.rechecked` rows > ~10k **OR** `derive_decay_counts` > ~100 ms | dashboard decay banner; push GROUP BY into SQL | schema bump + backfill from `payload_json` + writer update | verdict in two places; migration |
| 2 | `events(kind, article_id, ts_utc)` index | seeded-large-DB shows planner adopts it **AND** a material wall-time win | `derive_decay_counts` / recheck reads | one additive `CREATE INDEX` in the no-version-bump slot | tiny per-connect cost; not a superset of existing indexes |
| 3 | Reuse one connection per request | `/health` route p95 > ~300 ms **AND** profiling blames connect+PRAGMA churn | `build_health()` / `webui_app/routes/health.py` — the existing ~4-connect render | thread one `conn` through builders / Flask `g` | connection-lifecycle complexity |
| 4 | Batch projector inserts (`executemany`) | full rebuild wall-time > ~2 s (**requires building `bp-events-rebuild` first**) | projector / rebuild path | not a drop-in: per-row `append()` does R9 validation + quarantine routing → needs a validate-then-bulk two-pass | reducer restructure |
| 5 | Denormalize `platform` / `error_class` → columns | events > ~50k (**consumer already ships** — see below) | `per_adapter()` / `error_distribution()` in `webui_app/health_metrics.py`, run on every `/health` render | schema bump + backfill | columns in two places |
| 6 | FTS5 on `articles.body` / `events.payload_json` | a real full-text search consumer ships | search feature | reserved `maybe_create_fts5` slot | index maintenance on every write |
| 7 | `VACUUM` / pruning policy | `events.db` > ~50 MB **OR** `quarantine_log` > ~10k rows | disk hygiene | scheduled maintenance task | operational cadence |

> Row 5 is deferred **purely on row count** (currently ~450 « 50k), **not** on
> absence of a consumer — `per_adapter`/`error_distribution`/`build_health` ship
> today. Row 2's trigger is a **timing delta**, not SCAN-vs-SEARCH (see baseline).

---

## Baseline snapshot — 2026-06-01

Captured via `python scripts/probe_events_db.py` against the production
`~/.config/backlink-publisher/events.db`.

| Metric | Value |
|---|---|
| `events` rows | 450 |
| `articles` rows | 308 |
| `quarantine_log` rows | 0 |
| `events.db` / `-wal` / `-shm` | 548.0 KB / 0 B / 32.0 KB |

Query plans + coarse wall-time (whole-table window):

| Read path | Plan | ~time |
|---|---|---|
| `health.success_rate` (window fn) | `SEARCH events USING INDEX idx_events_kind_ts` + temp B-tree | ~5.50 ms |
| `health.per_adapter` (json_extract GROUP BY platform) | `SEARCH ... idx_events_kind_ts` + temp B-tree GROUP BY | ~0.31 ms |
| `health.error_distribution` (json_extract GROUP BY error_class) | `SEARCH ... idx_events_kind_ts` + 2 temp B-trees | ~0.09 ms |
| `recheck.derive_decay_counts` | `SEARCH events USING INDEX idx_events_kind_ts (kind=?)` | ~0.04 ms |
| `recheck.selection_universe` | `SEARCH events USING INDEX idx_events_kind_ts (kind=?)` | ~0.23 ms |
| `ledger.sources_article_scan` | `SCAN articles` | ~0.72 ms |

**Reading:** every `events` read already uses `idx_events_kind_ts`; nothing
full-scans `events`. `success_rate` (the window-function query) is the heaviest
path at ~5.5 ms. No trigger above is anywhere near firing.

---

## Row 2 gate result — 2026-06-01 (verdict: do not add the index)

The guard index was gated on a seeded-large-DB timing comparison (plan Unit 3).
Result, on a temp DB built with the full canonical index set (`initialize_schema`)
and **50,000 events at a realistic 12 % `link.rechecked` mix / 5,000 articles**:

| | Plan | best-of-5 |
|---|---|---|
| Without `idx_events_kind_article_ts` | `SEARCH events USING INDEX idx_events_kind_ts (kind=?)` | 4.16 ms |
| With `idx_events_kind_article_ts` | `SEARCH events USING INDEX idx_events_kind_article_ts (kind=? AND article_id>?)` | 3.76 ms |

The planner **does** adopt the candidate index, but the win is ~0.4 ms (~10 %)
at 100× current scale — **not material** — because `payload_json` is fetched from
the table regardless (the index is not covering) and the Python-side `verdict`
deserialization dominates. **Gate fails → no index shipped.** Re-open only if the
Row 2 trigger fires AND a fresh seeded-DB run shows a material win.

> Note on selectivity: if `link.rechecked` ever becomes the *majority* of
> `events`, the planner switches `derive_decay_counts` to a full `SCAN` (an index
> returning ~all rows is pointless) and the candidate index is moot. The verdict
> holds across both regimes.
