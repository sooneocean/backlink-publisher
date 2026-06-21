---
title: "feat: Triple-track parallel optimization iteration"
type: feat
status: active
date: 2026-06-21
origin: docs/brainstorms/2026-06-21-triple-track-optimization-requirements.md
claims:
  paths:
    - docs/brainstorms/2026-06-21-triple-track-optimization-requirements.md
  shas:
    - 74730c1
---

# 三軌並行全面優化迭代計畫 — Implementation Plan

## Summary

Three-track parallel optimization for backlink-publisher: **Stabilization** (ship in-flight work and close 9 active plans), **Experience** (immediate operator UX improvements — Vitest JS tests, ESM migration, false-success route fixes, store cleanup), and **Governance** (background type system, adapter error taxonomy unification, doc audit, CI governance). Each track runs at independent cadence so operator value starts flowing Week 1.

**Target version**: v0.3.1+ (incremental, non-breaking)
**Execution window**: 2026-06-21 to 2026-07-19 (4 weeks, ~45 person-hours across 3 parallel tracks)

## Current State Assessment (2026-06-21)

### Uncommitted Work (must ship in Wave S1)

| File | Type | Description |
|---|---|---|
| `pyproject.toml` | modified | +1 line — adds `auto-recover` CLI entrypoint |
| `src/backlink_publisher/events/kinds.py` | modified | +22 lines — new event kinds for channel health |
| `webui_app/health_metrics.py` | modified | +49 lines — new health metrics module |
| `webui_app/routes/health.py` | modified | +15 lines — health route additions |
| `webui_app/templates/health.html` | modified | +96/-1 lines — health dashboard template |
| `src/backlink_publisher/cli/auto_recover.py` | new | Auto-recover CLI module |
| `src/backlink_publisher/health/` | new | Health package (registry + router) |
| `tests/test_auto_recover.py` | new | Auto-recover tests |
| `tests/test_channel_health_registry.py` | new | Health registry tests |
| `tests/test_channel_health_router.py` | new | Health router tests |

### Active Plans (9 total, need assessment)

| Plan | Focus | Est. Completeness |
|---|---|---|
| `2026-06-02-001-refactor-publish-backlinks-decompose-plan.md` | Publish-backlinks decomposition | Unknown — assess |
| `2026-06-02-002-feat-channel-probe-ssrf-hardening-and-notesio-adapter-plan.md` | Channel probe SSRF + notes.io | Unknown — assess |
| `2026-06-04-001-feat-zero-auth-channel-mvp-plan.md` | Zero-auth channel MVP | Unknown — assess |
| `2026-06-06-001-feat-automated-wizard-watch-scoring-wave1-plan.md` | Wizard watch scoring | Unknown — assess |
| `2026-06-07-001-feat-backlink-remediation-queue-plan.md` | Remediation queue | Unknown — assess |
| `2026-06-07-002-feat-replan-dead-pipeline-plan.md` | Replan-dead pipeline | Unknown — assess |
| `2026-06-07-003-feat-quality-gate-plan.md` | Quality gate | Unknown — assess |
| `2026-06-08-001-feat-channel-health-routing-plan.md` | Channel health routing | SHIPPED (this is the uncommitted work) |
| `2026-06-10-001-feat-pipeline-end-to-end-optimization-plan.md` | Pipeline E2E optimization | Unknown — assess |

### JS ESM Migration Status

| File | Status | Notes |
|---|---|---|
| `lib/api.js` | ✅ ESM | Shared layer |
| `lib/dom.js` | ✅ ESM | Shared layer |
| `lib/profiles.js` | ✅ ESM | Shared layer |
| `index.js` | ✅ ESM | Page module |
| `settings.js` | ✅ ESM | Page module |
| `wizard.js` | ✅ ESM | Page module |
| `schedule.js` | ✅ ESM | Page module |
| `bind_channel.js` | ❌ Classic | Legacy — needs ESM migration |
| `channel-binding.js` | ❌ Classic | Legacy — needs ESM migration |
| `copilot.js` | ❌ Classic | Legacy — needs ESM migration |
| `equity.js` | ❌ Classic | Legacy — needs ESM migration |
| `mode_toggle.js` | ❌ Classic | Legacy — needs ESM migration |
| `url_derive.js` | ❌ Classic | Legacy — needs ESM migration |

