---
title: "refactor: Comprehensive optimization — 4-wave quality + type safety + observability"
type: refactor
status: complete
date: 2026-05-28
origin: docs/brainstorms/2026-05-28-comprehensive-optimization-requirements.md
claims:
  paths: []
  shas: []
---

# refactor: Comprehensive optimization — 4-wave quality + type safety + observability

## Overview

A 4-wave optimization plan covering 5 high-impact areas for backlink-publisher. Each wave is a single PR that can be independently reverted.

## Problem Frame

The codebase is mature (B+ rating) but has recurring pain classes:
1. **False success patterns** — operators see success when actions silently failed (O1)
2. **Silent exception swallowing** — adapters mask real failures (O2)
3. **Test coverage gaps** — security-adjacent code lacks dedicated tests (O4)
4. **No static analysis** — type errors caught only at runtime
5. **Performance blind spots** — no visibility into pipeline bottlenecks

## Requirements Trace

- **R1-R4:** Wave 1 — Quick wins (O1, O7)
- **R5-R8:** Wave 2 — Code quality (O2, O4)
- **R9-R12:** Wave 3 — Type safety
- **R13-R16:** Wave 4 — Performance & observability

## Scope Boundaries

**In scope:**
- WebUI route error handling
- Exit-code contract enforcement
- Adapter exception handling
- OAuth route testing
- Type safety infrastructure
- Health dashboard completion
- Performance profiling
- Error reporting enhancement

**Out of scope:**
- ❌ New publisher adapters
- ❌ New storage backends
- ❌ New LLM dependencies
- ❌ Major architectural changes

## High-Level Technical Design

### Wave 1: Quick Wins (1-2 days)

**Unit 1: Fix WebUI "false success" routes (O1)**

Routes to audit and fix:
- `routes/checkpoint.py:~89-93` — delete failure swallowed → success redirect
- `routes/drafts.py:~85-110` — scheduler job-removal failures swallowed
- `routes/url_verify.py:~181-185` — ANY exception reported as `network_error`
- `routes/pipeline.py:~137` — corrupt JSON → stale fallback, no feedback

**Pattern:** Surface the real failure to the UI instead of redirecting success.

```python
# BEFORE (false success):
try:
    result = do_something()
except Exception:
    pass  # swallowed
return redirect("/?flash_type=success&flash_msg=操作成功")

# AFTER (honest):
try:
    result = do_something()
except Exception as exc:
    return redirect(f"/?flash_type=danger&flash_msg=操作失败: {exc}")
return redirect("/?flash_type=success&flash_msg=操作成功")
```

**Unit 2: Enforce exit-code contract (O7)**

Create `tests/test_cli_exit_code_contract.py` with parametrized tests:

```python
@pytest.mark.parametrize("cli_entry,expected_codes", [
    ("plan-backlinks", [0, 1, 2]),
    ("validate-backlinks", [0, 1, 2]),
    ("publish-backlinks", [0, 1, 2, 3, 4, 5]),
    ("report-anchors", [0, 1, 6]),
    ("footprint", [0, 1]),
])
def test_exit_code_contract(cli_entry, expected_codes):
    """Verify documented exit codes match actual behavior."""
    ...
```

### Wave 2: Code Quality (2-3 days)

**Unit 3: Fix silent-swallow exceptions (O2)**

Adapters to audit:
- `adapters/medium_browser.py:~419` — screenshot/stderr-write errors on failure path
- `adapters/linkedin_api.py:~140` — `resp.json()` decode failure → `{}`

**Pattern:** Add one-line log + context to truly-silent ones.

```python
# BEFORE (silent):
except Exception:
    pass

# AFTER (logged):
except Exception as exc:
    logger.warning("screenshot_failed", reason=type(exc).__name__)
```

**Unit 4: Add OAuth route tests (O4)**

Create `tests/test_webui_routes_oauth.py`:
- Test `_is_loopback_uri()` with various inputs
- Test insecure-transport helper
- Test OAuth callback security gate

