---
date: 2026-05-28
topic: history-store-events-db-migration
---

# history_store → events.db Migration

## Problem Frame

The project maintains two independent stores for published backlink data:
`publish-history.json` (via `history_store`) and `events.db` (`articles` table).
These stores diverge whenever the projector ingests events the JSON writer missed,
or vice versa. `audit_state` CLI exists solely to detect this divergence — it is a
symptom of the dual-store design, not a cure.

Every future feature that reads publish history must maintain synchronisation with both
stores, and every operator report (equity ledger, health dashboard) performs a join
that partially re-derives what the other store already holds. Cutting the dual write
path makes events.db the single canonical source, `audit_state` redundant, and future
history reads trivial.

**Scope**: Only `history_store` ↔ `events.db` divergence. The other four JSON singletons
(`drafts_store`, `schedule_store`, `queue_store`, `profiles_store`) are explicitly out of scope.

## Requirements

**Write Path**

- R1. The `publish-backlinks` pipeline shall cease writing publish results to
  `publish-history.json`. All publish-result data currently written to
  `history_store` (via `webui_app/helpers/history.py` and the scheduler) shall be
  written to events.db instead.
- R2. events.db shall capture all data currently stored in `history_store` items,
  including: `platform`, liveness data (`verified_at`, `verify_error`), `status`,
  and a stable per-article identifier equivalent to the current `item_id`.
- R3. The WebUI recheck service (currently calls `history_store.update_item()` for
  liveness updates in `webui_app/routes/equity_ledger.py` and `api/history_api.py`)
  shall write liveness results to events.db.

**Read Path**

- R4. The WebUI history API (`webui_app/api/history_api.py`) and all template contexts
  that currently call `_history_store.load()` (primarily `helpers/contexts.py:155`)
  shall read from events.db instead.
- R5. `ledger/sources.py` shall stop joining `history_store` for `platform` and liveness
  data; all data shall come from a single events.db query path.

**Migration and Compatibility**

- R6. On first start-up after this change ships, if `publish-history.json` exists, its
  records shall be imported into events.db as a one-time migration. The file shall be
  renamed to `publish-history.json.migrated` after a successful import. Duplicate
  detection shall prevent double-counting records already present in events.db.
- R7. `history_store` shall remain importable (as a no-op or read-only shim) throughout
  any deprecation window to avoid breaking any callers that were not caught in the
  migration sweep; it shall be removed once all confirmed callers are gone.

**Cleanup**

- R8. `src/backlink_publisher/cli/audit_state.py` and its `audit/` support module shall
  be removed once all read/write paths have been migrated and the test suite passes
  without them.
- R9. All CI gates, tests, and `pyproject.toml` script entries referencing `audit-state`
  shall be updated or removed in the same PR as R8.

## User Flow

```
publish-backlinks runs
  │
  ├─► emit PUBLISH_CONFIRMED/UNVERIFIED/FAILED → events.db   [R1]
  │    (no write to publish-history.json)
  │
  └─► WebUI history page reads from events.db query          [R4]
       └─► recheck writes verify result → events.db          [R3]

First start-up after migration ships:
  publish-history.json exists?
  ├─ YES → import records into events.db (dedup-safe)        [R6]
  │         rename to publish-history.json.migrated
  └─ NO  → no-op
```

## Success Criteria

- `audit-state` CLI entry point is removed; no test file imports or calls it.
- `publish-history.json` is no longer written to during a standard publish run.
- WebUI history page shows identical records before and after migration (data fidelity
  test comparing imported JSON items to events.db query results).
- `ledger/sources.py` has no import of `history_store`.
- Equity ledger, health dashboard, and WebUI history views pass existing tests with no
  regressions.

## Scope Boundaries

- `drafts_store`, `schedule_store`, `queue_store`, and `profiles_store` are NOT migrated
  in this change.
- No schema version changes that require manual operator migration steps — the startup
  import (R6) is fully automatic.
