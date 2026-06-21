# Plan: End-to-end pipeline optimization & stage-specific integration hardening

**Status:** active  
**Date:** 2026-06-08  
**Origin:** user request; author continuing from prior diagnostics on CLI shells, EventStore, content.fetch, linkcheck, quality-gate, replan-dead, validate/engine  

## Executive Summary

The current backlink-publisher CLI pipeline (`plan-backlinks | validate-backlinks | publish-backlinks` plus sidecar stages like `replan-dead | quality-gate | plan-gap | remediation-queue | recheck-backlinks`) is functionally correct but exhibits four recurring O(n²) / redundant-I/O anti-patterns that dominate wall-clock time at scale. It also has weak cross-sidecar coordination when the user composes heurtistic feature stages into an E2E deploy script instead of a shell pipe. This plan targets throughput, latency, resource utilization, and error-handling robustness without touching any adapter registration surface or breaking the existing CLI contract.

| Target | Baseline | Optimized pattern | Estimated impact |
|---|---|---|---|
| quality-gate uniqueness | N × full-table SELECT + Jaccard per row | SQLite-backed group-by + prefix-indexed in-memory cache | 10–40× faster at 500+ articles |
| replan-dead / plan_gap per-row SQL | N separate query() calls per event | Bulk fetch → in-memory groupby / dict | Eliminates N×1 round-trip |
| EventStore connect overhead | New PRAGMA + schema-upgrade per query | Warm reader reuse within process lifetime | 3–5× lighter reads |
| Config TOML parse / banner emit | Re-parse/config_echo per CLI in each stage | Singleton + explicit reset API | Saves ~5–10 ms per pipe stage |
| Link-check / fetch batching | Fixed 10/5 workers, no shared budget | Unified HTTP budget with backpressure | Protects target sites; ~20% latency drop |
| Disk-cache cross-CLI sharing rules | import-time global; no atomic refresh surface | Explicit cache-manager singleton with reset hook | 30% cache-hit gain in long sessions |
| Error classification cost | Generic `except Exception` + regex/traceback | Typed exception gating + `__class__.__name__` fast path | Improves log-tail latency |

## Stage 1 — Data Ingestion

### 1.1 JSONL parsing — zero-cost fast path
**Current state:** every CLI shell calls `read_jsonl()` → `json.loads()` per line.
**Optimization:** add `orjson` under `[project.optional-dependencies].fast-io`, the shell does `try: from orjson import loads as json_loads; except ImportError: from json import loads`. Same for output. The strict/non-strict / empty-input contract is preserved; only the hot inner loop changes.

### 1.2 Structured empty-stream contract
**Current state:** each CLI independently decides whether empty input is exit-2 (schema violation) or exit-0 (advisory silent success).
**Optimization:** add `_consume_lines(src)` helper in `backlink_publisher._util.jsonl` returning `(rows, empty_reason)`. Caller dispatches to the appropriate code path. Reduces duplicated edge-case logic across plan_*, validate, quality-gate, replan-dead, plan-gap.

## Stage 2 — Preprocessing

### 2.1 EventStore reader connection warm-up + reuse
**Current state:** `EventStore.query()` opens + PRAGMAs + schema-upgrade per call. replan-dead runs 3+ queries; remediation_queue hits it per action in long sessions.
**Optimization:** add `get_read_connection()` returning a cached thread-local reader. Writers continue to use `connect()` / `connect_immediate()`. Schema-upgrade is idempotent, so reader reuse after the first touch is safe.
**Configuration knob:** `BACKLINK_EVENTSTORE_REUSE_CONN=1` (default 0 in CLI; 1 in WebUI). Invalidate on `os.fork()` via a `pid` sentinel.

### 2.2 Bulk-fetch event payloads (quality-gate uniqueness, replan_dead counting)
**Root cause (quality_gate.py):** `_check_content_uniqueness` deserializes every `publish.confirmed` event once per seed row — O(n × m).
**Optimization:** fetch all qualifying events once at startup into `dict[sha_prefix -> list[body_sha]]` and a parallel `dict[target_url -> live_dofollow_count]`. Single pass + prefix-dict membership. For replan_dead, use a grouped SQL:
```sql
SELECT target_url, COUNT(*) AS cnt
FROM events
WHERE kind = 'publish.confirmed'
  AND json_extract(payload_json, '$.live_url') IS NOT NULL
GROUP BY target_url
```
Rolls unbounded N×1 round-trips into one query.

### 2.3 SHA reflex fast-path in quality-gate
If two seeds carry identical content, their full SHA256 matches; similarity with themselves trivially exceeds any sensible threshold.
**Optimization:** keep a per-run `set[sha256]`. Identical-content hits block without Jaccard. Only distinct-but-prefix-collision bodies pay Jaccard. Seen-hash memo avoids recomputing similarity for either side of a already-scored pair.

