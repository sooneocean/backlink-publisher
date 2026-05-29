---
date: 2026-05-28
topic: comprehensive-optimization
type: requirements
status: draft
---

# Comprehensive Optimization Plan — backlink-publisher

## Overview

A 4-wave optimization plan covering 5 high-impact areas for backlink-publisher. The plan prioritizes quick wins first (O1, O7), then deepens into code quality (O2, O4), establishes type safety foundation, and finally adds performance/observability improvements.

## Problem Frame

The codebase is mature and well-engineered (B+ rating), but has recurring pain classes:
1. **False success patterns** — operators see success when actions silently failed
2. **Silent exception swallowing** — adapters mask real failures
3. **Test coverage gaps** — security-adjacent code lacks dedicated tests
4. **No static analysis** — type errors caught only at runtime
5. **Performance blind spots** — no visibility into pipeline bottlenecks

## Requirements

### Wave 1: Quick Wins (O1, O7)

**R1.** Fix WebUI "false success" routes that silently swallow errors and redirect as if successful.

**R2.** Enforce exit-code contract (0-6) with parameterized tests covering all CLI entrypoints.

**R3.** Each fix must be independently revertable as a single PR.

**R4.** All existing 304 tests must remain green after each fix.

### Wave 2: Code Quality (O2, O4)

**R5.** Fix silent-swallow exceptions in adapters — add one-line log + context to truly-silent ones.

**R6.** Add dedicated OAuth route tests covering `_is_loopback_uri()` and insecure-transport helpers.

**R7.** New test file `tests/test_webui_routes_oauth.py` must not import hot modules mid-rewrite.

**R8.** Each fix must be independently revertable as a single PR.

### Wave 3: Type Safety

**R9.** Introduce mypy/pyright static analysis with gradual adoption.

**R10.** Establish type checking CI gate — initially as non-blocking warning.

**R11.** Add type hints to all new code in Waves 1-2.

**R12.** Create type stubs for external dependencies lacking them.

### Wave 4: Performance & Observability

**R13.** Complete health dashboard integration — display quarantine gap counts.

**R14.** Add pipeline performance profiling hooks.

**R15.** Enhance error reporting with structured context.

**R16.** Add performance regression tests for critical paths.

## Scope Boundaries

### In Scope
- WebUI route error handling
- Exit-code contract enforcement
- Adapter exception handling
- OAuth route testing
- Type safety infrastructure
- Health dashboard completion
- Performance profiling
- Error reporting enhancement

### Out of Scope (Non-Goals)
- ❌ New publisher adapters
- ❌ New storage backends
- ❌ New LLM dependencies
- ❌ Major architectural changes
- ❌ Frontend toolchain introduction
- ❌ Medium adapter rewrite
- ❌ RECON.log format changes
- ❌ readtime U1-U4 modifications (already shipped)

## Success Criteria

### Technical Success
- ✅ All 304 existing tests remain green
- ✅ Each wave independently revertable
- ✅ No new runtime dependencies (except mypy/pyright as dev deps)
- ✅ Type checking CI gate passes
- ✅ Health dashboard displays real state

### Operator-Facing Success
- ✅ WebUI routes no longer show false success
- ✅ Exit codes match documented contract
- ✅ Error messages include structured context
- ✅ Health dashboard shows quarantine gaps

### Quality Metrics
- ✅ Zero silent exception swallowing in adapters
- ✅ OAuth routes have dedicated test coverage
- ✅ Type coverage > 80% for new code
- ✅ No performance regression in critical paths

## Key Decisions

**D1. Impact-First ordering** — Quick wins (O1, O7) provide immediate value, then deepen into code quality, then establish type safety, then add performance/observability.

**D2. Gradual type adoption** — Start with non-blocking warnings, tighten over time. This avoids massive one-time migration cost.

**D3. Wave isolation** — Each wave is a single PR that can be independently reverted. This provides rollback safety.

**D4. No new runtime deps** — mypy/pyright are dev-only tools, not runtime dependencies.

**D5. Preserve existing patterns** — Follow established project conventions (monolith SLOC budget, test patterns, CSRF guard).

## Dependencies / Assumptions

- **Assumed:** Current codebase state is the baseline (all waves 1-4 of direct deps upgrade completed)
- **Assumed:** Publishing health dashboard (2026-05-25) is in flight but not blocking
- **Assumed:** Equity ledger (2026-05-25) is in flight but not blocking
- **Dependency:** WebUI routes must be stable before O1 fixes
- **Dependency:** OAuth routes must be stable before O4 tests

## Implementation Waves

### Wave 1: Quick Wins (1-2 days)
- **Unit 1:** Fix WebUI "false success" routes (O1)
- **Unit 2:** Enforce exit-code contract (O7)

### Wave 2: Code Quality (2-3 days)
- **Unit 3:** Fix silent-swallow exceptions (O2)
- **Unit 4:** Add OAuth route tests (O4)

### Wave 3: Type Safety (3-5 days)
- **Unit 5:** Introduce mypy/pyright infrastructure
- **Unit 6:** Add type hints to Waves 1-2 code

### Wave 4: Performance & Observability (3-5 days)
- **Unit 7:** Complete health dashboard integration
- **Unit 8:** Add performance profiling hooks

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Wave 1 fixes break existing behavior | High | Each fix has before/after contract tests |
| mypy adoption causes massive type errors | Medium | Start with non-blocking warnings |
| Performance profiling adds overhead | Low | Profile only in dev mode |
| Health dashboard integration breaks UI | Medium | Add dashboard tests before integration |

## Next Steps

→ `/ce:plan` for structured implementation planning
→ Each wave gets its own plan document
→ Waves can be executed in parallel if resources allow
