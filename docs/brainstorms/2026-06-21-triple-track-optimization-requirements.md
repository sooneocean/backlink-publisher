---
date: 2026-06-21
topic: triple-track-optimization
type: requirements
status: draft
---

# 三軌並行全面優化迭代計畫 — backlink-publisher

## Summary

A three-track iterative optimization roadmap for backlink-publisher: **Stabilization** (ship in-flight work and close active plans), **Experience** (immediate operator UX improvements across WebUI and CLI), and **Governance** (background maintenance of type safety, documentation, and CI infrastructure). Each track runs at its own cadence — weekly ship, continuous, and background — so operator-facing value starts flowing immediately without waiting for housekeeping to finish.

## Problem Frame

backlink-publisher is a mature, well-governed codebase (302 source files, ~58.9k SLOC, ~6,270 tests, A-/B+ health rating) that has been through multiple optimization cycles:

1. **May 25** — Codebase Optimization Backlog (O1-O7 priority items identified)
2. **May 28** — Comprehensive Optimization 4-Wave Plan (shipped as `983f5b2`)
3. **Jun 07** — Full 7-Dimension System Audit (P0-P3 priorities published)
4. **Jun 08** — Channel Health Routing + auto-recover plan (in flight)
5. **Jun 10** — Pipeline End-to-End Optimization plan (active, CLI/engine layer)
6. **Jun 14** — Latest commit (`b523a29`, WebUI autopublish intake)

The project currently has **7+ active plans** in `docs/plans/` with `status: active`, plus **uncommitted work** (auto-recover CLI, health dashboard updates, channel health registry/router) that needs to converge. Meanwhile, the June audit identified **P0-P3 gaps** — particularly in operator-facing areas (JS test framework missing, legacy classic scripts not migrated to ESM, 29 route modules without lifecycle management, WebUI test coverage at only 47 test functions).

The core tension: **keep shipping while fixing the house**. A pure convergence-first approach delays operator value by 2-3 weeks. A pure feature approach accumulates debt. The three-track model resolves this by decoupling cadences.

## Actors

| Actor | Description | Primary Tracks |
|---|---|---|
| **Operator** | Daily user of WebUI dashboard and CLI pipeline. Cares about false success, missing feedback, workflow breaks. | Experience (E) |
| **Developer/Maintainer** | Writes code, reviews PRs, maintains adapters. Cares about type safety, test confidence, CI speed. | Governance (G) |
| **Project Owner** | Manages roadmap, reviews plans, makes scope decisions. Cares about WIP limits, predictability, governance health. | Stabilization (S), Governance (G) |

## Key Flows

### Flow 1: Three-Track Operation

```
Week 1-2:
  [S] Ship auto-recover + health dashboard + close 3-4 active plans
  [E] JS test framework (lib/api.js + dom.js) + false success route fixes
  [G] Type system baseline + doc inventory

Week 3-4:
  [S] Close remaining active plans + ship any blockers
  [E] ESM migration complete + singleton cleanup hooks + route refactoring start
  [G] Adapter error taxonomy unification + CLI argparser dedup

Week 5-6:
  [S] Verify all plans shipped, tree clean
  [E] Route lifecycle management complete + WebUI test coverage > 80%
  [G] Dependabot + CI governance + doc cleanup > 50%
```

### Flow 2: Stabilization Track Gate

```
Active plan → assess completeness (≥80% done?) 
  → yes: prioritize for ship this week
  → no: document remaining scope, decide: finish or explicitly defer
→ ship uncommitted work → close plan (status: shipped) → remove from active list
```

## Requirements

### Track S — Stabilization (收斂現有工作)

**S-R1**: All uncommitted work as of 2026-06-21 must be shipped within 2 weeks.

**S-R2**: Every active plan in `docs/plans/*.md` with `status: active` must be assessed for completeness. Plans that are ≥80% implemented must ship; plans that are <80% must be explicitly deferred or closed with a status note.

**S-R3**: After stabilization, `git status` must be clean (no uncommitted work) and active plan count must be ≤3.