### 2.4 Link-check / content-fetch unified HTTP budget
**Current state:** linkcheck uses 10 workers; content-fetch uses 5, both independent. A composed run can transiently double the dial budget on target sites.
**Optimization:** introduce a shared process-wide semaphore (cap ≈ 12) at `_util.net_safety / fetch.http` layer. Each `verify_url_*` call claims one slot before submission. Memory footprint is stable; concurrent reads via WAL are unaffected.

## Stage 3 — Model Training (LLM prompt construction)
No ML training exists. The closest analogue is `--quality-llm` per-row calls.
### 3.1 Async LLM dispatch for quality-gate
**Optimization:** launch LLM calls under a bounded ThreadPoolExecutor (`workers=4`, knob `--quality-llm-workers`). Preflight-short-circuit: if anchor-density delimiter already failed, the row is skipped before the LLM call. Fail-open semantics preserved.

## Stage 4 — Evaluation

### 4.1 Cross-CLI URL verification coordination
The existing `content_fetch._CACHE` + disk_cache is correct. Document that `plan-backlinks --no-fetch-verify | validate-backlinks` is the accepted "skip recompute" pattern for staging and ensure both CLIs emit the same `CacheHit` counter name (`cache_hits`) so operators can verify the handoff. No persistence format change; this is a contract clarification.

### 4.2 Plan-check parallelism
`plan-check` resolves paths and shas serially. Both use `git cat-file -e` / `git merge-base` per item. For large plans with dozens of claims:
**Optimization:** submit claims to `concurrent.futures.ThreadPoolExecutor` (workers=8) and aggregate drift after `as_completed`. Exit code semantics unchanged because result merging is post-hoc.

## Stage 5 — Deployment / Runtime Hardening

### 5.1 Exponential backoff + jitter for HTTP retries
**Current state:** linear backoff `delay * (attempt + 1)`, no Retry-After honor.
**Optimization:** `delay = min(MAX_BACKOFF_S, BASE * 2 ** attempt) + uniform(0, 0.5 * BASE)`. Honoring `Retry-After` overrides the computed delay. Prevents thundering-herd during scheduled refreshes.

### 5.2 Process-lifetime connection pool for EventStore
**Current state:** costly per-query setup for CLI; measurable for WebUI session refreshes and remediation_queue list endpoints.
**Optimization:** reader-only cached connection behind `_get_connection()` gated by `pid`. Writer connections remain freshly `connect()`ed per transaction. No shared mutable cursor across writers; correctness preserved.

## Stage 6 — Error Handling

### 6.1 Typed-exception fast path
Replace generic `except Exception` in `linkcheck`, `content.fetch`, `health_metrics` with concrete `(HTTPError, URLError, socket.timeout, ssl.SSLError, OSError)` catch lists. Pre-classify exceptions via helper `_classify_http_exc(exc) → "transient" | "permanent" | "auth"`.
**Impact:** avoids CPython traceback capture in hot paths.

### 6.2 Structured envelope extensibility
Add optional `json_fields: dict[str, Any] | None` to `emit_envelope_and_exit()`. Shells pass `{"url", "platform", "row_index"}` so downstream tooling can filter without regex on stderr. Backward-compatible: omitted fields produce the same payload as today.

## Cross-sidecar deploy-script hardening
When an operator composes scripted daily runs (not a Unix shell pipe), subsequent stages sometimes skip invariants that a pipe would preserve:
- each CLI should emit a single RECON bisect (`validate_reconciliation`, `plan_reconciliation`, `replan_reconciliation` at stdout/stderr boundary) with `input_rows`, `output_rows`, `delta`, `dropped.*`, so a deploy orchestrator can alert on silent-drip instead of silent success;
- `plan-backlinks` EXIT-2 on empty output must remain a loud failure (it is), while `plan-gap` EXIT-0 on empty seeds should also emit a dedicated `empty_seeds` marker on stderr so Netflix-style schedulers (not grounded in pipe semantics) don't silently treat it as success;
- Self-restart coordination: add a stderr line `run_id=<sha256>` printed by every shell; the deploy script stores `last_run_id` next to the pipe's `events.db` footprint so overlapping runs on a cron retry can detect "already covered" and safe-skip.

## Claims
```yaml
claims:
  paths:
    - src/backlink_publisher/cli/quality_gate.py
    - src/backlink_publisher/cli/replan_dead.py
    - src/backlink_publisher/events/store.py
    - src/backlink_publisher/events/kinds.py
    - src/backlink_publisher/content/fetch.py
    - src/backlink_publisher/linkcheck/http.py
    - src/backlink_publisher/_util/jsonl.py
    - src/backlink_publisher/cli/plan_gap.py
    - src/backlink_publisher/cli/plan_check.py
    - src/backlink_publisher/cli/validate_backlinks.py
    - pyproject.toml
    - docs/plans/2026-06-08-001-pipeline-performance-optimization-plan.md
  shas: []
```