### Vitest Infrastructure

- ✅ `vitest.config.mjs` already exists at `webui_app/static/js/`
- ✅ `happy-dom` + `vitest ^4.1.8` already installed as devDependencies
- ✅ `package.json` scripts: `test` and `test:watch` already defined
- ✅ Test directory: `tests/js/**/*.test.{js,mjs}`
- ⛔ No tests exist yet (confirmed by finding no `.test.` files)

### Route Modules (31 files, 4,850 total LOC)

Top 5 largest:
1. `channel_bind_save.py` — 436 LOC
2. `pipeline.py` — 368 LOC
3. `llm.py` — 340 LOC
4. `sites.py` — 311 LOC
5. `health.py` — 308 LOC

## Implementation Plan

### Tracks Overview

| Track | Cadence | Owner | Priority |
|---|---|---|---|
| **S** — Stabilization | Weekly ship | Backlog convergence | High (Week 1) |
| **E** — Experience | Continuous | Operator UX | High (continuous) |
| **G** — Governance | Background (no deadline) | Maintenance | Low (ongoing) |

---

## Wave S1 (Week 1: 2026-06-21 → 2026-06-27)

### S1-U1: Ship uncommitted auto-recover CLI and health infrastructure

**Scope**: `auto_recover.py`, `health/` package, all modified files, their tests
**Files**:
- `src/backlink_publisher/cli/auto_recover.py` (new)
- `src/backlink_publisher/health/__init__.py` (new — package init)
- `src/backlink_publisher/health/registry.py` (new — channel health registry)
- `src/backlink_publisher/health/router.py` (new — channel health router)
- `src/backlink_publisher/events/kinds.py` (modified)
- `webui_app/health_metrics.py` (modified)
- `webui_app/routes/health.py` (modified)
- `webui_app/templates/health.html` (modified)
- `pyproject.toml` (modified)
- `tests/test_auto_recover.py` (new)
- `tests/test_channel_health_registry.py` (new)
- `tests/test_channel_health_router.py` (new)

**Verification**:
1. `pytest tests/test_auto_recover.py tests/test_channel_health_registry.py tests/test_channel_health_router.py -v` → all pass
2. `python -m py_compile src/backlink_publisher/**/*.py` → no errors
3. `python -c "from backlink_publisher.cli.auto_recover import main; print('ok')"` → imports clean
4. `plan-backlinks --help` → does not break existing CLI (regression check)

### S1-U2: Ship health dashboard WebUI updates

**Scope**: The modified `health.html`, `health.py`, `health_metrics.py`, `kinds.py`
**Note**: This is part of S1-U1's modified files — they ship together in one PR.

### S1-U3: Assess all 9 active plans — classify as `shipped` / `deferred` / `active`

**Scope**: Read each active plan's frontmatter and implementation status
**Files**: All 9 plans in `docs/plans/2026-06-02-001` through `2026-06-10-001`
**Output**: A `status.md` file at `docs/plans/2026-06-21-triple-track-assessment.md` recording per-plan:
- Current status
- Implementation completeness estimate
- Decision: ship (flip to `shipped`) / defer (flip to `parked` with rationale) / keep active
- For shipped plans: one-paragraph "狀態摘要" in the plan frontmatter

**Verification**:
1. All 9 plans have updated `status:` in YAML frontmatter
2. `grep "^status: active" docs/plans/*.md` returns ≤3 plans

---

## Wave E1 (Week 1-2: 2026-06-21 → 2026-07-04)

### E1-U1: Vitest test suite for lib/ shared layer

**Scope**: Write tests for `lib/api.js`, `lib/dom.js`, `lib/profiles.js`
**Files**:
- `tests/js/lib/api.test.mjs` (new)
- `tests/js/lib/dom.test.mjs` (new)
- `tests/js/lib/profiles.test.mjs` (new)