**S-R4**: Each shipped item must be independently revertable as a single commit/PR.

**S-R5**: The stabilization track must not block the experience track — operator fixes ship on experience track cadence regardless of stabilization status.

**S-R6**: Each closed plan must include a one-paragraph "狀態摘要" in its frontmatter or as an追加 note recording what shipped, what was deferred, and why.

### Track E — Experience (Operator 體驗立即改善)

**E-R1**: JS test framework must be introduced for `static/js/lib/` shared layer (`api.js`, `dom.js`, `profiles.js`). Target: ≥80% line coverage of lib/ modules.

**E-R2**: Legacy classic scripts (`fetch_json.js`, `bind_channel.js`, `channel-binding.js`) must be migrated to ESM modules, completing the zero-build ESM architecture.

**E-R3**: WebUI "false success" routes (routes that swallow errors and redirect as if successful) must be identified and fixed. Each fix must include before/after behavior documentation.

**E-R4**: Module-level singletons in `webui_store/` must have cleanup hooks — idle timeout (default 30 min) with periodic cleanup cycle.

**E-R5**: 29 route modules in `webui_app/routes/` must be audited for lifecycle consistency. Maximum module size target: ≤500 LOC. Modules exceeding this must be decomposed.

**E-R6**: WebUI-specific test coverage must increase from current ~47 test functions to ≥120 test functions, covering route contracts, error handlers, and CSRF compliance.

**E-R7**: All UX fixes must ship with Chinese operator-facing messages for error states (matching `BIND_ERROR_MESSAGES` pattern).

**E-R8**: CLI UX improvements — ensure all CLI entrypoints produce operator-readable stderr diagnostics on failure (not just JSONL on stdout).

### Track G — Governance (背景維護)

**G-R1**: Gradual type system adoption via mypy/pyright. Start as non-blocking CI warning, tighten over 3 months. New code must have type hints.

**G-R2**: Adapter error taxonomy must be unified — all adapters to use consistent exception hierarchy per `docs/solutions/correctness/adapter-silent-exceptions-resolution.md` patterns.

**G-R3**: CLI `argparse` duplication must be reduced — shared argument definitions extracted to a common module.

**G-R4**: Dependabot (or equivalent) must be configured for automated dependency update PRs.

**G-R5**: The 203 plan/brainstorm/ideation docs in `docs/` must be audited: stale/superseded docs archived or deleted, active docs re-tagged, grandfathered pre-cutoff plans documented.

**G-R6**: CI pipeline must include a style consistency check (beyond current `py_compile` + `ast.parse`) — at minimum a targeted lint rule set.

**G-R7**: Governance track must not block either stabilization or experience track — it runs in background with no hard deadlines.

## Success Criteria

### Stabilization Track
- ✅ Zero uncommitted work
- All active plans either shipped (`status: shipped`) or explicitly deferred with rationale
- Clean `git status` on main branch

### Experience Track
- ✅ `lib/` shared JS layer has ≥80% test coverage
- ✅ All legacy classic scripts migrated to ESM
- ✅ Zero false-success route paths in WebUI
- ✅ Store singletons have cleanup hooks (verified via integration test)
- ✅ WebUI test functions ≥120
- ✅ Each UX fix carries Chinese operator message

### Governance Track
- ✅ Type checking CI gate passes (non-blocking)
- ✅ Adapter error taxonomy unified across all 30+ adapters
- ✅ Shared CLI argument definitions module exists
- ✅ Dependabot configured and producing PRs
- ✅ Plan/brainstorm docs audited, stale docs archived

## Scope Boundaries

### In Scope
- All three tracks (S, E, G) as described above
- JS test framework selection and integration
- ESM migration of legacy scripts
- WebUI route audit and decomposition
- Store singleton lifecycle management
- Type system gradual adoption infrastructure
- Adapter error taxonomy audit and unification
- CLI argparser refactoring
- Documentation audit and cleanup
- Dependabot/CI governance setup

