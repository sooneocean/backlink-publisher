---
title: "feat: Read-Time Reconciliation Hub"
type: feat
status: shipped
date: 2026-05-28
claims: {}
---

# Plan: Read-Time Reconciliation Hub

- **Status:** Shipped (U1-U4 PR #290 `482de679`; U5+Makefile PR #294)
- **Created:** 2026-05-28
- **Owner:** Sisyphus
- **Priority:** P1 (post publish-backlinks refactor)
- **Depends on:** publish-backlinks concurrent refactor landing (in flight); feature-003

## Problem

`publish-backlinks` creates checkpoint items during the link-publish phase, then `_project_all()` projects articles through the dedup store. But there is **no cross-check** that checkpoint items ever reached the dedup store, or that dedup records correspond to checkpoints at all. A backlink that was published (checkpoint `pending`) but failed to project (no dedup record) is **silent** — no operator signal, no automatic retry, no quarantine.

Separately, `publish-history.json` records what was published, and the dedup store records what was projected — but nobody compares them. A published URL that never entered the dedup store is invisible until a user notices the backlink is missing.

## Scope

**In scope:**
- Extend `_project_all()` with a reconciliation pass (same write lock, same pass — R1)
- Cross-reference checkpoint items against the in-memory dedup store → auto-fix where possible (R2)
- Quarantine irreconcilable checkpoint gaps, with schema support (R3, R9)
- Auto-clear reconciled quarantine items on subsequent runs (R8)
- Skip previously-quarantined items to avoid repeated work (R10)
- Cross-reference `publish-history.json` against the dedup store — report-only (R4)
- CLI flags: `--reconcile <run_id>` / `--reconcile-all` with JSONL output (R3)
- RECON.log for all events (R5)
- Dashboard gap count + warning banner (R7)
- Makefile gating: tests + `--reconcile-all` exit 0 (R11, R12)

**Deferred to follow-up:**
- `--reconcile` without `publish-backlinks` (standalone reconciler CLI) — may emerge as a separate entrypoint in a later PR
- Webhook/alert from the reconciler — future, not v1
- integration into `audit-state` (001 dual-state auditor) — that tool already covers events.db ↔ history; this plan covers checkpoints ↔ dedup

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| **R1/R8: Same-pass reconciler** — runs inside `_project_all()`, between dedup load and `dedup.store()`, under the `EventStore` context-manager write lock | Lock serialization ensures no concurrent mutation of events.db during reconciler reads/writes; the in-memory `DedupDir` is fresh and complete (all articles projected) |
| **R2 auto-fix** — checkpoint item `done` if dedup has a `done` record for the same canonical URL | The checkpoint is stale/pending only because projection never reached it (e.g. crash between publish and project); dedup truth supersedes checkpoint |
| **R3 quarantine gap** — checkpoint item `pending`/`failed` with NO matching dedup record → `_quarantine()` with `failure_type="reconcile_gap"` and `source="reconciler"` | The item was created during publish but never projected; partial publish or upstream failure is the likely cause |
| **R4 history gap** — report-only RECON log, no auto-fix, no quarantine | publish-history.json is an independent log; reconciler can't determine whether missing dedup = never projected vs already handled by a later manual operation |
| **R8 clear** — when a previously-quarantined item's canonical URL now has a `done` dedup record → `_clear_quarantine()` | The dedup now covers the URL, so the gap is resolved; removing the quarantine prevents stale entries |
| **R10 skip** — checkpoint items whose canonical URL appears in any open `reconcile_gap` quarantine → skip auto-fix attempt | Don't repeatedly try to fix items already escalated; reduces log noise and dedup lookups |
| **R9 schema** — `row_id TEXT` nullable column on `quarantine_log`, for reconciler entries that correspond to checkpoint item IDs | `run_id` already exists; `row_id` stores the checkpoint `item.id` to make quarantine entries traceable back to their checkpoint item |
| **RECON.log** — append-only flat file at `<cache_dir>/RECON.log`, same rotation-not-required policy as other logs (R5) | Simpler than structured storage; aligns with existing log conventions |
| **Makefile gating** — `make reconcile-check` runs `publish-backlinks --reconcile-all` and fails if exit != 0 (R11, R12) | Gating is on the reconciler's own report, not on findings count; exit 0 confirms the reconciler ran, not that zero gaps exist |

### Resolved During Planning

- *Does the reconciler need its own EventStore instance?* — No. Runs inside `_project_all()`'s existing `with EventStore(...)` block; reuses the same connection for `_quarantine()` / `_clear_quarantine()`.
- *How does the reconciler access checkpoint files?* — Checkpoint files live in `<cache_dir>/checkpoint/`. The reconciler reads them during `_project_all()`. The `checkpoint` module uses file-level locking; the reconciler uses read-only access (no modification to checkpoint files from the reconciler — checkpoint writes happen through `checkpoint.update_item()` API only).
- *Dedup is in-memory during `_project_all` — how does the reconciler query it?* — The `DedupDir` object is a dict-like cache. The reconciler calls `dedup.get(canonical_url)` which returns the record or `None`.
- *Quarantine entry lifecycle: who cleans up old reconciler-gap entries?* — R8 auto-clear: when the reconciler finds a matching dedup record for a quarantined item, it calls `_clear_quarantine()`. Old stale entries are cleared by the reconciler on each run rather than by a separate cleanup command.
- *History JSON path resolution?* — Use the same env-honoring path resolver as the publisher (mirror `store.py`'s config resolution). Tolerate missing file (empty).
- *Which canonicalization function?* — `backlink_publisher._util.url.canonicalize_url` (same as all other modules).

### Deferred to Implementation

- Whether checkpoint reading uses the existing `checkpoint.load_checkpoint()` API or a new bulk reader (depends on checkpoint data layout settled by the concurrent refactor).
- Exact RECON.log line format — settle during implementation, keeping it machine-parseable (`key=value` or JSONL as the existing convention dictates).
- Whether `--reconcile` runs through the existing CLI dispatcher or requires a separate entrypoint (scoping against the in-flight publish-backlinks refactor).
- Whether `--reconcile` implies `publish-backlinks` implicitly (i.e. runs a full project + reconcile) or only triggers the reconcile pass against existing checkpoints.

## Implementation Units

### Implementation order map

| Order | Unit | Depends on |
|-------|------|------------|
| 1st | **U1: Reconciler core + schema** | publish-backlinks refactor landed |
| 2nd | **U2: Checkpoint←dedup auto-fix + quarantine** | U1 |
| 3rd | **U3: History reverse-check** | U1 (same pass) |
| 4th | **U4: CLI args + RECON.log** | U2, U3 |
| 5th | **U5: Dashboard gap display** | U1 |
| — | **UVI: Makefile gating** | U4 |

- [x] **Unit 1: Reconciler core + schema — `events/reconcile.py` extension + schema migration**

**Goal:** Extend `ReadProjectionResult` with a reconciliation summary. Add `row_id` column to `quarantine_log`. Wire the reconciler into `_project_all()` under the existing lock.

**Requirements:** R1 (same-pass), R8 (clear-while-same-lock), R9 (row_id), R5 (RECON log creation).

**Dependencies:** publish-backlinks concurrent refactor landed (the shape of `_project_all()` and checkpoint directory must be settled).

**Files:**
- Modify: `src/backlink_publisher/events/reconcile.py`
- Modify: `src/backlink_publisher/events/schema.py` (add `row_id TEXT` to `quarantine_log` — nullable)
- Create: `src/backlink_publisher/events/reconcile_log.py` (RECON.log writer — thin wrapper over `open(..., 'a')` with a simple `log_event(event_type, **fields)` function)
- Test: `tests/test_reconcile_core.py`

**Approach:**
- **ReconciliationSummary dataclass** (in `reconcile.py`):
  ```python
  @dataclass
  class ReconciliationSummary:
      auto_fixed: int              # count of checkpoints auto-fixed
      quarantined: int             # count of items quarantined as reconcile_gap
      cleared: int                 # count of previously-quarantined items cleared
      history_gaps: int            # count of history-published URLs missing from dedup
      history_checked: int         # total history entries checked
      total_checkpoints: int       # total checkpoint items scanned
  ```
  Merge into `ReadProjectionResult` via a `reconciliation: ReconciliationSummary | None` field (default `None` for backward compat when reconciler doesn't run).

- **`_reconcile_pass(store, dedup, cache_dir) → ReconciliationSummary`** — runs inside `_project_all()`'s store context, after all `_project_one()` calls complete but before `dedup.store()`:
  1. Resolve checkpoint dir: `<cache_dir>/checkpoint/`
  2. If checkpoint dir absent → return empty summary (nothing to reconcile)
  3. Scan all checkpoint item files (TBD format settled by the refactor)
  4. Load quarantine_log rows where `source='reconciler'` (for R10 skip)
  5. For each checkpoint item:
     a. Compute `canonical_url = canonicalize_url(item.target_url)`
     b. If canonical_url is in the quarantine skip-set → skip (R10)
     c. Look up canonical_url in `dedup.get(canonical_url)`
     d. If dedup record exists AND its status is `done`:
        - `checkpoint.update_item(item.id, status='done')` (via the existing checkpoint API)
        - If item had a quarantine entry → `_clear_quarantine()` with the item's run_id (R8)
        - `log_event("auto_fix", item_id=item.id, ...)` to RECON.log (R5)
        - Increment auto_fixed
     e. Else (no dedup record for a pending/failed checkpoint):
        - If item is "stale" (threshold TBD — e.g. created_at older than the current run minus some grace period) → `_quarantine()` with `failure_type="reconcile_gap"`, `source="reconciler"`, `row_id=item.id`, `run_id=item.run_id`, `raw_payload_json={"canonical_url": ...}` (R3)
        - Increment quarantined
  6. Also: scan quarantine_log for existing `reconcile_gap` entries whose canonical_url now HAS a dedup record (but whose checkpoint item is already gone / was already fixed outside the reconciler) → `_clear_quarantine()` (R8, covers edge cases where the checkpoint was manually resolved)
  7. Return `ReconciliationSummary`

- **`_project_all()` integration:**
  ```python
  # After all _project_one calls, before dedup.store():
  reconciliation = _reconcile_pass(store, dedup, cache_dir)
  # ... then dedup.store(), then:
  return ReadProjectionResult(events=events, dedup_count=..., dedup_file_count=...,
                              reconciliation=reconciliation)
  ```

- **Schema migration** (`events/schema.py` — schema version bump + ALTER TABLE):
  ```sql
  ALTER TABLE quarantine_log ADD COLUMN row_id TEXT;
  ```
  Add to the schema-version upgrade block (detect current version → apply). `row_id` is nullable (existing rows get `NULL`).

- **RECON.log writer** (`events/reconcile_log.py`):
  ```python
  def log_event(cache_dir: str, event_type: str, **fields):
      """Append one line to RECON.log. Never raises."""
  ```
  Line format TBD at implementation (e.g. `key=value | key=value` or JSONL). Logs under `<cache_dir>/RECON.log`. One line per auto-fix/quarantine/clear/history-gap event.

**Patterns to follow:** `events/reconcile.py` existing `_project_one` + `_project_all` pattern; `events/store.py` `_quarantine()` / `_clear_quarantine()`; `_util/url.py` `canonicalize_url`; `events/schema.py` version migration pattern.

**Test scenarios:**
- Happy: seeded checkpoint items (all matching dedup) → all auto-fixed, `auto_fixed > 0`, `quarantined == 0`.
- Happy: seeded checkpoint items + some without dedup match → those get quarantined as `reconcile_gap`.
- Edge: no checkpoint directory → empty summary, no raise.
- Edge: all checkpoints already `done` → empty summary.
- R10: a checkpoint whose canonical URL has a `reconcile_gap` quarantine entry → skipped (not auto-fixed, not re-quarantined).
- R8: a previously-quarantined item now has dedup match → cleared from quarantine + auto-fixed.
- R9: new `row_id` column present after migration; reconciler quarantine entries have non-null `row_id`.
- Integration: `_project_all()` returns a `ReadProjectionResult.reconciliation` with correct counts.
- Error: `canonicalize_url` raises on a garbled URL → logged as a quarantine, reconciler does not crash.
- Error: RECON.log write fails → reconciler degrades gracefully (continues, does not raise).

**Verification:** `_project_all()` runs with and without checkpoint seeds; auto-fixes and quarantines produce correct summary counts; quarantine_log entries include `row_id`; RECON.log has one line per event; `_project_all()` with no reconciler work returns `reconciliation=None`.

---

- [x] **Unit 2: Checkpoint←dedup auto-fix — detailed cross-reference logic (extends U1)**

**Goal:** Implement the full cross-reference logic introduced in U1 — dedup lookup, `checkpoint.update_item()`, and the RECON.log event for each auto-fix. This unit refines U1's `_reconcile_pass()` with production-quality matching, coverage of edge cases, and the `_clear_quarantine()` integration.

**Requirements:** R2 (auto-fix), R5 (RECON log), R8 (clear quarantine), R10 (skip quarantined).

**Dependencies:** U1.

**Files:**
- (same files as U1 — `reconcile.py` is the implementation target)
- Test: `tests/test_reconcile_autofix.py`

**Approach:**
- **Matching rule:** A checkpoint item with `status in {pending, failed}` and canonical URL `c` is auto-fixed if `dedup.get(c)` returns a record with `.status == 'done'`. If dedup record exists but status is `pending` or `failed`, the checkpoint is left as-is (dedup hasn't completed either).
- **`checkpoint.update_item()` call:** wraps a single API call; uses the `checkpoint` module's existing function (the concurrent refactor will expose `update_item(id, status=...)`). The reconciler calls this after confirming the match.
- **RECON.log event:** `auto_fix item_id=<id> run_id=<run_id> url=<canonical_url> old_status=<pending|failed> new_status=done`
- **Quarantine clearing (R8):** Before the reconciler runs its main pass, it loads active reconciler-gap quarantine entries. For each: check dedup for the `canonical_url` stored in `raw_payload_json`. If match found → `_clear_quarantine()` first (so the same item also gets auto-fixed in the main pass if the checkpoint still exists).
- **Skip set (R10):** Build a `set(canonical_url for c in reconciler-gap quarantines)` before iterating checkpoints. Items whose URL is in this set are skipped entirely (no look-up, no auto-fix attempt).

**Matching edge cases:**
- Canonical URL equivalence (`canonicalize_url` handles trailing slashes, `utm_*`, `www` normalization, protocol) — same function used everywhere else in the codebase.
- Multiple checkpoint items with the same canonical URL → each independently checked; if both match dedup, both get auto-fixed.
- Checkpoint item with no `target_url` (edge case from a crash) → skip (cannot canonicalize), but record as a `bad_row` in summary.

**Test scenarios:**
- Happy: checkpoint item `pending`, dedup `done` → auto-fixed to `done`, RECON.log line emitted.
- Happy: checkpoint item `failed`, dedup `done` → auto-fixed (recovery from transient failure).
- Edge: checkpoint `pending`, dedup not present → no auto-fix, quarantine candidate (delegated to U1's stale check).
- Edge: checkpoint `pending`, dedup record present but also `pending` → no auto-fix (both paused).
- Edge: two checkpoint items, same URL, one `pending` one `failed` → both auto-fixed.
- R10: quarantined item in skip set → neither auto-fixed nor re-quarantined.
- R8: previously-quarantined item now has dedup match → cleared from quarantine_log.
- Error: `checkpoint.update_item()` raises → caught, no crash (item left as-is, RECON.log error event).

**Verification:** Checkpoint items with matching dedup records are reliably set to `done`; quarantine is cleared when appropriate; quarantined items are skipped; fix is atomic per item.

---

- [x] **Unit 3: History reverse-check — publish-history.json vs dedup store**

**Goal:** Inside the same reconciler pass, compare `publish-history.json` published URLs against the in-memory dedup store. Report missing entries to RECON.log. No auto-fix, no quarantine.

**Requirements:** R4 (history gap detection), R5 (RECON log).

**Dependencies:** U1 (reconciler pass infrastructure, RECON.log writer).

**Files:**
- Modify: `src/backlink_publisher/events/reconcile.py` (add `_reconcile_history_pass()`)
- Test: `tests/test_reconcile_history.py`

**Approach:**
- Called from `_reconcile_pass()` after the checkpoint←dedup pass completes, still inside the same `EventStore` context.
- Load `publish-history.json` from the env-resolved config dir (same resolver as `_project_all`). Tolerate missing file (skip).
- For each history entry with `status == "published"`:
  1. Extract all `article_urls` (the URLs that were supposedly backlinked).
  2. `canonical_url = canonicalize_url(published_url)`.
  3. Check `dedup.get(canonical_url)`.
  4. If dedup returns `None` (no record for this URL) → increment `history_gaps`, log to RECON.log.
  5. If dedup returns a record → increment `history_checked` (the positive case).

- **RECON.log event format:**
  `history_gap history_id=<id> url=<canonical_url> published_at=<timestamp>`
  `history_ok   history_id=<id> url=<canonical_url>`

- Merge results into `ReconciliationSummary.history_gaps` and `ReconciliationSummary.history_checked`.

**Why no auto-fix:** A history-published URL absent from dedup could mean:
- The publish never completed (aborted mid-publish)
- The URL was manually removed from dedup
- The URL was handled through a different mechanism

None of these is safe to auto-fix without human judgment. The RECON log alerts the operator.

**Test scenarios:**
- Happy: history with 3 published URLs, all present in dedup → `history_gaps=0, history_checked=3`.
- Gap: history with 2 URLs, 1 missing from dedup → `history_gaps=1`, RECON.log has the gap line.
- Edge: missing `publish-history.json` → skip (no raise).
- Edge: history entry with no `article_urls` → skip (no URL to check).
- Edge: history entry with `status != "published"` → skip.
- Edge: `publish-history.json` has `null` in `article_urls` → skip (canonicalize would crash).
- Integration: reconciler summary reflects combined checkpoint + history results.

**Verification:** History entries are correctly cross-referenced; missing URLs are logged; present URLs increment `history_checked`; reconciler does not crash on edge inputs.

---

- [x] **Unit 4: CLI flags — `--reconcile` / `--reconcile-all` + RECON.log output**

**Goal:** Add CLI flags to `publish-backlinks` that trigger the reconciler's JSONL report to stdout. The reconciler always runs (U1 ensures it runs inside `_project_all()`); the flags control what is reported and with what level of detail.

**Requirements:** R3 (CLI arguments), R5 (RECON.log output), R6 (JSONL format), R12 (Makefile gate).

**Dependencies:** U2 (auto-fix), U3 (history check).

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py`
- Create: `src/backlink_publisher/cli/reconcile_report.py` (the report formatting logic)
- Test: `tests/test_cli_reconcile.py`
- Modify: `Makefile` (add `reconcile-check` target)

**Approach:**
- **CLI args** (add to the existing `publish_backlinks.py` argparse):
  ```python
  parser.add_argument("--reconcile", metavar="RUN_ID",
                      help="Output reconciliation gap report for a specific run")
  parser.add_argument("--reconcile-all", action="store_true",
                      help="Output reconciliation gap report for all runs")
  ```
  Note: these are flags on `publish-backlinks`, not separate entrypoints.

- **Behavior:**
  - Reconciler always runs inside `_project_all()` (U1). CLI flags add a post-`_project_all()` report phase.
  - If `--reconcile` or `--reconcile-all` is passed → after the normal publish-backlinks output (JSONL to stdout), append a reconciliation report block (also JSONL) to stdout. The report covers:
    - Each quarantined `reconcile_gap` entry (R3 gap detail)
    - Each auto-fix (context: how many were fixed)
    - Each history gap (if any)
    - Total summary line
  - If neither flag is passed → reconciler runs silently (RECON.log only, no stdout).

- **Report format (JSONL):**
  ```jsonl
  {"event":"reconciler_auto_fix","item_id":"...","run_id":"...","url":"...","old_status":"pending","new_status":"done"}
  {"event":"reconciler_gap","item_id":"...","run_id":"...","url":"...","created_at":"...","quarantine_id":"..."}
  {"event":"reconciler_history_gap","history_id":"...","url":"...","published_at":"..."}
  {"event":"reconciler_summary","auto_fixed":1,"quarantined":0,"cleared":0,"history_gaps":1,"history_checked":5,"total_checkpoints":12}
  ```
  Each line is a separate JSON object. The summary line is always the last line.

- **`--reconcile <RUN_ID>` filtering:** Only include entries from the specified run_id.
- **`--reconcile-all`:** Include entries from all runs.

- **Makefile target:**
  ```makefile
  .PHONY: reconcile-check
  reconcile-check:
  	@echo "Running reconciliation check..."
  	$(PYTHON) -m backlink_publisher.cli.publish_backlinks --reconcile-all \
  	  --config $(CACHE_DIR)/config.yaml >/dev/null && \
  	  echo "RECONCILE OK" || \
  	  (echo "RECONCILE FAILED" && exit 1)
  ```

  **Note (R12):** The target does NOT fail on findings (it fails only if the CLI itself errors). Pass/fail is determined by exit code 0 vs non-0 from the reconciler. If the reconciler runs and finds gaps, it exits 0 (the report is valid). Gate failures come from crashes or assertion errors.

- **RECON.log output:** Already handled by U1/U2/U3. CLI does not duplicate to RECON.log (the reconciler pass already wrote there). The CLI report is a FORMATTED READ of what the reconciler produced.

  Wait — the reconciler runs inside `_project_all()`. By the time CLI formatting runs, the reconciler summary is already returned. So the CLI reads `ReadProjectionResult.reconciliation` and formats it. The RECON.log is written during the pass; the CLI stdout is a separate view.

**Patterns to follow:** `cli/publish_backlinks.py` existing argparse pattern; `cli/equity_ledger.py` for `write_jsonl`; `cli/report_anchors.py` for stderr summary lines.

**Test scenarios:**
- Happy: `publish-backlinks --reconcile-all` with seeded checkpoints → stdout has JSONL lines in the report format; exit 0.
- Happy: `publish-backlinks` (no flag) → normal output only, no reconciler report.
- Happy: `publish-backlinks --reconcile <run_id>` → only entries for that run_id in the report.
- Edge: `--reconcile <nonexistent_run_id>` → report with zero entries, summary line shows no work.
- Edge: no reconciler work happened → report has only the summary line (all zeros).
- Integration: RECON.log is written DURING `_project_all()` (before the CLI report), so `cat RECON.log` after a reconcile shows all events.
- Makefile: `make reconcile-check` passes when reconciler runs cleanly; fails if the CLI crashes.
- Error: bad `--reconcile` value → exit 1 (`UsageError`).

**Verification:** CLI flags produce valid JSONL on stdout; normal (no-flag) output unchanged; RECON.log independently written during the pass; Makefile target gates correctly.

---

- [x] **Unit 5: Dashboard gap display — health route + template**

**Goal:** Display the reconciliation gap count on the Health Dashboard, with a warning banner if gaps exist. Follows the "never 500" invariant of the health route.

**Requirements:** R7 (dashboard display).

**Dependencies:** U1 (ReconciliationSummary available from `ReadProjectionResult`).

**Files:**
- Modify: `webui_app/routes/health.py`
- Modify: `webui_app/templates/health.html`
- Test: extend `tests/` health route coverage

**Approach:**
- The health route currently calls `cli_runner.run("--publish-backlinks ...")` or similar. It needs access to the reconciler summary. Since the reconciler runs inside `_project_all()`, the health route could either:
  a. Run a lightweight reconciler check (read checkpoint files + quarantine_log, no full project) — simpler, independent of the publish flow
  b. Read the last run's `ReadProjectionResult.reconciliation` from a stored/reported state

  **Decision:** Use approach (a) — the health route runs a **read-only reconciler check** that scans checkpoint files and quarantine_log to compute a gap count. This avoids coupling to the publish pipeline and runs independently:

  ```python
  def _get_reconciliation_gaps(cache_dir: str) -> dict:
      """Read-only count of pending checkpoints and unreconciled quarantine entries.
      Returns {'pending_checkpoints': int, 'quarantine_gaps': int}
      Returns empty dict if any error (never raises, never 500s)."""
  ```

  This runs inside the health route's existing `try/except` (fallback to no data on error). It does not use the reconciler pass — it's a lightweight query that checks:
  - How many checkpoint items are `pending` or `failed` (can't auto-fix, indicates a gap)
  - How many `reconcile_gap` entries are in quarantine_log

- **Template integration** (`templates/health.html`):
  ```html
  {% if reconciliation_gaps %}
  <div class="warning-banner">
    <span class="warning-icon">⚠️</span>
    <span>{{ reconciliation_gaps.pending_checkpoints }} pending checkpoints,
          {{ reconciliation_gaps.quarantine_gaps }} reconciler gaps</span>
    <a href="/docs/reconciliation">Learn more</a>
  </div>
  {% endif %}
  ```

  Only rendered if gap count > 0. Zero → no banner (avoid alarm fatigue).

- **Never 500 invariant:** The check is wrapped in `try/except Exception` and degrades to empty dict on any error.

**Test scenarios:**
- Happy: no gaps → route renders without banner.
- Gap: seeded quarantine entries + pending checkpoints → banner with correct counts.
- Error: checkpoint dir corrupted → graceful degradation (no banner, no 500).
- Integration: route still renders when reconciler has never run (no checkpoint dir, no quarantine entries → empty dict).
- Regression: health route non-reconciler sections (site health, etc.) unchanged.

**Verification:** Banner appears only when gaps exist; route never 500s; gap count matches quarantine_log count.

---

### System-Wide Impact

- **Interaction graph:** The reconciler runs inside `_project_all()`'s existing `EventStore` context → same write lock, same connection. Uses `_quarantine()` / `_clear_quarantine()` for stateful gap tracking. Adds a new `row_id` column to `quarantine_log` (nullable, backward compatible). RECON.log is a new append-only file in the cache dir.
- **Error propagation:** Reconciler errors are caught inside `_reconcile_pass()` → it returns a partial summary rather than crashing `_project_all()`. RECON.log write failures are swallowed (log degraded, no pipeline impact). Schema migration follows existing versioned upgrade path.
- **State lifecycle risks:**
  - RECON.log grows unbounded (same as other logs) — same rotation-not-required policy.
  - `quarantine_log` gains reconciler entries alongside projector entries — no conflict (different `source` values).
  - `row_id` added as nullable column — no migration cost for existing rows.
  - Checkpoint items are mutated by `checkpoint.update_item()` (changing status). The reconciler always changes status from `pending`/`failed` to `done`, which is monotonic (no rollback needed).
- **API surface parity:** CLI flags on existing `publish-backlinks` only. No new entrypoint in v1.
- **Unchanged invariants:** events.db core schema, projector, dedup store format are untouched. The reconciler reads but does not write checkpoint files directly (uses the checkpoint API). Dedup is read-only during reconcile (writes happen only through `_project_one()` and `dedup.store()`).

### Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| publish-backlinks concurrent refactor changes `_project_all()` signature or checkpoint file format | **This plan's dependency**: lock step with the refactor. Implementation starts only after the refactor lands. |
| Reconciler too slow inside the write lock (blocks other writers) | Reconciler is read-intensive (dedup is in-memory, checkpoint files are small). The write lock is held for the duration of `_project_all()` already — reconciler adds a bounded linear scan. |
| `checkpoint.update_item()` not yet exposed by the refactored checkpoint module | Implement stub at implementation time; the refactor's contract includes item-status writes. |
| `canonicalize_url` raises on malformed URLs in checkpoint items or history | Caught per-item in `_reconcile_pass()` — isolated log, not a batch failure. |
| RECON.log grows unbounded | Same policy as other app logs: periodic archive by the operator (not in scope for v1). |
| Health route's `_get_reconciliation_gaps()` reads checkpoint files concurrently with `_project_all()` | Checkpoint files are `pending→done` atomic updates (file-level locking via the checkpoint module). The health route's read-only scan is best-effort (minor staleness accepted). |
| `--reconcile-all` doesn't produce findings on a clean system but Makefile gating expects success | The gate checks exit code 0 (reconciler ran successfully) not findings count (R11). A clean system → summary with all zeros → exit 0 → pass. |
| `row_id` colocation: the new column shares `quarantine_log` with projector entries (which don't use `row_id`) | Nullable column; projector entries get `NULL`. No index needed for v1. |

### Documentation / Operational Notes

- Add `--reconcile` / `--reconcile-all` flags to the CLI `--help` text and AGENTS.md entrypoint table.
- Operator runbook: "Run `publish-backlinks --reconcile-all` as a cross-check after any publish run to detect gaps. Gaps in the reconciler report indicate checkpoint items that were created during publish but never projected to the dedup store."
- RECON.log is append-only, lives alongside other logs in the cache dir. One line per event (auto-fix, quarantine gap, history gap). Machine-parseable.
- `--reconcile` is a reporting flag on `publish-backlinks`. The reconciler always runs (inside `_project_all`). The flag only controls stdout output.
- Quarantine entries from the reconciler use `source="reconciler"`, `failure_type="reconcile_gap"`, and `row_id=<checkpoint_item_id>`.

### Sources & References

- **Requirements document:** [docs/brainstorms/2026-05-27-readtime-reconciliation-hub-requirements.md](file:///Users/dex/YDEX/INPORTANT%20WORK/%E5%A4%96%E9%93%BE/backlink-publisher/backlink-publisher/docs/brainstorms/2026-05-27-readtime-reconciliation-hub-requirements.md)
- **Related plan:** `docs/plans/2026-05-26-001-feat-dual-state-divergence-auditor-plan.md` (close architectural neighbor — same code area, complementary R3 join)
- **Related code:** `events/reconcile.py` (`_project_all`, `_project_one`), `events/store.py` (EventStore, `_quarantine`, `_clear_quarantine`), `events/schema.py` (quarantine_log), `_util/url.py` (`canonicalize_url`), `webui_app/routes/health.py` (dashboard), `webui_app/templates/health.html`, `cli/publish_backlinks.py` (CLI entrypoint), `ledger/checkpoint.py` (checkpoint API)
- **Institutional learnings:** `publish-history-helper-invariant-2026-05-20`, `same-pass-reconciler-lock-scope-2026-05-27`