**Requirements**:
- Target ≥80% line coverage of each module
- Test `fetchJson`, `postJson`, `postForm`, `readCsrf` from api.js
- Test `on`, `delegate`, `qs`, `esc`, `renderBadge` from dom.js
- Test config-form editor and profile functions from profiles.js
- No DOM tests for happy-dom (focus on pure function logic)
- Run via `cd webui_app/static/js && npx vitest run`

**Verification**:
1. `cd webui_app/static/js && npx vitest run` → all tests pass
2. Coverage report shows ≥80% for lib/ modules

### E1-U2: Fix false-success WebUI routes

**Scope**: Identify and fix routes that swallow errors and return 200/success redirect
**Investigation required**: Audit all 31 route modules in `webui_app/routes/` for:
- Empty `try`/`except` blocks that log but don't propagate
- Routes that return `redirect(url_for(...))` after an error condition
- Routes that return `jsonify({"status": "ok"})` even on failure paths

**Files**: Targeted fixes in individual route modules (TBD by investigation)
**Output**: Per-fix documentation of before/after behavior in commit messages

**Verification**:
1. Each fixed route has a test confirming error path returns non-2xx or error body
2. `pytest tests/ -k "route" -v` passes

### E1-U3: ESM migrate legacy classic scripts

**Scope**: Convert `bind_channel.js`, `channel-binding.js`, `copilot.js`, `equity.js`, `mode_toggle.js`, `url_derive.js` from classic <script> to ESM modules
**Files**:
- `webui_app/static/js/bind_channel.js` (rewrite)
- `webui_app/static/js/channel-binding.js` (rewrite)
- `webui_app/static/js/copilot.js` (rewrite)
- `webui_app/static/js/equity.js` (rewrite)
- `webui_app/static/js/mode_toggle.js` (rewrite)
- `webui_app/static/js/url_derive.js` (rewrite)

**Rules**:
- Each module must `import` from `lib/` instead of relying on `window.*` globals
- Cross-component signals use DOM `CustomEvent`, not `typeof window.fn` probes
- No inline `on*` handlers — use `data-action` + delegated listeners
- Corresponding Jinja templates' `{% block page_module %}` must reference the `.js` path correctly

**Verification**:
1. `grep -r "import " webui_app/static/js/*.js | wc -l` == number of ESM modules (should be 11 total: 4 existing ESM + 7 classic migrated = wait, 3 lib + 4 existing page ESM + 7 new migrations = 14... let me recount)
   - Already ESM: `index.js`, `settings.js`, `wizard.js`, `schedule.js` = 4 page modules + 3 lib modules = 7 ESM
   - Classic to migrate: `bind_channel.js`, `channel-binding.js`, `copilot.js`, `equity.js`, `mode_toggle.js`, `url_derive.js` = 6
   - Total after migration: 7 + 6 = 13 ESM modules; 0 classic scripts
2. Manual walkthrough of each affected page in WebUI (hard refresh) — confirm no console errors
3. `npx vitest run` still passes (regression check on lib/ tests)

### E1-U4: Store singleton cleanup hook (v1 — centralized scheduler)

**Scope**: Add idle-timeout cleanup to all 6 module-level singletons in `webui_store/`
**Files**:
- `webui_app/webui_store/__init__.py` (add cleanup scheduler)
- Potentially: each store module if per-store TTL needed

**Design decision** (from requirements doc "Outstanding Questions"):
- **Chosen**: Centralized cleanup scheduler with per-store configurable TTL
- Default TTL: 30 minutes idle
- Cleanup cycle: periodic check (every 5 minutes via background thread or Flask `before_request`)

**Verification**:
1. Integration test: populate stores, wait for idle timeout, verify singleton reset
2. `pytest tests/ -k "store" -v` passes

---

## Wave S2 (Week 2: 2026-06-28 → 2026-07-04)

### S2-U1: Ship/close remaining active plans after assessment

