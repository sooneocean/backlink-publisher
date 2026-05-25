---
title: "refactor: CLI extension-readiness + codebase hygiene"
type: refactor
status: completed
date: 2026-05-18
completed: 2026-05-18
origin: docs/brainstorms/2026-05-18-codebase-hygiene-requirements.md
deepened: 2026-05-18
---

# refactor: CLI Extension-Readiness + Codebase Hygiene

## Overview

This plan makes the existing Publisher ABC + table-driven registry actually reachable from the CLI (so adding WordPress/Substack/Tumblr stops requiring edits to two CLI monoliths), publishes a contributor-facing adapter walkthrough, removes a stale `core/` monorepo experiment and two already-merged worktrees, relocates the workspace-root README into the actual git repo, and installs post-merge worktree auto-cleanup so the sprawl does not reaccumulate.

The plan is deliberately scoped: R9's CLI decoupling is *surgical* (only the platform-coupling lines), not monolith decomposition. F7 (in flight in `bp-f7-monolith`) remains the owner of broader monolith ratcheting and surgical extraction.

## Problem Frame

Owner perception is that the codebase is fragmented and hard to extend. Adversarial review of the origin brainstorm (see origin: `docs/brainstorms/2026-05-18-codebase-hygiene-requirements.md`) verified via `grep` that the "registry-already-solves-extension" framing is **partly false**: the registry generalises dispatch only, while the CLI and schema layers still hardcode platform names across **two hardcoding surfaces and 13 distinct sites**:

- **CLI argparse layer** (3 sites): `publish_backlinks.py:387`, `plan_backlinks.py:1488` (`choices=["blogger","medium"]`), `publish_backlinks.py:539` (literal error string).
- **CLI throttle layer** (4 + 1 sites): `publish_backlinks.py:13` (`_MEDIUM_ADAPTERS` set), `:119,253,326,759` (branches on it), and `:273` (`if platform == "medium"` separate mechanism).
- **CLI LinkedIn rejection** (2 sites): `publish_backlinks.py:536`, `validate_backlinks.py:202` (`if platform == "linkedin"`).
- **Schema layer** (3 sites): `schema.py:26` (`SUPPORTED_PLATFORMS = {"blogger","medium"}`), `:75` (rejection gate), `:78` (error message). Imported and consumed at `cli/publish_backlinks.py:164,554,636` and `cli/validate_backlinks.py:205`.
- **Schema LinkedIn rejection** (1 site): `schema.py:183` — `if row["platform"] == "linkedin": errors.append("platform 'linkedin' is not supported in this version")`. The three coordinated LinkedIn rejection sites (`schema.py:183`, `publish_backlinks.py:536`, `validate_backlinks.py:202`) read as a deliberate user-facing "we acknowledge LinkedIn, not yet supported" message — likely a live contract, not dead code.

R9's surgical decoupling targets exactly this enumerated set. Importantly: there is **no Telegraph adapter today**. `src/backlink_publisher/publishing/adapters/telegraph_node.py` is a Markdown→Node tree converter (Unit 3 of a separate, in-flight Telegraph adapter plan), not a Publisher subclass. The only Publisher subclasses are `BloggerAPIAdapter`, `MediumAPIAdapter`, `MediumBraveAdapter`, `MediumBrowserAdapter`. The R9 acceptance proof therefore uses a test-scoped `FakeAdapter(Publisher)` fixture, not Telegraph.

Without R9, the R5 walkthrough would be paper-true and code-false: a contributor following AGENTS.md would write a registered adapter, then discover their platform name doesn't appear in `argparse choices` AND `schema.validate_publish_payload` rejects their payload via the hardcoded `SUPPORTED_PLATFORMS` set. The confidence the walkthrough was supposed to build collapses on first use.

## Requirements Trace

Carried forward from origin (`docs/brainstorms/2026-05-18-codebase-hygiene-requirements.md`):

**P0 (load-bearing for the outcome)**

- **R9** — Surgically de-couple CLI from hardcoded platform names. Decomposed into: **R9a** argparse choices wiring (Unit 1); **R9b** error-message wiring (Unit 1); **R9c** medium throttle metadata (Unit 2); **R9d** LinkedIn rejection migration (Unit 3); **R9e** `SUPPORTED_PLATFORMS` schema-layer re-targeting (Unit 1 expanded).
- **R5** — AGENTS.md "Adding a new publisher adapter" walkthrough (Unit 4)
- **R6** — Walkthrough cites a real existing adapter (Blogger) at each step (Unit 4)

**P1 (visible noise reduction)**