### Wave 3: Type Safety (3-5 days)

**Unit 5: Introduce mypy/pyright infrastructure**

1. Add `mypy` and `pyright` to `[project.optional-dependencies] dev`
2. Create `mypy.ini` with gradual strictness:
   ```ini
   [mypy]
   python_version = 3.11
   warn_return_any = True
   warn_unused_configs = True
   disallow_untyped_defs = False  # Start permissive
   ```
3. Add CI step (non-blocking initially):
   ```yaml
   - name: Type check
     run: mypy src/ --ignore-missing-imports
     continue-on-error: true  # Non-blocking initially
   ```

**Unit 6: Add type hints to Waves 1-2 code**

Add type hints to all new/modified code in Waves 1-2.

### Wave 4: Performance & Observability (3-5 days)

**Unit 7: Complete health dashboard integration**

- Read `quarantine_log` in `routes/health.py`
- Display gap count in `templates/health.html`
- Add warning banner when gaps > 0

**Unit 8: Add performance profiling hooks**

- Add `--profile` flag to CLI entrypoints
- Output cProfile stats to `~/.cache/backlink-publisher/profiles/`
- Add performance regression tests for critical paths

## Implementation Units

- [ ] **Unit 1: Fix WebUI "false success" routes**

**Goal:** Surface real failures instead of redirecting success.

**Requirements:** R1, R3, R4

**Dependencies:** none

**Files:**
- Modify: `webui_app/routes/checkpoint.py`
- Modify: `webui_app/routes/drafts.py`
- Modify: `webui_app/routes/url_verify.py`
- Modify: `webui_app/routes/pipeline.py`
- Create: `tests/test_webui_false_success.py`

**Approach:**
1. Audit each route for broad `except Exception` patterns
2. Replace silent redirects with error surfaced to UI
3. Add contract tests verifying error paths

**Test scenarios:**
- Happy path: successful operations still show success
- Error path: failed operations show error message
- Edge case: partial failures show appropriate status

**Verification:**
- `pytest tests/test_webui_false_success.py` green
- `pytest tests/test_webui_*.py` green (no regression)

---

- [ ] **Unit 2: Enforce exit-code contract**

**Goal:** Add parameterized tests covering all CLI entrypoints.

**Requirements:** R2, R3, R4

**Dependencies:** none

**Files:**
- Create: `tests/test_cli_exit_code_contract.py`

**Approach:**
1. Create parametrized test over CLI entrypoints
2. Verify each documented exit code is reachable
3. Add to CI as required check

**Test scenarios:**
- Happy path: each exit code is reachable
- Error path: invalid input triggers expected codes

**Verification:**
- `pytest tests/test_cli_exit_code_contract.py` green

---

- [ ] **Unit 3: Fix silent-swallow exceptions**

**Goal:** Add one-line log + context to truly-silent exceptions.

**Requirements:** R5, R8

**Dependencies:** Unit 1 (stable routes)

**Files:**
- Modify: `publishing/adapters/medium_browser.py`
- Modify: `publishing/adapters/linkedin_api.py`
- Create: `tests/test_adapter_exception_logging.py`

**Approach:**
1. Audit adapters for silent exception swallowing
2. Add logging to truly-silent ones
3. Add tests verifying logging behavior

**Test scenarios:**
- Happy path: exceptions are logged with context
- Error path: logging doesn't break adapter behavior

**Verification:**
- `pytest tests/test_adapter_*.py` green

---

- [ ] **Unit 4: Add OAuth route tests**

**Goal:** Add dedicated tests for OAuth security helpers.

**Requirements:** R6, R7, R8

**Dependencies:** Unit 1 (stable routes)

**Files:**
- Create: `tests/test_webui_routes_oauth.py`

**Approach:**
1. Test `_is_loopback_uri()` with various inputs
2. Test insecure-transport helper
3. Test OAuth callback security gate

**Test scenarios:**
- Happy path: loopback URIs accepted
- Error path: non-loopback URIs rejected
- Edge case: IPv4/IPv6 loopback variants