**Scope**: Based on S1-U3 findings, ship or defer any remaining active plans
**Output**: Updated plan frontmatters, zero or ≤3 active plans remaining

### S2-U2: Verify clean tree

**Scope**: `git status` must show zero uncommitted work on main branch
**Output**: `git status` returns clean

---

## Wave E2 (Week 3-4: 2026-07-05 → 2026-07-18)

### E2-U1: Route lifecycle audit and decomposition

**Scope**: Audit all 31 route modules; decompose any >500 LOC
**Files**: Targeted refactoring of top-5 largest route modules
**Rules**:
- Each module stays ≤500 LOC after decomposition
- Shared route utilities extracted to `webui_app/routes/_shared.py` or similar
- Each route module retains a single responsibility

**Verification**:
1. `wc -l webui_app/routes/*.py | sort -rn | head -1` shows no module >500 LOC
2. All existing tests pass after refactoring

### E2-U2: WebUI test coverage expansion

**Scope**: Increase from ~47 test functions to ≥120 test functions
**Files**: `tests/` directory — add route contract tests, error handler tests, CSRF compliance tests
**Focus areas**:
- Route contract tests: each endpoint returns correct status code for both success and error paths
- CSRF compliance: all POST/PUT/PATCH/DELETE routes reject missing/wrong CSRF token
- Error handlers: error pages render correctly with operator messages
- `_settings_channel_binding.html` partial context processor test (csrf_token inheritance)

**Verification**:
1. `pytest tests/ --tb=short -q | tail -5` shows ≥120 test functions
2. CSRF compliance tests pass (including negative tests)

---

## Wave G1 (Weeks 1-4: 2026-06-21 → 2026-07-19)

### G1-U1: Type checking CI infrastructure

**Scope**: Configure mypy/pyright as non-blocking CI warning
**Files**:
- `pyproject.toml` or `setup.cfg` (mypy config)
- `.github/workflows/ci.yml` (add mypy step, non-failing)
- New modules get type hints; gradual adoption plan

**Verification**:
1. CI passes with mypy as warning (non-blocking)
2. `mypy src/backlink_publisher/` produces output without blocking exit code

### G1-U2: Adapter error taxonomy unification

**Scope**: Audit all 30+ adapters in `src/backlink_publisher/publishing/adapters/` for consistent exception hierarchy
**Reference**: `docs/solutions/correctness/adapter-silent-exceptions-resolution.md`
**Output**: Gap report (doc or embedded in the plan) identifying adapters that don't follow the taxonomy

**Verification**:
1. Each adapter's `publish()` method raises correct exception type
2. `grep -r "except:" src/backlink_publisher/publishing/adapters/` shows consistent error handling

### G1-U3: CLI argparse deduplication

**Scope**: Extract shared argument definitions to a common module
**Files**:
- `src/backlink_publisher/cli/_shared_args.py` (new — shared argument definitions)
- All CLI files that define argparse arguments (refactor to use shared definitions)

**Verification**:
1. All CLI entrypoints accept same arguments as before (regression check)
2. `src/backlink_publisher/cli/_shared_args.py` exists and is imported by ≥2 CLI modules

### G1-U4: Dependabot configuration

**Scope**: Add Dependabot config for automated dependency update PRs
**Files**:
- `.github/dependabot.yml` (new)
- Schedule: weekly (default)

**Verification**:
1. `.github/dependabot.yml` exists and parses correctly
2. `gh api /repos/:owner/:repo/dependabot/config` returns valid config

### G1-U5: Plan/brainstorm doc inventory and cleanup

**Scope**: Audit all 203 plan/brainstorm/ideation docs
**Files**: All `.md` files in `docs/plans/`, `docs/brainstorms/`, `docs/ideation/`
**Process**:
1. Categorize each doc: active/stale/superseded/grandfathered
2. Stale docs → move to `docs/archive/YYYY-MM-DD-title.md`
3. Superseded docs → add frontmatter `superseded_by: <newer-doc-path>`
4. Update grandfathered pre-cutoff plans with explicit note