- **R1** — Remove already-merged worktrees `bp-events-u1` (PR #49) and `bp-events-u6` (PR #47) (Unit 6)
- **R3** — Delete stale `core/` directory (Unit 5)
- **R4** — Relocate workspace-root README into `backlink-publisher/README.md` (Unit 5)

**P2 (per-judgment + sustainability)**

- **R2** — Triage `bp-local-unit{2,4,5,6}` rehearsal worktrees with dirty-state guard (Unit 7)
- **R10** — Post-merge worktree auto-cleanup (`/ship` hook + prune helper) (Unit 8)

Success criteria carried forward (objective acceptance gates):

- After R9: `grep -nE 'choices=\["blogger","medium"\]|_MEDIUM_ADAPTERS|Supported platforms: blogger, medium' src/backlink_publisher/cli/ src/backlink_publisher/schema.py` returns zero matches. `grep -n 'SUPPORTED_PLATFORMS\s*=\s*\{' src/backlink_publisher/schema.py` returns zero matches.
- After R9: a test-scoped `register("fake", FakeAdapter)` fixture makes `publish-backlinks --platform fake ...` parse successfully via argparse AND validate successfully via schema, without any other CLI/schema edit. This is the R9 falsifiable acceptance proof.
- After R9: the three coordinated LinkedIn rejection sites are folded into a single registry-driven helper that surfaces "platform 'X' is not supported; supported: ..." for any unregistered platform.
- After R4: canonical README lives at `backlink-publisher/README.md` (versioned, PR-reviewable).
- After R1, R3: workspace root has no `core/` and no merged-branch worktrees.
- After R10: next PR merged after this plan lands prunes its own worktree without owner intervention.

## Scope Boundaries

**In scope:** R9 surgical decoupling across CLI + schema layers (argparse choices, error strings, `_MEDIUM_ADAPTERS` branches, the platform-keyed `if platform == "medium"` site at 273, all three LinkedIn rejection sites folded into one helper, `SUPPORTED_PLATFORMS` re-targeted at `registered_platforms()`, new `post_publish_delay_seconds: int` field on `AdapterResult` with Medium adapters setting 30); AGENTS.md adapter walkthrough; light docstring refresh in `publishing/registry.py`; `core/` deletion; README relocation; two-worktree removal (R1); four-worktree triage (R2); post-merge auto-cleanup automation (R10).

**Out of scope:**

- Splitting `src/backlink_publisher/` into a `core/` + `packages/` monorepo. The registry + R9 cover dispatch + CLI coupling; package split is a future option iff a new adapter's deps cause a resolver conflict (escalation path documented in R5).
- Broad decomposition of `plan_backlinks.py` (1744 LOC) or `publish_backlinks.py` (840 LOC). R9 touches only the platform-coupling lines.
- Replacing Jinja2 templates with React/Vue.
- Adding a real new platform adapter (WordPress, Substack, Telegraph, etc). The Telegraph Publisher subclass is the deliverable of a separate, in-flight plan (`docs/plans/2026-05-15-004-feat-telegraph-adapter-plan.md`).
- Migrating existing Blogger / Medium / Telegraph dependencies from `[project.dependencies]` into extras.
- Treating dep-resolver-conflict prevention as an in-scope deliverable.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/registry.py:82` — `register(platform, *publishers)` variadic, supports fallback chain.
- `src/backlink_publisher/publishing/registry.py:90` — `registered_platforms() -> list[str]` already exists. R9(a) wires argparse to it.
- `src/backlink_publisher/publishing/registry.py:41` — `Publisher` ABC with abstract `publish()` and optional `available()`.
- `src/backlink_publisher/publishing/adapters/__init__.py` — registration site, import-side-effect convention. Today registers blogger + medium fallback chain. The registration shape (`register("platform", AdapterCls, ...)`) is the exact mechanism the R9 acceptance test exercises via a test-scoped `FakeAdapter`.
- `src/backlink_publisher/publishing/adapters/base.py` — `AdapterResult` dataclass. Unit 2 adds `post_publish_delay_seconds: int = 0` field.
- `src/backlink_publisher/cli/publish_backlinks.py:13` — `_MEDIUM_ADAPTERS`.
- `src/backlink_publisher/cli/publish_backlinks.py:119, 253, 326, 759` — 4 branch sites checking `result.adapter in _MEDIUM_ADAPTERS`.
- `src/backlink_publisher/cli/publish_backlinks.py:273` — `if platform == "medium":` separate gating mechanism, NOT `_MEDIUM_ADAPTERS`.
- `src/backlink_publisher/cli/publish_backlinks.py:387` and `cli/plan_backlinks.py:1488` — `argparse choices=["blogger", "medium"]`.
- `src/backlink_publisher/cli/publish_backlinks.py:536-539` and `cli/validate_backlinks.py:202` — `if platform == "linkedin":` + literal error string.
- `src/backlink_publisher/schema.py:26` — `SUPPORTED_PLATFORMS = {"blogger", "medium"}` — second hardcoding surface. Consumed at `schema.py:75,78`, `publish_backlinks.py:164,554,636`, `validate_backlinks.py:205`. Imported via `from ..schema import SUPPORTED_PLATFORMS` at `cli/publish_backlinks.py:26` and `cli/validate_backlinks.py:23`.
- `src/backlink_publisher/schema.py:183` — third LinkedIn rejection site: `if row["platform"] == "linkedin": errors.append(...)`.
- `tests/` — existing fixture-backed test pattern.
- `AGENTS.md` — currently 18 lines. R5's walkthrough will roughly triple its length but keep it under ~100 lines.
- `backlink-publisher/README.md` — exists. R4 appends a worktree-convention note here.
- `pyproject.toml:23-24` — `[project.optional-dependencies].dev` block. R5 walkthrough cites this as the precedent location for new `[platform]` extras.

### Institutional Learnings

- Memory `[Worktree Concurrent Switching]` — external processes will switch branches and erase uncommitted modifications in shared worktrees. R2 and R10 both encode the stash-or-`wip/`-branch guard.
- Memory `[Multi-agent turf-check]` — before any code edit, check worktree state. Pre-flight for R9 is mandatory.
- Memory `[CI workflow pr_filter quirk]` — CI runs only on `pull_request.branches=[main]`. R9 PR retargets need close+reopen to re-trigger CI.

### External References

External research skipped — codebase has strong local patterns and R9 is mechanical replacement of known surfaces.

## Key Technical Decisions

- **Decision**: R9 splits across three units (1, 2, 3) keyed by *kind* of hardcoding. **Rationale**: Argparse + schema (Unit 1, including R9e) is mechanical replacement against `registered_platforms()`. Throttle metadata (Unit 2) is a real interface change (`AdapterResult` gains a delay field). LinkedIn rejection migration (Unit 3) is a 3-site contract preservation.
- **Decision**: `post_publish_delay_seconds` delay metadata travels on `AdapterResult` itself, not via reverse-lookup. **Rationale**: AdapterResult.adapter is a string; reverse lookup adds indirection. Simplest contract: each `Publisher.publish()` sets `result.post_publish_delay_seconds = <int>`. The CLI loop calls `time.sleep(result.post_publish_delay_seconds)` only when `> 0`. Drops the broken "max across chain" semantics.
- **Decision**: Unit 4 depends on Units 1–3 landing first. *Drafting* in parallel with placeholder anchors is acceptable; *merging* must follow R9.
- **Decision**: Acceptance proof uses a test-scoped `FakeAdapter(Publisher)` fixture, NOT Telegraph. **Rationale**: `telegraph_node.py` is a Markdown→Node converter, not a Publisher subclass. FakeAdapter is the same mechanism real contributors will use.
- **Decision**: Unit 3 LinkedIn rejection treated as a **live user-facing contract**. **Rationale**: Three coordinated rejection sites + human-readable "in this version" message — deliberate UX. Removing it degrades UX for users who pass `linkedin` rows in a CSV.
- **Decision**: Keep one Python distribution, do not split into `core/` + `packages/`. Escalation path documented in R5 step (d).
- **Decision**: Optional-deps convention stated **inside the R5 walkthrough**, not as a standalone requirement.
- **Decision**: Relocate the canonical README into `backlink-publisher/README.md`. Workspace root is not a git repo (verified).
- **Decision**: Add R10 post-merge auto-cleanup. Worktree sprawl is a recurring failure mode per memory.
- **Decision**: R9 and F7 are order-independent; coordinate via the `[Multi-agent turf-check]` memory pattern, not by sequencing. R9 net-removes lines; F7 calibrates ceilings. Either landing order produces a coherent result.

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification.*

**Before R9 (today)** — platform-coupling surfaces across CLI + schema layers:

```
CLI argparse                                Adapter dispatch (already correct)
─────────────────────────                   ─────────────────────────
choices=["blogger","medium"]   ┐
"Supported platforms: ..."     ┤            register("blogger", BloggerAPIAdapter)
if platform == "linkedin"      ┤  ←  X  →   register("medium", MediumAPI…, MediumBrave…, MediumBrowser…)
if platform == "medium"        ┤
_MEDIUM_ADAPTERS = {...}       ┘            registered_platforms() exists, returns sorted list

Schema layer                                AdapterResult
─────────────────────────                   ─────────────────────────
SUPPORTED_PLATFORMS = {"b","m"}   ←  X      adapter: str  (e.g. "medium-api")
schema.validate rejection at :183                — does NOT carry delay metadata
```

**After R9** — single coupling point: registration in `adapters/__init__.py`:

```
CLI argparse                                Adapter dispatch
─────────────────────────                   ─────────────────────────
choices=registered_platforms()  ─────►      register("blogger", BloggerAPIAdapter)
                                            register("medium", MediumAPI…, …)
Schema layer                                AdapterResult
─────────────────────────                   ─────────────────────────
supported_platforms() delegates ─────►      adapter: str
  to registered_platforms()                 post_publish_delay_seconds: int  ← set by each adapter
LinkedIn rejection (3 sites)                  default 0; Medium adapters set 30
  folded into one registry-driven
  unsupported-platform helper
```

## Implementation Units

```
Phase 1: Extension Readiness (load-bearing) — blocked on bp-ko-html landing first (turf collision)
  Unit 1 (R9a+b+e) ─┐
  Unit 2 (R9c) ─────┼──► Unit 4 (R5+R6 walkthrough)
  Unit 3 (R9d) ─────┘

Phase 2+: Independent hygiene/sustainability — any order
  Unit 5 (R3+R4: core/ + README)    independent
  Unit 6 (R1: merged worktrees)     independent — DONE 2026-05-18
  Unit 7 (R2: rehearsal triage)     independent — DONE 2026-05-18
  Unit 8 (R10: auto-cleanup)        independent
```

- [ ] **Unit 1: Wire CLI argparse choices + schema SUPPORTED_PLATFORMS to `registered_platforms()`** (R9a + R9b + R9e)

**Goal:** Replace three hardcoded CLI surfaces AND the schema-layer `SUPPORTED_PLATFORMS` set with calls into `publishing.registry`. After this unit, adding a `register("x", XAdapter)` call to `adapters/__init__.py` makes `x` appear in `--platform` choices AND `schema.validate_publish_payload` accepts payloads with `platform="x"`, all without other CLI/schema edits.

**Requirements:** R9 (parts a, b, e)

**Dependencies:** None for the work itself; turf-blocked on `bp-ko-html` landing first since `bp-ko-html` has uncommitted edits to `schema.py`.

**Files:**
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (line 387 argparse choices; 539 literal error string)
- Modify: `src/backlink_publisher/cli/plan_backlinks.py` (line 1488 argparse choices)
- Modify: `src/backlink_publisher/cli/validate_backlinks.py` (line 205 error string for SUPPORTED_PLATFORMS)
- Modify: `src/backlink_publisher/schema.py` (line 26 frozen set → registry-delegating function; lines 75, 78 consumers; 5 other consumer sites in CLI)
- Test: `tests/test_publish_backlinks*.py` or `tests/cli/test_publish_backlinks*.py` (confirm path in-unit)
- Test: `tests/test_schema.py` (or matching)

**Approach:**
- Add explicit `import backlink_publisher.publishing.adapters  # noqa: F401 — populates the registry before argparse construction` at the top of `plan_backlinks.py` and `validate_backlinks.py`. `publish_backlinks.py:19` already imports from `..adapters` for other symbols and gets the side-effect transitively.
- Import `registered_platforms` from `backlink_publisher.publishing.registry`.
- Replace `choices=["blogger","medium"]` with `choices=registered_platforms()`.
- Replace the literal `"Supported platforms: blogger, medium"` error string at `publish_backlinks.py:539` with an f-string interpolating `", ".join(registered_platforms())`.
- **Re-target `SUPPORTED_PLATFORMS` in `src/backlink_publisher/schema.py:26`** — replace the frozen set with a function (e.g., `def supported_platforms() -> frozenset[str]: return frozenset(registered_platforms())`) and update the 5 consumers (`schema.py:75,78`, `publish_backlinks.py:164,554,636`, `validate_backlinks.py:205`) to call it. The 3 imports of the bare constant must change to import the function. **Without this, R9's acceptance proof fails downstream of argparse.**
- Do **not** touch the `--mode`, `--log-level`, or `--language` argparse choices.

**Patterns to follow:**
- Existing `adapters/__init__.py` import-side-effect pattern.

**Test scenarios:**
- *Happy path:* Invoking `publish-backlinks --platform blogger ...` parses successfully. Same for medium.
- *Happy path (acceptance proof for R9 — non-circular):* In a test, define a `FakeAdapter(Publisher)` subclass with a stub `publish()`. Use a fixture-scoped `register("fake", FakeAdapter)` (with teardown that pops the registry entry). Assert: (a) `publish-backlinks --platform fake ...` argparse-parses successfully; (b) `schema.validate_publish_payload({"platform": "fake", ...})` returns no errors; (c) the fake adapter's `publish()` is invoked end-to-end. This is the falsifiable proof.
- *Error path:* Invoking `publish-backlinks --platform nonexistent ...` produces an argparse error that lists the registered platforms.
- *Edge case:* Empty registry — `registered_platforms()` returns `[]`. Argparse with `choices=[]` rejects everything; acceptable.
- *Integration:* Run `publish-backlinks --help` and assert output contains "blogger" and "medium" (and after the FakeAdapter fixture is active, "fake"). Catches import-order bug.

**Verification:**
- `grep -E 'choices=\["blogger","medium"\]|Supported platforms: blogger, medium' src/backlink_publisher/cli/` returns zero matches.
- `grep -n 'SUPPORTED_PLATFORMS\s*=\s*\{' src/backlink_publisher/schema.py` returns zero matches.
- Adding a fixture-scoped `register("fake", FakeAdapter)` makes `publish-backlinks --platform fake ...` parse successfully AND schema-validate successfully, without any other CLI/schema edit.
- All existing CLI and schema tests still pass.

- [ ] **Unit 2: Replace `_MEDIUM_ADAPTERS` branches with `AdapterResult`-carried throttle metadata** (R9c)

**Goal:** Move the 30 s post-medium-publish wait out of the CLI layer by giving `AdapterResult` a `post_publish_delay_seconds: int` field that each adapter's `publish()` sets, then having the CLI loop call `time.sleep(result.post_publish_delay_seconds)` directly.

**Requirements:** R9 (part c)

**Dependencies:** None (parallel-safe with Unit 1, most coherent in same PR)

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/base.py` (add `post_publish_delay_seconds: int = 0` field to `AdapterResult` dataclass)
- Modify: `src/backlink_publisher/publishing/adapters/medium_api.py`, `medium_brave.py`, `medium_browser.py` (each `publish()` sets `post_publish_delay_seconds=30` on returned `AdapterResult`)
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (delete `_MEDIUM_ADAPTERS` at line 13; replace 4 branches at 119, 253, 326, 759; investigate 273 separately; line 536 is Unit 3's territory)
- Test: `tests/test_publishing_registry.py` or `tests/test_adapter_result.py`
- Test: existing `tests/test_publish_backlinks*.py`

**Approach:**
- Add `post_publish_delay_seconds: int = 0` to `AdapterResult` dataclass.
- In the three `Medium*Adapter.publish()` implementations, set `post_publish_delay_seconds=30` when constructing the result. `dry_run` path sets 0.
- In `publish_backlinks.py`, replace `if result.adapter in _MEDIUM_ADAPTERS: time.sleep(30)` with `if result.post_publish_delay_seconds > 0: time.sleep(result.post_publish_delay_seconds)`.
- Delete `_MEDIUM_ADAPTERS = {"medium-api", "medium-browser"}` at line 13.
- Re-examine each of the 4 branch sites (119, 253, 326, 759) plus the platform-keyed gate at 273.

**Execution note:** Characterization-first. Before changing CLI branches, add a test that captures current throttle behavior end-to-end (publish via medium → assert ~30 s wait observable in a stubbed clock).

**Patterns to follow:**
- The existing `Publisher.available()` classmethod pattern (mirror the shape if adding any new classmethod).
- `registry.py:90` `registered_platforms()` style — read-only, no side effects.

**Test scenarios:**
- *Happy path:* `AdapterResult(post_publish_delay_seconds=30, ...)` from MediumAPIAdapter; `AdapterResult(...)` from BloggerAPIAdapter has default 0.
- *Happy path:* CLI publish loop for medium waits ~30 s (stubbed `time.sleep`). For blogger, `time.sleep` not called (or called with 0).
- *Edge case:* `dry_run=True` on a Medium adapter sets `post_publish_delay_seconds=0`.
- *Edge case:* Adapter explicitly sets 0 — CLI does NOT call `time.sleep(0)`.
- *Error path:* Adapter raises during `publish()` — exception propagates; CLI does not read `result.post_publish_delay_seconds` on non-existent result.
- *Integration:* Existing publish-pipeline integration test with medium preserves throttle. New test: `AdapterResult(post_publish_delay_seconds=60, ...)` from fake adapter, CLI waits 60 s.

**Verification:**
- `grep '_MEDIUM_ADAPTERS' src/backlink_publisher/cli/` returns zero matches.
- All medium-throttle integration tests still pass.
- A `FakeAdapter.publish()` returning `AdapterResult(post_publish_delay_seconds=60, ...)` causes the CLI to wait 60 s with zero CLI edits.

- [ ] **Unit 3: Fold LinkedIn rejection into a registry-driven helper** (R9d)

**Goal:** Replace the three coordinated LinkedIn rejection sites (`schema.py:183`, `publish_backlinks.py:536`, `validate_backlinks.py:202`) with a single helper that surfaces a user-friendly "platform 'X' is not supported; supported: ..." message for any unregistered platform.

**Requirements:** R9 (part d)

**Dependencies:** Unit 1 (the new `supported_platforms()` function from R9e is the source of truth)

**Files:**
- Modify: `src/backlink_publisher/schema.py` (line 183 LinkedIn-specific rejection → registry-driven helper call)
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` (line 536 LinkedIn branch + line 539 error string → call the helper)
- Modify: `src/backlink_publisher/cli/validate_backlinks.py` (line 202 LinkedIn branch + line 205 error string → call the helper)
- Test: `tests/test_schema.py`, `tests/test_publish_backlinks*.py`, `tests/test_validate_backlinks*.py`

**Approach:**
- Phase A — investigation (brief; framing is "live contract", not "dead code"): confirm via `grep -rn linkedin src/ tests/ docs/` that the 3 source sites are the entire surface; read existing test assertions for the linkedin-rejection message.
- Phase B — migration: Add `def reject_unsupported_platform(platform: str) -> Optional[str]` helper next to `supported_platforms()` in `schema.py`. Returns formatted message like `"platform 'X' is not supported in this version; supported: blogger, medium"` for any platform not in `supported_platforms()`, else `None`.
- Replace `if row["platform"] == "linkedin": errors.append("...")` at `schema.py:183` with `if (msg := reject_unsupported_platform(row["platform"])): errors.append(msg)`.
- Replace `if platform == "linkedin"` blocks at `publish_backlinks.py:536` and `validate_backlinks.py:202` with the same helper call.
- Preserve the existing user-facing message tone.

**Patterns to follow:**
- The `supported_platforms()` function created in Unit 1 (R9e).

**Test scenarios:**
- *Happy path:* `reject_unsupported_platform("linkedin")` returns the expected user-facing message; `reject_unsupported_platform("blogger")` returns `None`.
- *Happy path:* CSV row with `platform=linkedin` produces the same human-readable rejection as before.
- *Happy path:* CSV row with `platform=tiktok` (or any unregistered platform) ALSO produces a sensible rejection — coverage now extends beyond linkedin.
- *Integration:* `grep -nE '"linkedin"' src/backlink_publisher/` returns matches ONLY in tests/.

**Verification:**
- `grep -nE 'if (platform|row\["platform"\]) == "linkedin"' src/backlink_publisher/` returns zero matches.
- User-facing rejection message for `linkedin` payloads preserved.
- All existing LinkedIn-rejection test assertions pass after migration.

- [ ] **Unit 4: AGENTS.md "Adding a new publisher adapter" walkthrough** (R5 + R6)

**Goal:** Add a discoverable, copy-paste-runnable walkthrough to `backlink-publisher/AGENTS.md` that takes a contributor from zero to a registered, configured, tested new adapter — citing Blogger at each step.

**Requirements:** R5, R6

**Dependencies:** Units 1, 2, 3 must land first.

**Files:**
- Modify: `backlink-publisher/AGENTS.md` (add new section, ~80-100 lines)
- Modify: `backlink-publisher/README.md` (add link to AGENTS.md section under "For contributors")
- Modify: `src/backlink_publisher/publishing/registry.py` (light docstring refresh)

**Approach:**
- The walkthrough has 5 steps citing Blogger as concrete reference:
  1. **Subclass `Publisher`** — cite `blogger_api.py::BloggerAPIAdapter`.
  2. **Implement `publish()`** — quote the Blogger implementation's structure.
  3. **Register** — show the one-line addition to `adapters/__init__.py`. Note: "post the R9 refactor, you do not edit any CLI file."
  4. **Add config (if needed)** — cite the Blogger config dataclass.
  5. **Add optional dependency (if needed)** — show `pyproject.toml` `[project.optional-dependencies]` addition. Note escalation path for resolver conflicts.
  6. **Add a test** — cite an existing adapter test.
- End with a contributor PR description checklist.
- Light docstring refresh in `registry.py`: update to mention `AdapterResult.post_publish_delay_seconds` and link to AGENTS.md.

**Test scenarios:**
- Test expectation: none — pure documentation change.

**Verification:**
- A reviewer who has never touched the adapter layer can read AGENTS.md and identify all the files to touch.
- README.md's "For contributors" link reaches the AGENTS.md section.
- Owner pairs through the walkthrough on a short call after landing.

- [x] **Unit 5: Delete stale `core/` + relocate workspace-root README** (R3 + R4) — *Completed 2026-05-18. Pre-flight grep confirmed zero live references to `core/src` or `../core` in tracked code (only planning docs mention them). `core/` (which contained `pyproject.toml` for `backlink-publisher-core` v0.2.0 + a `src/` mirroring the live source) deleted from workspace root via `rm -rf ../core`. Workspace-root `README.md` (which falsely claimed a `core/` + `packages/` monorepo and recommended `uv` despite the project using pip) replaced with a 3-line pointer to `backlink-publisher/README.md`. Canonical README at `backlink-publisher/README.md` got a new "Workspace Layout" section documenting the `bp-<topic>/` git worktree convention. Commit `eaf1c07` on `docs/r9-extension-readiness-plan` branch.*

**Goal:** Remove the abandoned `core/` and move the canonical README into the actual git repo.

**Requirements:** R3, R4

**Dependencies:** None

**Files:**
- Delete: `core/` (workspace root)
- Modify: `backlink-publisher/README.md` (append worktree-convention paragraph)
- Modify or replace: `README.md` at workspace root

**Approach:**
- Pre-flight: `grep -rn "core/src" src/ tests/ scripts/ docs/ 2>/dev/null` and check shell aliases for `core/`. CI is already verified clean.
- Action for R3: `rm -rf "../core/"` from inside `backlink-publisher/`. Unversioned (workspace root not a git repo).
- Action for R4: Read current workspace-root `README.md`, relocate useful content into `backlink-publisher/README.md` under a new section with the worktree-convention paragraph. Overwrite workspace-root `README.md` with one-line pointer or delete it.

**Test scenarios:**
- Test expectation: none — operational change.

**Verification:**
- `ls /Users/.../core` returns "no such file".
- `grep -rn 'core/src' src/ tests/ scripts/ docs/ 2>/dev/null` returns zero matches.
- Workspace-root `README.md` is a one-line pointer or absent.
- `backlink-publisher/README.md` contains the worktree-convention paragraph.

- [x] **Unit 6: Remove already-merged worktrees `bp-events-u1` and `bp-events-u6`** (R1) — *Completed 2026-05-18: `bp-events-u6` was already auto-cleaned (dir + branch both gone). `bp-events-u1` working tree was in a corrupted post-packaging-refactor state (mass deletions of OLD-layout files including `src/backlink_publisher/{adapters,cli,events,...}/*`). Branch tip `e23ea1f` confirmed squash-merged to main at `08d0b7e` via PR #49 — no real work lost. Force-removed with `git worktree remove --force ../bp-events-u1` + `git branch -D feat/event-substrate-u1`.*

- [x] **Unit 7: Triage `bp-local-unit{2,4,5,6}` rehearsal worktrees** (R2) — *Completed 2026-05-18: All four worktrees triaged via the R2 decision tree. Outcome: all four KEPT — all are dirty (same post-packaging-refactor mass-deletion pattern as bp-events-u1) AND all four branches are local-only (not pushed to origin, not on main). Per R2 "if ambiguous, keep and surface". These hold the Telegraph adapter implementation work (Units 2-6 of plan 2026-05-15-004); HEAD commits are real work, the deletions on top are post-packaging noise. Safe cleanup path: per-worktree stash+wip-branch then remove, deferred until Telegraph adapter plan lands.*

- [x] **Unit 8: Post-merge worktree auto-cleanup** (R10) — *Completed 2026-05-18 on branch `feat/worktree-auto-cleanup` (commit `07316a2`, pushed). Three shell artefacts: `scripts/_worktree_safety.sh` (sourceable helpers: `wt_is_clean`, `wt_branch_in_main` with squash-merge `gh pr list` fallback, `wt_remove` with self-deletion guard, `wt_list_porcelain`); `scripts/prune-stale-worktrees.sh` (on-demand helper with `--dry-run`/`--force`/`--help`, interactive y/N/q prompts, exit 2 on removal failure); `scripts/install-post-merge-hook.sh` (per-clone installer; hook fires only on main worktree on main branch; notifies by default, auto-removes only when `BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1`). 5 end-to-end pytest tests against fresh fixture repos with merged+clean / unmerged / merged+dirty worktrees — all pass. AGENTS.md got a new "Worktree Auto-Cleanup" section.*

**Goal:** Add two pieces of automation: post-merge hook + prune helper.

**Requirements:** R10

**Dependencies:** None.

**Files:**
- Create: `backlink-publisher/scripts/prune-stale-worktrees.sh`
- Create: `backlink-publisher/.git/hooks/post-merge` template + `scripts/install-post-merge-hook.sh` installer
- Test: `tests/scripts/test_prune_helper.py` or `tests/test_prune_stale_worktrees.sh.bats`

**Approach:**
- Recommend Option B (git hook + installer) + prune helper script. Option A (extend `/ship`) is out of scope. Option C (manual discipline) leaves room for human forgetfulness.
- `scripts/prune-stale-worktrees.sh`: lists worktrees whose HEAD/PR-mergeCommit is reachable from `origin/main`, runs dirty-state guard, prompts before removal. Modes: `--dry-run`, `--force`.
- `scripts/install-post-merge-hook.sh`: writes a `post-merge` hook that detects when a squash-merge brings in the current worktree's branch and runs `git worktree remove` after the dirty-state guard. Hook iterates `git worktree list --porcelain` and matches each worktree's branch against the merged commit's PR via `gh pr list --search 'sha:<HEAD>'`. If running from a bp-* worktree, refuses with clear error.
- Both scripts share dirty-state-guard logic.

**Execution note:** Test-first for the helper script.

**Test scenarios:**
- *Happy path:* `--dry-run` against fixture with one merged + one active worktree lists only the merged one.
- *Happy path:* Post-merge hook after simulated squash-merge of branch `feat/x` while active worktree is `bp-x` → calls `git worktree remove ../bp-x`.
- *Edge case:* Worktree is dirty → both helper and hook abort with clear message.
- *Edge case:* Worktree's branch is currently checked out elsewhere → `git worktree remove` refuses; surface error.
- *Edge case:* No worktrees match → prints "no stale worktrees" and exits 0.
- *Error path:* `gh` not installed/authenticated → degrades to HEAD-based check, warns squash-merged worktrees may be missed.
- *Integration:* Run helper against post-Unit-7 workspace, expect zero candidates.

**Verification:**
- `bash scripts/prune-stale-worktrees.sh --dry-run` exits 0 with "no stale worktrees" on post-Unit-7 workspace.
- Next PR merged after this unit lands auto-removes its worktree.
- Hook installer documented in AGENTS.md.

## System-Wide Impact

- **Interaction graph:** R9 (Units 1-3) changes the CLI/schema ↔ registry contract. `dispatch()` semantics preserved. New `AdapterResult.post_publish_delay_seconds` field consumed by `publish_backlinks.py` only.
- **Error propagation:** Unit 3's helper returns `Optional[str]` — None=pass, string=validation error message. Unit 2's `time.sleep` only called when delay > 0. Unit 8 helper failures non-fatal.
- **State lifecycle risks:** Unit 2 touches publish-loop throttle. Characterization-first execution note prevents accidental "no medium throttle" regression that would breach Medium's rate limits.
- **API surface parity:** `AdapterResult` gains one optional field. `Publisher` ABC unchanged at method level. CLI subcommands' `--platform` choices change from hardcoded to dynamic.
- **Integration coverage:** Unit 1's FakeAdapter acceptance test is the cross-layer integration test for R9. Unit 2's fake-adapter-with-custom-delay test validates throttle decoupling.
- **Unchanged invariants:** `dispatch()` semantics, Medium fallback-chain order, `MediumBraveAdapter.available()` macOS gate, the `publish()` public function signature, OAuth/auth/config behavior, all existing adapter `publish()` implementations, all webui_app routes.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Unit 2 silently breaks Medium throttling, discovered in production via 429 storms | Characterization-first execution note. Acceptance gate explicitly tests medium-throttle preservation via `result.post_publish_delay_seconds`. |
| Unit 3 inadvertently degrades LinkedIn rejection UX | New helper preserves "not supported" phrasing by default. PR description shows side-by-side before/after for `linkedin`. |
| Schema-layer SUPPORTED_PLATFORMS is missed and R9 ships with argparse-accepts-but-schema-rejects bug | R9e is explicit in Unit 1 with its own grep verification. FakeAdapter test exercises BOTH argparse AND schema-validation paths. |
| `registered_platforms()` called at parser-construction before adapters/__init__.py finishes registering | Unit 1's Approach mandates explicit `import backlink_publisher.publishing.adapters # noqa: F401` at top of plan_backlinks.py and validate_backlinks.py. |
| Unit 7 force-removes a dirty worktree, losing uncommitted phase 0 work | Dirty-state guard non-negotiable. Surface to owner if ambiguous. **Mitigated: all 4 worktrees were KEPT per the decision tree.** |
| Unit 8 post-merge hook misfires on non-squash merge or stacked work | Hook detects only when currently-checked-out branch of the bp-* worktree is being merged. Test scenarios cover edge cases. |
| Unit 4 walkthrough drifts as registry pattern evolves | `registry.py` docstring is summary with link to AGENTS.md (single source of truth). Bi-directional link makes drift visible. |
| R9 and F7 step on each other in the same files | Order-independent. Pre-flight turf-check per memory `[Multi-agent turf-check]`. |
| R9 and bp-ko-html collide on schema.py + publishing/adapters | **Active blocker**: R9 deferred until bp-ko-html lands. Re-check line numbers after ko-html ships before starting Unit 1. |
| Telegraph adapter, when actually added in a future plan, finds R9's contract doesn't fit | R9 contract shaped by existing `Publisher` ABC and actual extension recipe. Telegraph's plan (2026-05-15-004) was already mid-flight when R9 designed. |
| Workspace-root README pointer absent file confuses tooling | Brainstorm explicitly accepted this. Mitigation is the pointer-file content. |

## Documentation / Operational Notes

- **Documentation updates:**
  - `backlink-publisher/AGENTS.md`: new "Adding a new publisher adapter" section (Unit 4).
  - `backlink-publisher/README.md`: appended workspace/worktree layout paragraph + "For contributors" link (Units 4, 5).
  - `src/backlink_publisher/publishing/registry.py`: docstring refresh (Unit 4).
  - Workspace-root `README.md`: replaced with one-line pointer or deleted (Unit 5).
- **Operational notes:**
  - Unit 8's post-merge hook is opt-in. Documented in AGENTS.md.
  - `scripts/prune-stale-worktrees.sh` runnable manually anytime.
  - No CI changes required.
  - No migration, no data backfill, no feature flag.

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-18-codebase-hygiene-requirements.md`
- **Related code:**
  - `src/backlink_publisher/publishing/registry.py:41,82,90,95` — Publisher ABC + register + registered_platforms + dispatch
  - `src/backlink_publisher/publishing/adapters/__init__.py` — current registration site
  - `src/backlink_publisher/cli/publish_backlinks.py:13,119,253,273,326,387,536-539,759` — hardcoding surfaces
  - `src/backlink_publisher/cli/plan_backlinks.py:1488` — argparse choices
  - `src/backlink_publisher/cli/validate_backlinks.py:202` — linkedin branch
  - `src/backlink_publisher/schema.py:26,75,78,183` — SUPPORTED_PLATFORMS surface
- **Related plans:**
  - `docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md` — Unit 7 landed the registry
  - `docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md` (in `bp-f7-monolith`) — F7 ratchet
  - `docs/plans/2026-05-15-004-feat-telegraph-adapter-plan.md` — Telegraph adapter (separate, in-flight)
- **Related PRs:** #49 (squash `08d0b7e` — bp-events-u1 source, removed), #47 (squash `a8345ef` — bp-events-u6 source, removed), #55 (events U6 merged since memory), #56 (F7 monolith merged since memory)
- **Memory references:** `[Worktree Concurrent Switching]`, `[Multi-agent turf-check]`, `[Verify External Commits Before Push]`, `reference_phase0_local_rehearsal_branches.md`, `[CI workflow pr_filter quirk]`

## Phased Delivery

**Load-bearing sequence (must ship in order, R9 blocked on bp-ko-html landing first):**
- **PR #1 — `refactor(cli): de-couple platform hardcoding (R9)`** — Units 1 + 2 + 3 together. Unit 4 can be drafted in parallel.
- **PR #2 — `docs(adapters): add 'Adding a new publisher adapter' walkthrough`** — Unit 4, merged after PR #1.

**Independent hygiene + sustainability (any order):**
- Unit 5 — one PR (`core/` deletion + README relocation).
- Unit 6 — ✅ DONE 2026-05-18, operational.
- Unit 7 — ✅ DONE 2026-05-18, operational triage (all 4 KEPT).
- Unit 8 — one PR (`scripts/prune-stale-worktrees.sh` + post-merge hook + installer + AGENTS.md installation note).

**F7 coordination:** Order-independent. Pre-flight turf-check only.

## Open Questions

### Resolved During Planning

- **AGENTS.md walkthrough location?** Inline (AGENTS.md is 18 lines; tripled stays scannable).
- **`post_publish_delay_seconds` contract?** `int` field on `AdapterResult` itself, set by each adapter's `publish()`. NOT classmethod with reverse lookup.
- **Does R9 wait for F7?** Order-independent.
- **Telegraph adapter shipped or non-existent?** Non-existent (`telegraph_node.py` is a converter, not a Publisher).
- **R9e its own unit?** Part of Unit 1.
- **LinkedIn dead or live?** Live user-facing contract; migrated to generic helper.
- **CI editing for Unit 5?** No (verified).

### Deferred to Implementation

- **Exact file path of existing CLI tests** — implementer confirms in-unit.
- **Per-branch resolution for the 4 `_MEDIUM_ADAPTERS` sites** + the platform-keyed gate at 273 — implementer maps each in Unit 2.
- **Exact name + return type of `reject_unsupported_platform()` helper** — implementer chooses based on existing schema.py style.
- **Unit 8 hook semantics details** — iterate `git worktree list --porcelain`, match against PR via `gh pr list --search 'sha:<HEAD>'`.
- **Whether `scripts/_worktree_safety.sh` extracted as sourceable helper** — implementer chooses.

## Next Steps

→ `/ce:work` to begin implementing **once `bp-ko-html` lands** (Units 1-4). Units 6 + 7 already complete. Units 5 and 8 can run independently anytime.

## Session Log

- **2026-05-18 brainstorm**: 4-persona document-review, adversarial finding flipped premise (registry alone insufficient). User chose to refine + add R9 CLI de-coupling, R10 worktree automation, R4 README relocation; dropped R7/R8 standalone (folded into R5).
- **2026-05-18 plan**: deepening pass + headless document-review surfaced 3 P0s: Telegraph non-existent, SUPPORTED_PLATFORMS schema-layer missed, post_publish_delay max-across-chain wrong. All resolved.
- **2026-05-18 execution (this session)**: Units 6 + 7 completed; Units 1-3 blocked on bp-ko-html collision (turf-check identified ko-html has uncommitted edits to schema.py and adapters); plan file lost when concurrent session on `feat/history-filter` wiped untracked files in main worktree; reconstructed into isolated `bp-r9-plan` worktree on branch `docs/r9-extension-readiness-plan`.