**Verification:**
- `pytest tests/test_webui_routes_oauth.py` green

---

- [ ] **Unit 5: Introduce mypy/pyright infrastructure**

**Goal:** Establish type checking with gradual adoption.

**Requirements:** R9, R10

**Dependencies:** none

**Files:**
- Modify: `pyproject.toml` (add mypy/pyright to dev deps)
- Create: `mypy.ini`
- Modify: `.github/workflows/ci.yml` (add type check step)

**Approach:**
1. Add mypy/pyright to dev dependencies
2. Create mypy.ini with permissive config
3. Add CI step (non-blocking initially)

**Test scenarios:**
- Happy path: `mypy src/ --ignore-missing-imports` passes
- Error path: type errors are reported but don't block CI

**Verification:**
- `pip install -e ".[dev]"` includes mypy/pyright
- CI type check step runs (non-blocking)

---

- [ ] **Unit 6: Add type hints to Waves 1-2 code**

**Goal:** Add type hints to all new/modified code.

**Requirements:** R11

**Dependencies:** Unit 5 (mypy infrastructure)

**Files:**
- Modify: all files changed in Units 1-4

**Approach:**
1. Add type hints to new functions
2. Add type hints to modified functions
3. Run mypy to verify

**Test scenarios:**
- Happy path: mypy passes on modified files

**Verification:**
- `mypy <modified_files>` passes

---

- [ ] **Unit 7: Complete health dashboard integration**

**Goal:** Display quarantine gap counts in health dashboard.

**Requirements:** R13

**Dependencies:** none (readtime U1-U4 already in main)

**Files:**
- Modify: `webui_app/routes/health.py`
- Modify: `webui_app/templates/health.html`
- Create: `tests/test_health_dashboard_gaps.py`

**Approach:**
1. Read `quarantine_log` in health route
2. Display gap count in template
3. Add warning banner when gaps > 0

**Test scenarios:**
- Happy path: gap count displayed correctly
- Edge case: no data shows "0 gaps"

**Verification:**
- `pytest tests/test_health_dashboard_gaps.py` green

---

- [ ] **Unit 8: Add performance profiling hooks**

**Goal:** Add profiling capability for performance analysis.

**Requirements:** R14, R16

**Dependencies:** none

**Files:**
- Modify: `cli/plan_backlinks.py`
- Modify: `cli/publish_backlinks.py`
- Create: `tests/test_performance_profiling.py`

**Approach:**
1. Add `--profile` flag to CLI entrypoints
2. Output cProfile stats to cache dir
3. Add performance regression tests

**Test scenarios:**
- Happy path: profiling outputs stats file
- Edge case: profiling doesn't affect normal operation

**Verification:**
- `pytest tests/test_performance_profiling.py` green

## System-Wide Impact

- **Interaction graph:** Each wave is independent; no cross-wave dependencies
- **Error propagation:** Wave 1-2 fixes surface errors to UI; Wave 3 adds type safety; Wave 4 adds observability
- **State lifecycle risks:** Minimal — mostly test additions and error handling improvements
- **API surface parity:** CLI/WebUI behavior unchanged (errors now surfaced, not hidden)
- **Unchanged invariants:** All 304 existing tests remain green

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Wave 1 fixes break existing behavior | Contract tests verify before/after |
| mypy adoption causes massive type errors | Start with non-blocking warnings |
| Performance profiling adds overhead | Profile only in dev mode |
| Health dashboard integration breaks UI | Add dashboard tests first |

## Documentation / Operational Notes

- Update README.md with new CLI flags (`--profile`)
- Update AGENTS.md with type checking section
- Add runbook for performance profiling

## Sources & References

- Origin: `docs/brainstorms/2026-05-28-comprehensive-optimization-requirements.md`
- Related: `docs/ideation/2026-05-25-codebase-optimization-backlog.md`
- Related: `docs/brainstorms/2026-05-28-v_next-consistency-observability-release-requirements.md`