### Deferred for Later (post-roadmap)
- New publisher adapters (beyond current in-flight ones)
- Major architectural changes (e.g., replacing Flask, changing events store backend)
- RECON.log format changes
- readtime U1-U4 modifications (already shipped)
- GA4/GSC/AI value axes for channel-scorecard (inert:not-landed per plan)
- Performance profiling hooks (covered by Jun 10 pipeline plan)

### Outside This Roadmap's Identity
- ❌ NOT a feature delivery plan — this is an optimization and quality-of-life roadmap
- ❌ NOT scrapping existing active plans — it incorporates and converges them
- ❌ NOT a rewrite or framework migration
- ❌ NOT introducing Node.js or bundler into the build chain

## Key Decisions

**D1. Three-track independent cadence.** Stabilization ships weekly, experience ships continuously, governance runs in background. No track blocks another.

**D2. Operator experience is the prioritization axis.** When tracks conflict, experience track wins. This means a WebUI false-success fix ships even if stabilization hasn't fully converged.

**D3. JS test framework choice deferred to planning.** The audit recommends Vitest or node:test; the choice should be made during implementation based on actual ESM compatibility testing.

**D4. Type safety is gradual, not a big bang.** Starting as non-blocking CI warning avoids blocking other tracks. Tightening happens over months, not weeks.

**D5. Documentation cleanup is aggressive but non-destructive.** Stale docs are archived (moved to `docs/archive/`), never deleted. Only confirmed duplicates are removed.

## Dependencies / Assumptions

- **Assumed:** Current uncommitted work (auto-recover CLI, health dashboard, channel health registry) is near-completion and can ship within 1-2 weeks
- **Assumed:** Active plans are at varying completion levels; the stabilization track may reveal some plans need more work than expected
- **Assumed:** JS test framework integration does not require changes to the build chain (zero-build architecture preserved)
- **Assumed:** Operator pain points identified in the June audit are still current (no major fixes shipped between audit and this document)
- **Dependency:** ESM migration requires testing on all major page modules — a regression in the auto-publish intake page (`b523a29`) would block E-R2
- **Dependency:** Route decomposition (E-R5) requires route-level test coverage first — order: tests → refactor

## Outstanding Questions

1. **JS test framework specifics** — Vitest vs node:test vs web-test-runner? To be decided in planning based on ESM compatibility and CI integration cost.
2. **Store cleanup granularity** — Should cleanup be per-store (each store manages its own TTL) or centralized (a single cleanup scheduler)? Product decision needed.
3. **Doc archive location** — `docs/archive/` or a separate branch? Preference for in-repo archive for traceability.
4. **Dependabot schedule** — Weekly or monthly? Depends on how noisy the updates are given the dependency set.

## Implementation Waves (High-Level)

### Wave S1 (Week 1) — Stabilization Sprint
| Item | Track | Target |
|---|---|---|
| Ship auto-recover CLI + tests | S | PR merged |
| Ship health dashboard updates (health.html, health_metrics.py, kinds.py) | S | PR merged |
| Ship channel health registry + router | S | PR merged |
| Assess all 16 active plans, classify as shipped/deferred | S | Status table |
| JS test framework for lib/ (api.js, dom.js) | E | First tests green |

### Wave E1 (Week 1-2) — Experience Sprint
| Item | Track | Target |
|---|---|---|
| Fix false-success WebUI routes (O1 audit item) | E | All routes verified |
| ESM migrate fetch_json.js | E | No classic scripts in lib/ |
| ESM migrate bind_channel.js + channel-binding.js | E | No classic scripts remain |
| Store singleton cleanup hook (v1 — centralized scheduler) | E | PR merged |
| Begin route audit — identify top-5 modules by LOC | E | Audit report |

### Wave G1 (Weeks 1-4) — Governance Background
| Item | Track | Target |
|---|---|---|
| mypy/pyright CI config (non-blocking warning) | G | CI green |
| Adapter error taxonomy scan | G | Gap report |
| Doc inventory — categorize all 203 docs | G | Inventory spreadsheet |
| Dependabot config PR | G | PR merged |
| Shared argparse module extraction | G | PR merged |