**Verification**:
1. `ls docs/archive/*.md` ≥ 20 files (stale docs moved)
2. All remaining docs in `docs/plans/` have valid `status:` field
3. `grep "^status: active" docs/plans/*.md` returns our ≤3 active plans

---

## Verification Strategy

| Level | What | When |
|---|---|---|
| **Unit** | `pytest tests/ -v --tb=short --timeout=30` | After every change |
| **JS unit** | `cd webui_app/static/js && npx vitest run` | After JS changes |
| **Import** | `python -m py_compile src/backlink_publisher/**/*.py` | After Python changes |
| **Lint** | `flake8 src/ --count --select=E9,F63,F7,F82` | Weekly |
| **CLI smoke** | `cat fixtures/seed.jsonl \| plan-backlinks \| validate-backlinks \| publish-backlinks --dry-run` | After E2-U1 |
| **WebUI walkthrough** | Manual — hard refresh each page, verify no console errors | After E1-U3 |
| **Plan assessment** | `grep "^status: active" docs/plans/*.md` count | After S1-U3 |

## Risk & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Uncommitted work has uncaught bugs | Medium | Medium | Ship with thorough + existing test suite; fixes ship in Week 2 |
| Active plans assessment reveals >50% incomplete | Low | Medium | Bulk-defer plans that are <50%; keep only ≥80% items in active |
| ESM migration breaks page behavior | Medium | High | Manual walkthrough after each migration; ship one module at a time per PR |
| Vitest needs browser API not in happy-dom | Low | Low | Fall back to `jsdom` environment for DOM-dependent tests |
| Store cleanup causes race conditions | Low | Medium | Integration test with concurrent request simulation |
| Dependabot generates noise | Low | Low | Configure `open-pull-requests-limit: 5` initially |
| Doc cleanup deletes valuable content | Medium | Medium | Archive never delete. Only confirmed duplicates are removed |

## Implementation Order

```
Week 1 (Jun 21-27):
  [S1-U1] + [S1-U2] → Ship auto-recover + health infrastructure (PR #1)
  [E1-U1] → Vitest lib/ tests (parallel to S1)
  [E1-U4] → Store cleanup (parallel)
  [S1-U3] → Assess active plans (parallel)

Week 2 (Jun 28 - Jul 4):
  [E1-U2] → False-success route fixes
  [E1-U3] → ESM migration batch 1 (bind_channel, channel-binding, copilot)
  [S2-U1] → Close remaining plans
  [S2-U2] → Clean tree verification
  [G1-U1] → Type checking CI (background)
  [G1-U4] → Dependabot config (background)

Weeks 3-4 (Jul 5-18):
  [E2-U1] → Route decomposition
  [E2-U2] → WebUI test coverage expansion
  [E1-U3] → ESM migration batch 2 (equity, mode_toggle, url_derive)
  [G1-U2] → Adapter error taxonomy (background)
  [G1-U3] → CLI argparse dedup (background)
  [G1-U5] → Doc inventory & cleanup (background)

Week 4+ (Jul 19+):
  [E2-U2] → Finish WebUI test target (≥120)
  [G1-U5] → Doc cleanup final pass
  → Final track review
```

## Dependencies

- **Track S → Track E**: None (independent). Experience track does not wait for stabilization.
- **Track S → Track G**: None.
- **Track E → Track G**: None.
- **E1-U2 (false-success routes)** depends on initial audit passing first (sequential within the unit).
- **E2-U1 (route decomposition)** depends on E2-U2 (route tests) — tests must exist before refactoring.
- **E1-U3 (ESM migration)** is parallelizable into 2 batches of 3 scripts each.
- **G1-U5 (doc inventory)** is the lowest priority — can defer to after all other units if time is short.

## Plan Verification

- [ ] All 21 requirements (S-R1 to G-R7) addressed by at least one implementation unit
- [ ] Each wave has verification steps defined before marking complete
- [ ] Parallel tracks have no blocking dependencies on each other
- [ ] ESM migration preserves zero-build architecture (no bundler added)
- [ ] All file paths use repo-relative format (not absolute)
