---
title: "fix: Pytest Bug Sweep — Full-Suite Failure Triage & Sequential Repair"
type: fix
status: completed
date: 2026-05-18
deepened: 2026-05-18
completed: 2026-05-18
origin: docs/brainstorms/2026-05-18-pytest-bug-sweep-requirements.md
pr: https://github.com/redredchen01/backlink-publisher/pull/40
report: docs/bug-sweep-2026-05-18.md
---

# fix: Pytest Bug Sweep — Full-Suite Failure Triage & Sequential Repair

## Overview

Run the full `pytest` suite on backlink-publisher, collect every `FAILED` / `ERROR` case, triage by module + root-cause type, and fix sequentially from foundation modules upward. Default repair target is `src/`; tests are modified only under three named exceptions (inverted-negative, shape-only, stale-docstring), each flagged in the final report. Final deliverable: `docs/bug-sweep-2026-05-18.md` with per-failure root cause, fix summary, and before/after pass counts.

## Problem Frame

`main` (HEAD `2b1656f`) has absorbed multiple feature merges in the last two weeks (adapter retry, content-fetch gate, anchor entropy, homepage three-tier form, telegraph node converter from PR #37 squash-merged as `d769bff`). The working tree at planning time is **not** on `main` — it sits on branch `refactor/webui-contract-tests` (HEAD `c650d5e refactor(webui): extract JsonStore`), which is **behind** `main` on PR #37's telegraph files and ahead on the JsonStore + webui contract tests work. The 52-file pytest suite on `main` (53 on the current branch) has not been verified green end-to-end since the recent landings; baseline failure count is unknown.

In-flight PRs against `main`: #36 (docs/telegraph-phase0-report), #38 (spike/velog-phase0 — docs/spike only), #39 (refactor/webui-contract-tests — this branch's parent PR). PR #37 is **already merged** (`d769bff`); coordinate-as-in-flight logic for #37 is removed from this plan.

We need a one-pass sweep on **base = `main` (2b1656f)** that converges to 0 failure / 0 error. The current `refactor/webui-contract-tests` branch's work is intentionally out of scope.

See origin: `docs/brainstorms/2026-05-18-pytest-bug-sweep-requirements.md`.

## Requirements Trace

- **R1** Run full `pytest`, collect FAILED/ERROR/SKIPPED.
- **R2** Classify by (a) failing module, (b) error type, (c) dependency layer.
- **R3** Per-failure one-liner root-cause hypothesis + "src bug vs test stale" judgment.
- **R4** Fix bottom-up: foundation → primitives → domain → adapters → orchestration. Re-run downstream slice after each batch.
- **R5** Default fix `src/`. Modify test only under inverted-negative / shape-only / stale-docstring exceptions; flag each case in the report.
- **R6** Re-run target + full suite after each fix; no new SKIPs introduced to mask failures.
- **R7** One atomic commit per fix, conventional English subject.
- **R8** Final report at `docs/bug-sweep-2026-05-18.md`: per-failure root cause + diff summary + test-change flag + new-dependency flag + unresolved-with-reason list.
- **R9** Pass/fail/skip count delta before vs after.

## Scope Boundaries

- OUT: ruff / mypy / bandit / any non-test static analysis unless it directly triggers a pytest failure.
- OUT: refactors, performance work, new features, dependency upgrades (unless a failure's root cause is a version mismatch).
- OUT: test-style cleanup, parametrize rewrites, fixture extraction.
- OUT: real-network integration tests against Blogger / Medium / Google OAuth — record env-bound failures in report, do not "fix" by hardcoding creds.
- OUT: `webui_store/` data edits, `fixtures/` data edits, CI config.
- OUT: changes to `docs/solutions/*` frontmatter (two reverts on record — enshrined).

## Context & Research

### Relevant Code and Patterns

**Module layout** (`src/backlink_publisher/`) mapped to test files:

| Layer | Modules | Representative tests |
|---|---|---|
| **L1 Foundation** | `errors`, `io_utils`, `jsonl`, `url_utils`, `markdown_utils`, `logger`, `config`, `config_echo` | `test_io_utils.py`, `test_url_utils.py`, `test_logger_redactor.py`, `test_config*.py` (6 files), `test_config_echo.py`, `test_markdown_render.py` |
| **L2 Primitives** | `content_fetch`, `linkcheck`, `language_check`, `schema`, `bulk_input`, `footprint`, `checkpoint` | `test_content_fetch.py`, `test_edge_cases.py`, `test_linkcheck.py`, `test_language_check.py`, `test_gate_properties.py`, `test_bulk_input.py`, `test_footprint.py`, `test_checkpoint.py` |
| **L3 Domain** | `anchor_*` (lang/metrics/profile/resolver/scheduler), `work_scraper`, `work_themed_generator`, `verify_publish` | `test_anchor_*.py` (5 files), `test_llm_anchor_provider.py`, `test_report_anchors.py`, `test_work_*.py`, `test_short_article_renderer.py`, `test_verify_publish.py`, `test_publish_verify_integration.py` |
| **L4 Adapters** | `adapters/{base,retry,link_attr_verifier,llm_anchor_provider,blogger_api,medium_api,medium_browser,medium_brave}` | `test_adapter_base.py`, `test_adapter_retry.py`, `test_adapter_blogger_api.py`, `test_adapter_medium_api.py`, `test_adapter_medium_browser.py`, `test_adapter_dispatcher.py`, `test_link_attr_verifier.py` |
| **L5 Orchestration** | `cli/{plan_backlinks, validate_backlinks, publish_backlinks, report_anchors, footprint}`, `webui.py` + `webui_app/` | `test_plan_backlinks*.py` (3), `test_publish_backlinks*.py` (4), `test_validate_backlinks.py`, `test_validate_zh_short_payload.py`, `test_silent_drop_tripwire.py`, `test_throttle.py`, `test_webui_*.py` |

**Conftest autouse fixtures** (`tests/conftest.py`):
1. `_mock_publish_check_url` — patches `publish_backlinks.check_url` → `(True, None)`.
2. `_mock_content_fetch` — patches `verify_urls_batch` + `verify_url_has_content` to default-pass; **skips by string match** on `tests/test_content_fetch.py`; resets `content_fetch.reset_cache()` per test.
3. `_disable_real_network` — `pytest-socket.disable_socket(allow_unix_socket=True)`; **soft-fallback no-op if pytest-socket missing**.

**Registered markers** (`pyproject.toml`): only `real_ssrf_check`. **No `enable_socket`, no `network` marker** — per-test socket escape does not exist; test-asking-real-network = test bug.

**Test config**: no `addopts`, no `pytest-randomly`, no `pytest-xdist`; sequential filesystem-order discovery.

**Layer DAG is not a strict DAG — known back-edge**:
- `src/backlink_publisher/anchor_resolver.py:27` imports `from .adapters.llm_anchor_provider import ...`. L3 (domain) → L4 (adapters) back-edge. Strict bottom-up fixing breaks here: an `anchor_resolver` failure may need the L4 adapter fixed first. Treat `adapters/llm_anchor_provider` as **L3-shared** during this sweep; fix it inside Unit 4 (L3) when any anchor_resolver test reds, even though the module physically lives in `adapters/`. Audit follow-up: run `grep -rn "from .adapters" src/backlink_publisher --include="*.py" | grep -v "^src/backlink_publisher/adapters/"` at Unit 1 to surface any other back-edges before triage.

**Active PR surfaces to coordinate with**:
- #39 `refactor/webui-contract-tests` — the current working tree's branch. Touches `webui_app/` + `tests/test_webui_*` (overlap with L5). Note: if base = `main`, sweep does **not** include this branch's JsonStore refactor + new contract tests.
- #38 `spike/velog-phase0` — docs/spike, no blast-radius overlap.
- #36 `docs/telegraph-phase0-report` — docs only, no blast-radius overlap.
- #37 `feat/telegraph-adapter-unit3` — **MERGED** as `d769bff` on main. `src/backlink_publisher/adapters/telegraph_node.py` and `tests/test_telegraph_node.py` are now part of `main` and are in-scope for the sweep when base=`main`.

### Institutional Learnings

- **`docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`** — three hotspots in Medium adapter fallback testing:
  - `--timeout=N` requires `pytest-timeout` in `[dev]`. Without it, pytest **exits code 4** before any test runs. Verify before reading any failure list.
  - **macOS Brave running silently makes `MediumBraveAdapter.publish()` execute for real** via AppleScript. Mock every level of the fallback chain (`MediumAPIAdapter` → `MediumBraveAdapter` → `MediumBrowserAdapter`).
  - Module-level `backlink_publisher.cli.publish_backlinks.time.sleep` must be mocked in any batch test — throttle is 60-300s.
- **`docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md`** — when a fix turns a test red, do **not** delete it. Negative-shape assertions (`assert X not in result`, `def test_..._does_not_*`) may be load-bearing. Recover by inverting polarity, renaming, or adding a positive complement. Audit recipe: `rg -n 'assert\s+.+\s+not\s+in\b' tests/` and `rg -n 'def test_.*(does_not|must_not|should_not|is_read_only)' tests/`.
- **`docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`** — shape-only assertions (`isinstance(warnings, list)`) hide no-op gates. Suspect the test if it only checks shape after a real fix surfaces it.

### External References

None — this is internal triage; existing repo docs and conftest are sufficient grounding.

## Key Technical Decisions

- **Isolated git worktree based off `main`, explicit base ref.** Use `git worktree add ../bp-bugsweep-001 -b fix/pytest-bug-sweep-2026-05-18 main` from the repo. **Never** omit the base ref: `git worktree add` without a ref silently branches from the **current** HEAD, which at planning time is `c650d5e` on `refactor/webui-contract-tests` — not what we want. **Rationale**: per `MEMORY.md feedback_worktree_concurrent_switching`, external processes can switch branches and erase uncommitted work; worktree isolates the working tree from concurrent agent activity. **Caveat**: worktrees share `.git/` (object DB, refs, hooks). Worktree does not protect against another agent running `git fetch`/`git gc`/`git push` against the same `.git/`. Unit 1 snapshots `main` HEAD and re-checks at each layer boundary; abort if it moves unexpectedly.
- **Push sweep branch to origin at each layer boundary; PR creation stays opt-in.** After each layer (L1, L2, L3, L4, L5) reaches green, `git push -u origin fix/pytest-bug-sweep-2026-05-18` so work is durable on the remote. **No PR is opened automatically.** Final Unit 3 presents user with "open PR / hold / discard" options. **Rationale**: locally-only state is the highest-risk state per `feedback_worktree_concurrent_switching`. Pushing the ref ≠ merging it; we keep the report-as-deliverable principle while eliminating the overnight-loss-window risk.
- **Repro command is `pytest -p no:randomly --tb=short -ra`.** `no:randomly` is a no-op (plugin not installed) but stated explicitly to lock semantics across worktrees. `--tb=short` keeps the failure list readable. `-ra` exposes the full SKIP reason set so we can audit each one. **Do not** add `--timeout=N` unless `pytest-timeout` is confirmed in installed deps (else exit code 4 before any test runs).
- **Test-modification gate is exception-based, not permission-based.** Modifying a test is allowed only when one of four conditions is met and explicitly documented in the per-fix report row: (a) **inverted-negative** polarity (the negative shape encodes the bug we are fixing), (b) **shape-only** assertion (no behavior check), (c) **stale-docstring** rationalizing buggy behavior, (d) **contract-evolution** (the source legitimately evolved via a merged PR or refactor and the test still encodes the pre-evolution contract — requires linking to the originating commit/PR). All other red tests under a fix indicate the fix is wrong, not the test. **Rationale**: institutional precedent — see `inverted-negative-assertion-enshrined-config-save-data-loss`. The fourth category exists because Problem Frame names recent merges (adapter retry, content-fetch gate, anchor entropy, JsonStore refactor) — pre-evolution tests are the modal "test-needs-update" case in this repo right now.
- **Fix order is layer-bottom-up, but within a layer triage by error type.** Inside each layer, group: ImportError/ModuleNotFoundError (env first) → AttributeError/TypeError (signature drift) → assertion failures (logic). Re-run only the affected file after each fix; re-run the whole suite at layer boundaries (L1→L2, L2→L3, ...). **Rationale**: ImportError in L1 will cause cascade red across all 53 files and inflate the failure count; clearing them first gives a true L2+ baseline.
- **Environment-bound failures are recorded, not "fixed".** Tests requiring real OAuth credentials, real Blogger/Medium API, real Playwright browser instances, or non-loopback network are flagged as `ENV-BOUND` in the report with the specific missing prerequisite. They count as neither "fixed" nor "broken"; they remain in the post-sweep skip/fail bucket with an annotation.
- **macOS Brave is quiesced before the run.** Verify with `pgrep -i "Brave Browser"`; if non-empty, prompt to quit. **Rationale**: known macOS-specific real-AppleScript trap.
- **One atomic commit per root cause** (not per failure row). No squashing. Commit subject: `fix(<module>): <one-liner>` where `<module>` is the L1-L5 leaf (`config`, `content_fetch`, `adapter-medium`, `cli-publish`, `webui`, etc.). Final commit: `docs(bug-sweep): publish 2026-05-18 sweep report`. **Rationale**: a single ImportError causing 20 cascade reds is one commit, not 20. The report tracks `root_cause_group_id` per failure so the count is faithful.

## Open Questions

### Resolved During Planning

- **Base ref**: `main` (HEAD `2b1656f`). 52-file test suite. PR #39's JsonStore + new webui contract tests are out of scope.
- **Push policy**: push branch to origin at each layer boundary; PR creation opt-in at Unit 3.
- **Should we work on `main` directly?** No — isolated worktree, base ref explicit.
- **Is `pytest-randomly` involved?** No — not installed and not in `[dev]` deps. Order is stable.
- **Can a test opt back into real network?** No — only `real_ssrf_check` marker is registered; no `enable_socket` exists. Tests that "want network" are test bugs.
- **Which PR surfaces overlap with the sweep?** #39 (webui) only — #37 is merged, #36/#38 are docs/spike. Coordinate #39 at report time, not during fixing.
- **Where does the report live?** `docs/bug-sweep-2026-05-18.md` at repo root. Not under `docs/solutions/` (those are case-studies, not run reports).
- **Are `pytest-timeout` and `pytest-socket` declared in `[dev]`?** Yes — `pyproject.toml` line 24 lists both. Verify *installed* in the worktree's venv before any timed run (Unit 1).

### Deferred to Implementation

- **Actual failure count and distribution by layer.** Knowable only after the first `pytest --collect-only` + full run. Two pause gates apply: (a) total `failed + errored > 30`, OR (b) any single failure's traceback head implicates more than one cross-layer module (suggesting fundamental refactor, not a per-test bug). Either trips pause-and-re-evaluate.
- **Cascade dedup before triage.** Before assigning fixes to baseline rows, cluster failures by traceback signature head. A single L1 ImportError causing 20 cascade reds is one root cause, not 20. The baseline schema must accommodate this: each row carries `root_cause_group_id`, and one `fix_commit_sha` may resolve multiple rows. R7's "one atomic commit per fix" reads as "one commit per root cause", not per failure row.
- **Are there test-order pollution failures?** Detectable only by re-running affected files in (a) isolation and (b) reverse filesystem order at each layer boundary. If a file passes alone but fails in suite, isolate the polluting fixture/global — typically APScheduler or Flask app config singleton.
- **Is PR #39's branch already known to red the suite?** Check `git log --since="14 days ago" --grep="known fail\|skip later\|TODO test"` and the PR description before assuming a failure originated from `main`. PR #37 is merged — its failures, if any, are now `main`-side bugs.
- **Is the `c650d5e` JsonStore refactor itself the root cause of any webui failure?** Only relevant if base = (B) or (C). If base = (A) main, JsonStore is not in scope.
- **Conftest as root cause.** If a failure traces to a fixture bug in `tests/conftest.py`, stop the sweep at the current commit, write a `DEFERRED-CONFTEST` entry in the report with the fixture name + symptom, and exit Unit 6 with the suite **not** fully green. This is an explicit accepted termination state, not unhandled.
- **Solutions-doc freshness.** Before relying on a docs/solutions pattern in Units 2-5, run its audit recipe (e.g., `rg -n 'assert\s+.+\s+not\s+in\b' tests/`) and a quick read of the now-current source. If drifted, record "pattern verified current" or "pattern drifted, adjusted as: ..." in baseline metadata; skip the doc's prescription if it no longer fits.
- **Conftest string-match special case follow-up.** Surface as a follow-up in the final report: consider migrating `tests/conftest.py:63` string-match on `test_content_fetch.py` to a pytest marker (parallel to `real_ssrf_check`). Out of scope for this sweep; out-of-scope notice is the fix.
- **Pytest output parsing.** Use `pytest --junit-xml=baseline.xml` (built-in, no new dep) for structured baseline capture instead of regex-parsing `--tb=short` text. Convert XML → baseline.json. Decision deferred to Unit 1: if XML produces poorer signal than expected, fall back to text parse + note in report.

## Implementation Units

- [ ] **Unit 1: Worktree Bootstrap & Baseline Capture**

**Goal:** Create the isolated worktree, install dev deps, run the first full pytest, and produce a structured baseline failure log.

**Requirements:** R1, R2

**Dependencies:** None.

**Files:**
- Worktree dir (sibling of repo): `../bp-bugsweep-001/` based off `main` (HEAD `2b1656f`) on branch `fix/pytest-bug-sweep-2026-05-18`
- Modify: nothing in `src/` or `tests/` in this unit
- Create: `docs/bug-sweep-2026-05-18.md` directly (the report file) with: env-check section, environment snapshots, baseline pre-table (failure rows with empty fix-commit columns). No JSON intermediate.
- Create (kept): `docs/bug-sweep-2026-05-18-baseline.xml` from `pytest --junit-xml=...` (raw pytest output, kept for traceback signature clustering across Unit 2; deleted at end of Unit 3)

**Approach:**
- `git worktree add ../bp-bugsweep-001 -b fix/pytest-bug-sweep-2026-05-18 main` — **base ref is mandatory**, do not omit. Capture `main` HEAD SHA.
- Snapshot `main` HEAD as `main_snapshot_sha`. Re-check at each layer-boundary; abort if it moved unexpectedly (concurrent agent activity).
- In worktree: ensure venv, `pip install -e ".[dev]"`. Verify `pytest-timeout`, `pytest-socket`, and `pytest-asyncio` are *installed* (`pip show ...`). Record versions.
- Quiesce macOS Brave: `pgrep -i "Brave Browser"` — if non-empty, prompt user to quit before continuing. On non-macOS (`uname` ≠ Darwin), skip with note. If `pgrep` is unavailable (`command -v pgrep` empty), prompt user manually.
- Run **layer back-edge audit**: `grep -rn "from .adapters" src/backlink_publisher --include="*.py" | grep -v "^src/backlink_publisher/adapters/"` — record every L1-L3 → L4 import found. Known: `anchor_resolver.py` → `adapters/llm_anchor_provider`.
- Run **solutions-doc freshness audit**: for each of the three cited docs/solutions entries, execute its audit recipe and a 30-second source read; record verdict (`verified` | `drifted: ...`).
- Run `pytest --collect-only -q` first — surfaces collection-time `ImportError` before any test executes.
- Run `pytest -p no:randomly --tb=short -ra --maxfail=999 --junit-xml=docs/bug-sweep-2026-05-18-baseline.xml --no-header --durations=10`. Capture stdout/stderr to a tmp log as fallback.
- **Cluster by traceback signature**: parse the JUnit XML; hash the head of each traceback to compute `root_cause_group_id`; rows in the same group share one ID. Cascade ImportErrors (1 root → N reds) become visible at triage time.
- **Write `docs/bug-sweep-2026-05-18.md` directly** with the structure: front-matter (date, base SHA, env-check block), Summary section (pre-counts only at this stage; post-counts added in Unit 3), Per-Layer Narrative (one heading per layer, body added during Unit 2), Per-Failure Table (one row per failure with columns: `Test`, `Layer`, `RootCauseGroupID`, `RootCauseHypothesis`, `FixCommit` [empty], `TestModified` [empty], `Status` [BASELINE]), Follow-ups section [empty]. No intermediate JSON.
- **Stop points** (either trips a pause):
  - total `failed + errored > 30`, OR
  - any single failure's traceback head implicates **>1 cross-layer module** (suggesting fundamental refactor, not per-test bug).
  If paused, report to user and await explicit go-ahead.

**Execution note:** Characterization-first. Capture the baseline exactly before any fix; this is the comparison point for R9's pass/fail delta. No `src/` edits in this unit.

**Patterns to follow:**
- Worktree bootstrap pattern from prior `MEMORY` feedback.
- Markdown report convention from prior plans in `docs/plans/`.

**Test scenarios:**
- Test expectation: none — this unit is environment setup + observation. Manual verification: report's baseline row count + `passed` count match `pytest --collect-only` collected count exactly.

**Verification:**
- `../bp-bugsweep-001/` exists, on branch `fix/pytest-bug-sweep-2026-05-18`, based off `main`.
- `docs/bug-sweep-2026-05-18.md` exists with: env-check block, pre-counts, per-failure table populated with BASELINE status rows, empty Per-Layer Narrative and Follow-ups (filled in later units).
- `docs/bug-sweep-2026-05-18-baseline.xml` exists (pytest junit-xml output).
- Brave is not running on macOS; pytest-timeout/socket/asyncio installed status recorded.
- User has been notified of total failure count + cross-layer traceback check; if either stop point trips, explicit go-ahead is received before Unit 2.

---

- [ ] **Unit 2: Sequential Layer Repair (L1 → L5)**

**Goal:** Walk each layer L1→L5 from `main`'s baseline to green, with hotspot-aware handling and per-layer push to origin.

**Requirements:** R3, R4, R5, R6, R7

**Dependencies:** Unit 1 baseline rows exist in `docs/bug-sweep-2026-05-18.md` (the report file is created in Unit 1 and progressively appended; there is no intermediate JSON to manage).

**Files (per layer — same shape repeats for L1..L5):**
- Modify: `src/backlink_publisher/` files in the layer's module set (see Context & Research table)
- Test: corresponding `tests/test_*.py` files
- Modify (only under named exception): same test files, with exception category recorded
- Append rows to: `docs/bug-sweep-2026-05-18.md` per-failure table

**Approach — per layer iteration (apply at L1, then L2, ..., L5):**

1. **Triage**: cluster the layer's baseline rows by `root_cause_group_id`. Within each group, order by error type: ImportError → AttributeError/TypeError → assertion failures.
2. **Fix in src/**: write one-liner root-cause in the report row, implement fix. Default target is `src/`; the four named test-modification exceptions (inverted-negative, shape-only, stale-docstring, contract-evolution) apply with `test_modified_reason` recorded.
3. **Inner-loop verify**: `pytest tests/test_X.py -x` for the affected file. Green → re-run all of the layer's test files.
4. **Layer-boundary verify**: re-run the cumulative suite (L1 → L1; L2 → L1+L2; ... L5 → full 53 files).
5. **Order-pollution sample** (at each layer boundary, not just L5): pick 2 test files from the just-completed layer, run each in isolation. If isolated-pass but suite-fail, log under Outstanding Items and pause before continuing.
6. **Push to origin**: `git push -u origin fix/pytest-bug-sweep-2026-05-18` so work is durable on remote.
7. **`main` HEAD re-snapshot**: `git rev-parse main`; abort if it moved unexpectedly (shared `.git/` race).

One commit per **root cause**, not per failure row: `fix(<module>): <one-liner>`.

**Hotspot table — per-layer specifics:**

| Layer | Modules | Hotspots / docs/solutions to consult |
|---|---|---|
| **L1 Foundation** | `errors`, `io_utils`, `jsonl`, `url_utils`, `markdown_utils`, `logger`, `config`, `config_echo` | `config.py` snapshot-history must preserve atomic-write per [inverted-negative-assertion-enshrined-config-save-data-loss](docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md). Audit recipe: `rg -n 'assert\s+.+\s+not\s+in\b' tests/test_config*.py`. |
| **L2 Primitives** | `content_fetch`, `linkcheck`, `language_check`, `schema`, `bulk_input`, `footprint`, `checkpoint` | **`test_content_fetch.py` is conftest:63 string-matched** — do not rename/relocate. `language_check` is the no-op-gate hotspot per [language-matches-always-true-no-op-gate](docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md); shape-only `assert isinstance(warnings, list)` is insufficient — require a positive-rejection assertion. `checkpoint._cache_dir` must be patched to `tmp_path` in tests. |
| **L3 Domain** | `anchor_*` (5 modules), `work_scraper`, `work_themed_generator`, `verify_publish` | **L3→L4 back-edge**: `anchor_resolver.py:27` imports `adapters/llm_anchor_provider`. Fix that adapter here in L3 (treat as L3-shared), not in L4. Anchor entropy thresholds per [anchor-entropy-alarm plan](docs/plans/2026-05-14-002-feat-anchor-entropy-alarm-plan.md). `test_publish_verify_integration.py` is integration — ENV-BOUND if it needs real network. |
| **L4 Adapters** | `adapters/{base,retry,link_attr_verifier,llm_anchor_provider,blogger_api,medium_api,medium_browser,medium_brave,telegraph_node}` | **macOS Brave hotspot** (consulted [ci-test-isolation-failures-medium-brave-sleep-timeout](docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md)): mock all of `MediumAPIAdapter → MediumBraveAdapter → MediumBrowserAdapter`; patch `backlink_publisher.cli.publish_backlinks.time.sleep` (module-level, not stdlib). PR #37's `telegraph_node` is on `main` and in-scope. |
| **L5 Orchestration** | `cli/{plan_backlinks, validate_backlinks, publish_backlinks, report_anchors, footprint}`, `webui.py` + `webui_app/` | Preserve silent-drop tripwire in `test_silent_drop_tripwire.py` (load-bearing negative assertion). PR #39 surface = out of scope (base=main). PR #39 overlap diff command: `git fetch origin && git diff main..origin/refactor/webui-contract-tests -- src/`. |

**Execution note:** When in doubt about a test's polarity before editing, run audit recipes for the file: `rg -n 'assert\s+.+\s+not\s+in\b' tests/test_X.py` and `rg -n 'def test_.*(does_not|must_not|should_not|is_read_only)' tests/test_X.py`.

**Patterns to follow:**
- Adapter fallback mocking pattern (see L4 hotspot doc).
- Atomic-write pattern in `config.py` (see L1 hotspot doc).
- Hypothesis property tests in `test_gate_properties.py` — preserve property invariants on any L2 fix.
- Conventional commit `fix(<module>): ...`.

**Test scenarios (apply per layer; categories that materialize depend on layer):**
- *Happy path* — every fix's targeted test goes green; no new SKIPs introduced.
- *Edge case* — `config.py` round-trip after a polarity inversion; `test_content_fetch.py` location/name unchanged; conftest string-match still active.
- *Error path* — `errors.py` / `verify_publish` preserve exception type AND message-substring contracts. `language_check.matches` fixes carry positive-rejection assertion.
- *Integration* — `test_publish_verify_integration.py` outcome categorized: green / red-but-ENV-BOUND / red-and-fixable.
- *Order-pollution* — isolated-vs-suite diff at every layer boundary (not just L5). Two sample files per layer.
- *Contract-evolution* — any test modified under the 4th exception links the originating commit/PR in the report row.

**Verification:**
- All 52 test files green in full-suite re-run at end of L5; `failed == 0`, `errored == 0`.
- Each completed layer pushed to origin.
- Report file has every failure row populated with `fix_commit_sha` OR `ENV-BOUND` / `OUT-OF-SCOPE` reason.
- Test-modification exceptions zero OR each recorded with category + originating-commit link.
- `main` HEAD snapshot recorded at each layer boundary; no unexpected motion.
- Order-pollution sampling clean at every layer; if any layer fails the sample, work is paused with note in report.

---

- [ ] **Unit 3: Final Report Finalization & Handoff**

**Goal:** Finalize the report file (already incrementally written through Units 1-2), commit as the final atomic commit, push, and present PR option.

**Requirements:** R8, R9

**Dependencies:** Unit 2 complete (full suite green); report file in mid-state with all per-failure rows + per-layer narratives accumulated.

**Files:**
- Finalize: `docs/bug-sweep-2026-05-18.md` (write Summary delta + Follow-ups sections; cross-check row count vs commit count)

**Approach:**
- Top section: pass/fail/skip delta. Example: `Before: 412 passed, 18 failed, 3 errored, 5 skipped. After: 430 passed, 0 failed, 0 errored, 8 skipped (3 ENV-BOUND, 5 pre-existing)`.
- Verify the per-failure table: every row has `fix_commit_sha` or a categorical status; every distinct `fix_commit_sha` corresponds to one commit in `git log --oneline fix/pytest-bug-sweep-2026-05-18 -- src/`.
- Final `Follow-ups` section:
  - Coordinate-notes for PR #39 maintainer if any surface was touched (default: none, since base=main).
  - Conftest string-match → marker-based opt-out (deferred follow-up).
  - Any layer that hit the order-pollution sample warning.
  - Solutions-doc freshness drift findings, if any.
- Commit: `docs(bug-sweep): publish 2026-05-18 sweep report`. Push to origin.
- Present user with three options: (1) `gh pr create --base main --head fix/pytest-bug-sweep-2026-05-18 --title "fix(tests): pytest bug sweep 2026-05-18"`, (2) hold (branch is on remote, no PR), (3) discard worktree (`git worktree remove ../bp-bugsweep-001 && git branch -D fix/pytest-bug-sweep-2026-05-18`).

**Execution note:** Report is the human-review artifact. Quality bar: user can read it in 5 minutes and decide per-commit whether to keep, revert, or follow up.

**Patterns to follow:**
- Markdown table convention from prior plans in `docs/plans/`.
- `docs/solutions/` style for the per-layer narrative paragraphs (root cause → fix → invariant preserved).

**Test scenarios:**
- Test expectation: none — this is documentation. Manual verification: report's pre/post counts match `pytest --collect-only` count exactly; every row in the table has a corresponding commit.

**Verification:**
- `docs/bug-sweep-2026-05-18.md` exists, contains pre/post counts, per-layer summary, per-failure table, follow-ups section.
- Final commit pushed to origin.
- User has chosen PR / hold / discard.

## System-Wide Impact

- **Interaction graph:** All five layers touched in sequence. Foundation fixes ripple upward; adapter fixes ripple into CLI; webui fixes are contained to L5. Conftest fixtures (`_mock_publish_check_url`, `_mock_content_fetch`, `_disable_real_network`) are read-only here — we do not modify them. If a failure root cause traces to a conftest fixture itself, escalate as a separate scope-change conversation.
- **Error propagation:** Fixes preserve existing exception types and error-message contracts; downstream consumers (CLI report renderers, webui error pages) read these.
- **State lifecycle risks:** APScheduler / Flask-singleton order-pollution suspects are observation-only. If order-pollution sampling at any layer boundary flags a hit, log under report's Follow-ups; do not fix in this sweep.
- **API surface parity:** `src/backlink_publisher/cli/*` entrypoints are exposed via `[project.scripts]`. Any fix must preserve the public CLI argument shape; rename a flag and downstream `test-pipeline.sh` integration script breaks (and so do any external invocations the user has wired up).
- **Integration coverage:** `test_publish_verify_integration.py` is the explicit cross-layer test. We do not stub its way to green; if it requires real network, it stays ENV-BOUND.
- **Unchanged invariants:**
  - Conftest autouse fixture set is unchanged.
  - `docs/solutions/*` frontmatter is unchanged (enshrined per memory feedback).
  - `[project.scripts]` console-script entrypoints are unchanged.
  - `pyproject.toml [tool.pytest.ini_options] markers` set is unchanged unless a fix legitimately requires a new marker (record in report).
  - In-flight PRs (#36, #38, #39) surfaces remain mergeable post-sweep (no rename/move of files they touch). #37 already merged — its files are part of `main` and are normal sweep targets.

## Risks & Dependencies

| Risk | Mitigation |
|---|---|
| `pytest-timeout` missing → exit code 4 before any test runs, looks like config error | Unit 1 verifies presence; if missing, install before continuing or remove `@pytest.mark.timeout` markers as a fix prereq. |
| macOS Brave running → `MediumBraveAdapter` executes for real via AppleScript | Unit 1 `pgrep` check + user prompt to quit before run. |
| Conftest's string-match special case on `test_content_fetch.py` silently bypassed by rename | Unit 3 explicitly blocks structural rename; surface to user if a fix demands it. |
| Test-order pollution makes a fix look complete that isn't | Unit 5 includes isolated re-run sampling at L5; full-suite re-run at every layer boundary. |
| PR #37 already merged but plan treats as in-flight | Plan corrected: #37 is `d769bff` on main. Telegraph files are sweep targets when base = main. |
| PR #39 surface conflict with a fix | Worktree-isolated branch based on user-chosen base ref; final-step PR creation lets user merge or rebase #39 explicitly. |
| Worktree shares `.git/` so concurrent agent activity can still affect refs/hooks/packfiles | Unit 1 snapshots `main` HEAD and re-checks at each layer boundary; abort if it moves unexpectedly. |
| Strict bottom-up layer fix order is invalidated by L3→L4 back-edge (`anchor_resolver` → `adapters/llm_anchor_provider`) | Treat `adapters/llm_anchor_provider` as L3-shared; fix it in Unit 4. Unit 1 audits for any other back-edges. |
| Single root cause inflates failure count, breaks one-commit-per-fix | Cluster failures by traceback signature into `root_cause_group_id`; R7 reads "one commit per root cause". |
| Numeric `>30` threshold misses fundamental-refactor small-count cases | Second gate orthogonal to count: pause if any single traceback head implicates >1 cross-layer module. |
| Cited solutions-doc patterns may have rotted since 2026-05-13/14 | Unit 1 runs each cited doc's audit recipe before relying on it; records `freshness_check` in baseline. |
| Total failure count > 30 OR a single traceback spans >1 layer makes batch-mode autopilot inappropriate | Unit 1 hard-pauses on either condition and asks user before continuing. |
| Negative-shape test gets deleted to "make green" by accident | Test-modification gate is exception-based with named reasons (inverted-negative, shape-only, stale-docstring, contract-evolution); default behavior is "fix src, not test". |
| Local-only `c650d5e` (JsonStore) lives only on `refactor/webui-contract-tests`; could be lost if base ref is misunderstood | Plan's base ref decision (A/B/C) makes the choice explicit. If base=main, JsonStore is intentionally out of scope. Unit 1 records `base_ref_sha`. |
| Concurrent agent on `main` does shared `.git/` operations (fetch/gc/push) mid-sweep | Worktree isolates working-tree only; combined with Unit 1's `main` HEAD snapshot + re-check at each layer boundary, this catches most concurrent-agent races. |

## Documentation / Operational Notes

- Final report `docs/bug-sweep-2026-05-18.md` is the durable artifact, written directly from Unit 1 onward (no JSON intermediate).
- Intermediate `docs/bug-sweep-2026-05-18-baseline.xml` (pytest junit) is removed at end of Unit 3.
- Branch is pushed to origin at each layer boundary; PR creation is **opt-in by user** at Unit 3.
- No CHANGELOG entry needed (bug-sweep is a maintenance cycle, not a feature change).
- If a fix requires adding a dev dependency, record in `pyproject.toml [project.optional-dependencies] dev` and call it out in the report.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-18-pytest-bug-sweep-requirements.md](docs/brainstorms/2026-05-18-pytest-bug-sweep-requirements.md)
- **Institutional learnings:**
  - [docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md](docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md)
  - [docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md](docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md)
  - [docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md](docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md)
- **Key code references:**
  - `tests/conftest.py` (3 autouse fixtures)
  - `pyproject.toml` ([tool.pytest.ini_options], [project.optional-dependencies])
  - `src/backlink_publisher/` (all submodules in layer map)
  - `webui_app/` (JsonStore refactor, local-only `c650d5e`)
- **Related PRs:** #36, #37, #38, #39 (active against `main`)
- **MEMORY references:** `feedback_worktree_concurrent_switching`, `feedback_solutions_category_frontmatter`, `project_backlink_publisher_overview`