- The CLI pipeline (`publish-backlinks` run outside the WebUI) must continue to work
  throughout; no WebUI-only gating.
- `publish-history.json.migrated` is left in place as an operator safety net; automatic
  deletion is out of scope.

## Key Decisions

- **Focused scope (history_store only)**: avoids scope creep; other 4 stores have no
  recorded divergence symptoms and no audit CLI counterpart.
- **Cut write path, not projection cache**: no cache invalidation complexity; cleaner
  to reason about correctness.
- **Startup-time auto-migration**: zero operator friction — no manual CLI step required.
- **audit_state deletion**: once the single source exists, divergence between stores can
  no longer occur; the tool becomes dead code and shall be removed (R8).

## Dependencies / Assumptions

- events.db schema must be extended (or new event kinds added) to capture `platform`,
  `verified_at`/`verify_error`, and a stable item identifier — currently absent from the
  `articles` table as dedicated columns. Note: `platform` is already stored as a key
  inside `payload_json` on `publish.confirmed` / `publish.unverified` events but is NOT
  a first-class `articles` column; `verified_at`, `verify_error`, and the history-row
  `item_id` do not exist anywhere in the DB layer. Schema strategy is deferred to planning.
- The `articles` table's `UNIQUE` constraint on `live_url` is the natural dedup key for
  the startup migration (R6). However, history rows with no `article_urls` (status
  `published` with an empty URL list) produce a `live_url = NULL` event in the current
  projector — and SQLite treats NULLs as distinct, so the UNIQUE constraint does NOT
  deduplicate them. The migration must handle this case explicitly (e.g. skip or
  match by run_id + target_url instead).
- `history_store.update_item()` is the only mutation path called outside the publish
  pipeline itself (recheck service, scheduler). No external scripts read
  `publish-history.json` directly — to be confirmed during planning.
- `scheduler.py` line 105 calls `_history_store.update()` directly (not via
  `_push_history_per_row`) — this bypass must be migrated as part of R1/R3.
- `helpers/contexts.py` has TWO `_history_store.load()` call-sites: line 155
  (`_calc_next_available`) and line 414 (`_render` auto-injection). R4 must
  cover both.
- `ledger/sources.py:_load_history()` lazy-imports `history_store` and feeds the
  result into `build_target_buckets`; after R5 this import path must be removed
  and replaced with a direct events.db query that returns equivalent `LinkRecord`
  data including `history_item_id` (currently only populated from the JSON store).
- `webui.py` imports `history_store` but does not call it directly — the import
  exists only for the startup store-init side effect and should be removed in R7.

## Outstanding Questions

### Resolve Before Planning

*(none — all product decisions are resolved)*

### Deferred to Planning

- [Affects R2][Technical] Schema extension strategy: extend `articles` table columns
  (`platform`, `verified_at`, `verify_error`) vs introduce new event kinds
  (`publish.verified`, `publish.verify_failed`) that record liveness as events.
- [Affects R3][Technical] Shape of the recheck write path in events.db — column update
  vs event append?
- [Affects R4][Needs research] Full call-site inventory of `_history_store.load()` and
  `history_store.update*` across WebUI routes and templates. Confirmed production
  call-sites found: `history_api.py` (load/update/bulk_delete/purge/get_item),
  `equity_ledger.py` (get_item/update_item), `helpers/contexts.py:155` and `:414`,
  `helpers/history.py` (all three push helpers), `scheduler.py:105` (direct update bypass),
  `ledger/sources.py:_load_history` (lazy import). All must be migrated.
- [Affects R6][Technical] Dedup logic during startup migration: match on `live_url`
  canonicalization; confirm no other match key is needed.
- [Affects R7][Needs research] Are there any callers outside `webui_app/` and
  `src/backlink_publisher/` (e.g. operator scripts, external integrations) that read
  `publish-history.json` directly?

## Next Steps

→ `/ce:plan` for structured implementation planning
