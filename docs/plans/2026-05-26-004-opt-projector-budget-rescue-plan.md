---
title: "opt: events/projector.py budget rescue — shared-helper extraction"
type: opt
status: active
date: 2026-05-26
claims: {}
---

# opt: events/projector.py budget rescue

## Scope

Extract 11 self-contained shared helpers from `events/projector.py` (671 radon SLOC,
ceiling 680 — 9 SLOC headroom) into `events/_project_helpers.py`, freeing ~120 SLOC
and bringing headroom to a comfortable ~130 SLOC.

## Extraction target: `_project_helpers.py`

Functions to move (none interact with the three reducers' internal state):

| # | Function | SLOC est. | Dependencies |
|---|----------|-----------|-------------|
| 1 | `_detect_source` | 12 | `_HISTORY_FILENAME`, `_DRAFTS_FILENAME`, `_RUN_ID_RE`, `ProjectionError` |
| 2 | `_cursor_load` | 12 | `sqlite3`, `json`, `ProjectionError` |
| 3 | `_cursor_save` | 16 | `sqlite3`, `json` |
| 4 | `_split_iso_with_offset` | 5 | `datetime` |
| 5 | `_split_local_naive` | 5 | `datetime` |
| 6 | `_read_json` | 8 | `json`, `_log` |
| 7 | `_extract_anchors` | 10 | — |
| 8 | `_host_of` | 3 | `urlparse` |
| 9 | `_write_quarantines` | 16 | `EventStore`, `_log` |
| 10 | `_checkpoint_event_timestamp` | 10 | `_split_iso_with_offset` |
| 11 | `_article_payload` | 24 | `canonicalize_url`, `json` |
| | **Total** | **~121** | |

Also move `ProjectionError`, `_RUN_ID_RE`, `_HISTORY_FILENAME`, `_DRAFTS_FILENAME`
constants. `_log` is duplicated in the new module.

## What stays in projector.py

- `ProjectionResult`, `flush_for`, `record_projection_health`, `project_run_safe`
- `_HEALTH_SOURCE`, `_QUARANTINE_DEGRADED_RATIO`
- Three reducers: `_project_checkpoint`, `_project_history`, `_project_drafts`
- The existing `_log` (still used by `record_projection_health` and `project_run_safe`)

## Circular dependency analysis

- `_project_helpers.py` imports: standard lib + `.._util.url.canonicalize_url` + `.store.EventStore`
- `_project_helpers.py` does NOT import from `projector.py`
- `projector.py` imports from `_project_helpers.py`: one-way, no cycle ✓

## Verification

- Full test suite pass (4990+ tests)
- `test_events_r10_alarm.py` (imports `ProjectionResult`) — unchanged
- `test_events_projection_wiring.py` (imports `_HEALTH_SOURCE`) — unchanged
- Budget test `test_no_monolith_regrowth.py` — ceiling 680 unchanged; headroom must be ≤50
