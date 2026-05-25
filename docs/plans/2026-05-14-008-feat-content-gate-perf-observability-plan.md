---
title: Content-fetch gate — performance + observability optimisation
type: feat
status: completed
date: 2026-05-14
completed: 2026-05-14
---

# Content-fetch gate — performance + observability optimisation

## Overview

Plan 007 wired the content-fetch gate into the publish pipeline (PRs #20–#22). It's correct but coarse: each row triggers its own URL batch fetch (5–15s), batch CSV runs with 10 rows over 10 distinct domains take 10× that wall-clock instead of overlapping. Cache is forever-in-process (no TTL — fine for short CLI invocations, problematic for the long-lived webui daemon). No observability beyond per-event recon — operator can't see "how often is the cache helping?" without scraping logs.

This plan tightens those edges: cross-row URL prefetch (single union batch up-front), cache TTL (configurable, default off for CLI / 15 min for webui), and a stats snapshot exposed via recon at end-of-run.

## Problem Frame

Three concrete gaps from plan 007's "minimum viable" wiring:

1. **Sequential per-row batches.** Plan-backlinks main loop processes rows one at a time. Each `_build_links` call invokes `verify_urls_batch` for that row's 6–8 candidate URLs. For a 10-row CSV across 10 different domains, total HTTP time = 10 × (slowest single fetch). The supporting URLs (Wikipedia/MDN/SO/GitHub/HN) re-fetch every batch — wasted because the cache only helps within a single ThreadPoolExecutor invocation if multiple workers race the same URL, not across batches in sequence (cache is consulted but only one batch runs at a time).

2. **No cache TTL.** Webui daemon survives across operator sessions. A URL fetched at 09:00 and cached as `(False, "http_404")` stays cached forever. If the target site fixes the URL by noon, the next save still rejects. CLI invocations are short-lived so TTL doesn't matter for them — but the same module is used by webui.

3. **No aggregate observability.** Each `link_dropped_no_content` event is per-link. Operator running a 100-row batch gets 100+ log lines but no rollup: cache hit rate, p95 fetch latency, reason distribution. The `plan_reconciliation` event reports drops but not gate-internal health.

## Requirements Trace

- R1. A single plan-backlinks invocation processes N rows with at most one HTTP call per distinct URL, regardless of how many rows reference that URL. The 5 supporting URLs are fetched at most once per invocation.
- R2. The in-run cache supports a configurable TTL via two surfaces: `content_fetch.set_default_max_age(seconds)` (Python API for webui startup) and `BACKLINK_GATE_CACHE_TTL_SECONDS` env var (read by webui). Default behavior — no TTL, i.e. infinite cache lifetime within the process — is preserved for CLI.
- R3. `content_fetch.stats_snapshot()` returns a dict with: `cache_hits`, `cache_misses`, `fetches`, `total_latency_ms`, `reason_counts: dict[str, int]`. Updated on every `verify_url_has_content` call. `reset_stats()` test hook clears it.
- R4. `plan-backlinks` emits a `content_fetch_stats` recon event at end-of-run with the snapshot. Operator greps `RECON content_fetch_stats` for one-line health summary.
- R5. No behavior change to existing callers when the new features are not configured. Existing autouse `_mock_content_fetch` fixture continues to default-pass.

## Scope Boundaries

- **Not in scope:** persistent cross-process cache (would need disk-backed store, out of plan-007's "in-memory only" boundary).
- **Not in scope:** webui dashboard route. Recon log + optional one-off curl-able JSON endpoint is enough; full UI deferred.
- **Not in scope:** TTL eviction (memory bound). Cache stays unbounded by entry count — only by TTL. Realistic upper bound: a few thousand distinct URLs per process lifetime, well under any memory concern.
- **Not in scope:** asyncio rewrite. ThreadPoolExecutor stays the concurrency primitive.
- **Not in scope:** distributed stats / Prometheus export. Process-local only.
- **Not in scope:** retroactive metric backfill from existing recon log lines.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/content_fetch.py` (PR #20) — the gate module. `_CACHE: dict[str, CheckResult]` lives at module scope; replace with `dict[str, _CacheEntry]` where `_CacheEntry` carries timestamp.
- `src/backlink_publisher/cli/plan_backlinks.py` (PR #21) — main loop iterates rows; insert cross-row URL collection between row validation and dispatch.
- `webui.py` (PR #22) — startup path is `_scheduler.start()` + `app.run(...)`. Insert `content_fetch.set_default_max_age(int(os.environ.get("BACKLINK_GATE_CACHE_TTL_SECONDS", "900")))` near the entry point.

### Institutional Learnings

- `feedback_recon-level-for-always-on-signals.md` — emit `content_fetch_stats` via `plan_logger.recon` so it's always visible without `--log-level`.
- `feedback_floating-point-tiebreak.md` — latency averaging uses integer milliseconds, not floats, to avoid noise in test assertions.
- `feedback_test-autouse-verify-mock.md` — the autouse mock in conftest must continue working; new fields on the cache shouldn't break existing fixture.

### External References

None warranted.

## Implementation Units

- [ ] **Unit 1: Cache TTL + stats counters in content_fetch.py**

**Goal:** Cache entries gain a timestamp; expired entries get re-fetched. Per-process stats counters track cache hit rate, fetch count, total latency, reason distribution. Single PR, additive — no caller changes required.

**Requirements:** R2, R3

**Dependencies:** None.

**Files:**
- Modify: `src/backlink_publisher/content_fetch.py`
- Modify: `tests/test_content_fetch.py`

**Approach:**
- Replace `_CACHE: dict[str, CheckResult]` with `_CACHE: dict[str, tuple[CheckResult, float]]` where the float is `time.monotonic()` at write time.
- Module-level `_DEFAULT_MAX_AGE_S: float | None = None`. New `set_default_max_age(seconds: float | None)` setter.
- `verify_url_has_content(url, max_age_seconds: float | None = None)`: effective TTL = explicit arg > `_DEFAULT_MAX_AGE_S` > None. On cache hit, check age: if older than TTL, treat as miss.
- Stats counters: module-level dict `_STATS: dict[str, Any]` with keys `cache_hits`, `cache_misses`, `fetches`, `total_latency_ms`, `reason_counts` (`dict[str, int]`). Updated inline in `verify_url_has_content`.
- New `stats_snapshot() -> dict[str, Any]` returns shallow copy with `reason_counts` deep-copied.
- New `reset_stats()` test hook.
- `verify_urls_batch` unchanged externally; benefits from cache automatically.

**Test scenarios:**
- Happy path: `set_default_max_age(0.1)`; first call fetches; sleep 0.2; second call re-fetches (cache miss recorded).
- Happy path: `verify_url_has_content(url, max_age_seconds=0)` always re-fetches (TTL=0 means never cache).
- Happy path: `verify_url_has_content(url, max_age_seconds=None)` (explicit) uses module default.
- Stats: 5 calls — 2 distinct URLs (1 hit + 1 hit + 2 misses + 1 miss-failure) → stats reports 2 hits, 3 misses, 2 fetches, reason_counts = {ok: 2, http_404: 1}.
- Stats: `reset_stats()` clears counters mid-test.
- Stats: `total_latency_ms` accumulates across calls (assert > 0 after a real-ish fetch).
- Edge case: explicit `max_age_seconds=None` falls back to module default, not interpreted as "never expire".
- Edge case: setter accepts None to disable TTL.

**Verification:** new tests + existing 29 tests in test_content_fetch.py all green.

---

- [ ] **Unit 2: Cross-row URL prefetch in plan-backlinks main loop**

**Goal:** Before dispatching any row, collect every URL that will be candidate-fetched across all rows. Issue one big `verify_urls_batch` up front. Subsequent per-row `_build_links` calls hit the cache exclusively.

**Requirements:** R1

**Dependencies:** Unit 1 (no hard dep, but stats observability makes the win visible).

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks.py`
- Modify: `tests/test_plan_backlinks.py`

**Approach:**
- New helper `_collect_candidate_urls_for_row(row, config) -> list[str]`. Mirrors the URL-emission logic in `_build_links` but pure-string, no HTTP. Returns: main_domain + target_url + extra_urls[:2] + (B/C category/detail from config if present).
- In `main()` main loop, after validation but before per-row dispatch: walk validated rows, accumulate union URL set, append the 5 fixed supporting URLs once, call `content_fetch.verify_urls_batch(union_urls, max_workers=10)`. Discard the result — the side effect is cache warming.
- Skip the prefetch when `args.no_fetch_verify` is set (saves the round trip).
- Skip the prefetch when no rows survived validation (empty input case).
- Document the prefetch in a one-line comment pointing to plan 008 Unit 2.
- Recon event `content_fetch_prefetch` emitted once with `n_urls_prefetched` so operator sees the up-front cost.

**Test scenarios:**
- Happy path: 3-row CSV with 3 distinct main_domains → `verify_urls_batch` called once with the union of all candidate URLs; subsequent per-row `_build_links` calls all hit cache (assert via stats: 0 additional fetches after prefetch).
- Happy path: 5-row CSV all targeting same main_domain → prefetch dedupes to 1 main + 5 supporting = 6 unique URLs; not 5 × 6 = 30.
- Edge case: `--no-fetch-verify` → prefetch is skipped, no `content_fetch_prefetch` recon emitted.
- Edge case: all rows fail validation → prefetch is skipped (empty union).
- Regression: existing single-row tests in test_plan_backlinks.py + TestContentFetchGate all green.

**Verification:** before/after stats on a 10-row test fixture: prefetched run has 1 batch fetch + 9 cache hits; non-prefetched (the old behavior) has 10 batch fetches.

---

- [ ] **Unit 3: Stats snapshot + recon emission at end-of-run**

**Goal:** Plan-backlinks emits `content_fetch_stats` recon event at end-of-run with the snapshot. Webui startup wires `BACKLINK_GATE_CACHE_TTL_SECONDS` env var into `set_default_max_age`.

**Requirements:** R3, R4

**Dependencies:** Unit 1.

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks.py` (end-of-run recon)
- Modify: `webui.py` (startup TTL wiring)
- Modify: `tests/test_plan_backlinks.py` + `tests/test_webui_three_url.py`

**Approach:**
- In `plan-backlinks main()`, after the `plan_reconciliation` recon, emit `content_fetch_stats` with `stats_snapshot()` as kwargs. Format: one recon line with full snapshot. Operator grep target: `RECON content_fetch_stats`.
- Reset stats at the start of `main()` so each invocation reports its own counters (otherwise pytest cross-test bleed).
- Webui startup (near `_scheduler.start()`): read `BACKLINK_GATE_CACHE_TTL_SECONDS` env var, default 900 (15 min); call `content_fetch.set_default_max_age(value)`. Skip if `BACKLINK_NO_FETCH_VERIFY=1` is set (gate is bypassed anyway).
- Document the env var in webui's `_resolve_bind_host` neighborhood or near the new code.

**Test scenarios:**
- Happy path: plan-backlinks with mocked gate emits `content_fetch_stats` event at end; parsed JSON contains all expected keys.
- Happy path: stats event reports correct counts after a run with mixed hit/miss/failure cases.
- Webui: `BACKLINK_GATE_CACHE_TTL_SECONDS=120` env var → `_DEFAULT_MAX_AGE_S` set to 120 after webui module import (verified via direct read).
- Webui: env var unset → default 900.
- Webui: `BACKLINK_NO_FETCH_VERIFY=1` set → `set_default_max_age` not called.

**Verification:** recon event renders cleanly + webui startup honors the env.

## System-Wide Impact

- **Interaction graph:** plan-backlinks main loop gains a prefetch phase; `_build_links` per-row calls now mostly hit the cache. Webui startup grows a TTL setter call. No production-code change to PRs #19-#22 contracts.
- **Error propagation:** TTL expiry causes a cache-miss + re-fetch; the second fetch's result is what's stored. If the re-fetch fails differently from the original, the new failure wins.
- **State lifecycle risks:** Cache TTL is per-process — webui restart clears it. CLI process exit clears it. Both expected.
- **API surface parity:** `verify_url_has_content` gains an optional `max_age_seconds` keyword arg; existing callers pass nothing and get the current behavior.
- **Integration coverage:** Unit 2's prefetch is exercised by an end-to-end multi-row test (3-row CSV → 1 batch fetch).
- **Unchanged invariants:** Gate criterion (HTTP 200 + non-empty title), failure-reason taxonomy, the recon-event shapes from plan 007, the autouse-mock contract in `tests/conftest.py`.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Stats counters introduce per-call mutation overhead. | Module-level dict update is sub-microsecond; negligible compared to HTTP latency. Single-threaded GIL keeps it safe (the only writer per process). |
| Cache TTL eviction causes a thundering herd if many URLs expire at once. | TTL is per-URL, not global; expiry distributes naturally over wall-clock. Plus `verify_urls_batch` already concurrency-caps at 5/10 workers. |
| Cross-row prefetch over-fetches URLs that some rows fail validation on. | Prefetch runs after validation; only valid rows contribute URLs. |
| Stats reset at `main()` start clobbers prefetch counters. | Reset BEFORE the prefetch call so prefetch fetches show up in the stats snapshot. |
| Cross-test cache leakage in pytest (autouse mock doesn't clear stats). | Extend the autouse fixture in `tests/conftest.py` to also call `reset_stats()`. |
| Webui hot-reload (Flask debug mode) re-executes startup code and may double-set TTL. | Idempotent setter; second call is harmless. |

## Documentation / Operational Notes

- **CHANGELOG:** "plan-backlinks emits content_fetch_stats recon event at end-of-run. webui caches gate results for 15 min by default (override: BACKLINK_GATE_CACHE_TTL_SECONDS env var)."
- **Operator recon greps:** add `content_fetch_stats` to the runbook list alongside `plan_reconciliation`, `link_dropped_no_content`, `row_dropped_content_gate`.
- **Memory:** add `feedback_content-fetch-perf.md` post-land if cross-row prefetch's performance gain is meaningful in real CSV runs.

## Sources & References

- Direct parent: PRs #20, #21, #22 (plan 007 implementation).
- Plan 007: `docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md` (Scope Boundaries explicitly deferred "cross-row prefetch" + "TTL").
- Memory: `feedback_recon-level-for-always-on-signals.md`, `feedback_test-autouse-verify-mock.md`.
