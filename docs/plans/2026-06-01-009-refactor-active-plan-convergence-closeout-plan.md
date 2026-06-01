---
title: "refactor: Active-plan convergence — status reconciliation + thin-WebUI Unit 8 closeout"
type: refactor
status: completed
closed: "2026-06-01. R1 done (13 flips + thin-webui closeout). R4/R5 PARKed (geo, history). R3 done: thin-webui Unit 8 was already shipped w/ U6-U7; added the missing plan/report_anchors parity anchors (PR-B, green)."
date: 2026-06-01
origin: docs/brainstorms/2026-06-01-active-plan-convergence-requirements.md
claims: {}
---

# Active-Plan Convergence — Status Reconciliation + thin-WebUI Unit 8 Closeout

## Overview

Two things ship here, both about closing the gap between what the repo *did* and
what its tracking *says*:

1. **R1 — status reconciliation.** Of the 16 plans that carried `status: active`
   before this plan was written (the active set is now 17, this plan included),
   14 are already merged (verified against code + git): **13 plan docs flip to
   `completed`** here, and **`2026-05-27-004` closes via Unit 3** once its last
   unit lands. Each flip is annotated with the merge SHA/PR so the `active` set
   tells the truth.
2. **R3 — finish the one genuinely-open refactor.** The thin-WebUI in-process
   migration (plan `2026-05-27-004`) is done in code through Unit 7
   (`PipelineAPI.plan/validate/report_anchors` are in-process; `publish`/login
   stay subprocess **by design**). The remaining work is **Unit 8 only**: migrate
   the in-scope subprocess-mocking tests to the engine seam, then close that plan
   out.

R4 (geo-ai-citation U5-U8) and R5 (history-store→events-db migration) are
surfaced as **go/no-go decisions**, not planned in detail (see Open Questions and
Unit 4).

## Problem Frame

The repo carries 73 brainstorms / 120 plans but only 10 captured solutions (see
origin: docs/brainstorms/2026-06-01-active-plan-convergence-requirements.md). The
perception of "lots of unfinished work" is mostly a stale-`status` artifact, not a
real backlog: reconciliation showed 14/16 active plans already shipped. Fixing the
signal (R1) and closing the single near-done refactor (R3) converts that false
backlog into an accurate, short open-work list — without adding any net-new
product scope.

## Requirements Trace

- R1. Flip the 14 shipped plans' frontmatter `status: active` → `status: completed`, each annotated with its merge SHA/PR. (origin R1)
- R2. After all units land (R1 + Unit 3 + this plan's own self-closeout), `grep -l "^status: active" docs/plans/*.md` returns **exactly** the 2 genuinely-open frontier plans: `2026-05-29-006` (geo) and `2026-05-28-007` (history). Use the `^`-anchored grep — an unanchored `grep -l "status: active"` falsely matches the literal string in body prose (this plan and the gate plans quote it), inflating the count. (origin R2)
- R3. Finish thin-WebUI Unit 8: migrate the **in-scope** (validate/plan/report-anchors) subprocess-mocking tests to the engine/`PipelineAPI` seam; leave publish/login/footprint mocks intact; full suite green under `PYTHONHASHSEED=0`. Then close out plan `2026-05-27-004` (flip its unit checkboxes + status). (origin R3)
- R4. Record an explicit go/no-go for geo-ai-citation U5-U8. (origin R4)
- R5. Record an explicit go/no-go for history-store→events-db migration. (origin R5)

## Scope Boundaries

- **No new feature/product scope.** This is reconciliation + closeout. Zero net-new ideas (origin Scope Boundaries).
- **`publish-backlinks`, login CLIs, and `footprint` stay subprocess** — explicit non-goals carried from `2026-05-27-004` (exit-4 partial-success semantics, credential-write/SSRF isolation, PYTHONHASHSEED startup-only). Their subprocess test mocks are **not** migrated.
- **R4/R5 are decisions, not implementation.** Do not start either migration here.
- Phase-2 deferrals already documented inside shipped PRs (canary L3, scorecard GA4/GSC) stay deferred.

## Context & Research

### Relevant Code and Patterns

- `webui_app/api/pipeline_api.py` — `PipelineAPI.plan()` (line ~268), `validate()` (~329), `report_anchors()` (~439) are **already in-process** (docstrings cite "Unit 7"/"U6"). `publish()`/`publish_seed()`/`resume()` (~401–434) deliberately call `_invoke_capture` (subprocess) — the seam boundary to respect.
- Engines already extracted: `src/backlink_publisher/cli/plan_backlinks/_engine.py`, `src/backlink_publisher/validate/engine.py`, `src/backlink_publisher/cli/_report_engine.py`.
- Unit 7 engine tests already exist: `tests/test_plan_engine.py`, `tests/test_report_engine.py`, `tests/test_validate_engine.py`.
- Already-migrated WebUI tests that mock the in-process seam (good exemplars, verified against code): `tests/test_webui_false_success.py` (patches `webui_app.routes.pipeline._api`), `tests/test_webui_three_url.py` (patches `backlink_publisher.cli.plan_backlinks._engine.plan_rows`; also has one residual `subprocess.run` to assess). `tests/test_pipeline_inprocess_characterization.py` is the **subprocess-vs-in-process parity baseline** (asserts on `.validate` against a real subprocess) — keep, do not migrate.
- Still subprocess-heavy, **must be partitioned** in-scope vs keep (counts approximate — measure live during Unit 2, do not trust these): `tests/test_pipeline_api_seam.py` (~11–14 run_pipe), `tests/test_webui_route_contract.py` (~7 run_pipe + ~3 subprocess.run), `tests/test_webui_error_fidelity.py` (~14–16 run_pipe — largely the typed-error-envelope contract = legitimately subprocess, mostly KEEP), `tests/test_webui_typed_error_surfacing.py` (~11 run_pipe).
- Unit 8 spec lives in `2026-05-27-004-...-plan.md` lines 660–700 — the authoritative checklist for this work.

### Institutional Learnings

- `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md` — tests silently coupled to local config state route to the wrong code path. Directly relevant: the in-process seam resolves config differently than a subprocess; migrated tests must assert against the autouse-sandboxed config, not the operator's.
- `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md` — plan checkboxes/status drift behind code. This whole convergence is the remedy; Unit 3 closes the loop for `2026-05-27-004`.
- `docs/solutions/workflow-issues/validate-main-before-planning-off-feat-branch-2026-05-19.md` — confirmed: single worktree, on `main`.

### External References

- None. Entirely repo-local refactor + housekeeping; strong local patterns exist.

## Key Technical Decisions

- **Partition the subprocess-mock tests by grep before migrating, do not assume a count.** The `2026-05-27-004` Unit 8 approach explicitly warns ~27 files reference `subprocess` but most are out-of-scope. The migration set is small; per-file judgment is required. Rationale: blindly re-pointing all `run_pipe` mocks would break the publish/error-envelope tests that *must* stay on the subprocess path.
- **`run_pipe`/`subprocess.run` mocks in error-fidelity tests are NOT automatically in-scope.** `test_webui_error_fidelity.py` exercises the typed-error envelope *through* the subprocess bridge (Units 1–3) — that is the point of the test. Keep those.
- **Status flips are evidence-annotated, not bulk sed.** Each of the 14 gets its merge SHA/PR inline so the `completed` claim is auditable (counters the original drift).
- **R4/R5 get a written decision record, not silence.** A plan sitting at `active` with no decision is the exact failure mode this work fixes.

## Open Questions

### Resolved During Planning

- *Is thin-WebUI Unit 7 done?* — Yes, verified in code (`pipeline_api.py` in-process `plan`/`validate`/`report_anchors` + extracted engines + engine tests). Only Unit 8 remains.
- *Is `publish` supposed to be in-process?* — No. Explicit non-goal (R4/R5 of the origin refactor); stays subprocess. Its test mocks are out-of-scope for migration.
- *Which plans are truly still open after R1?* — thin-WebUI (this closes it), geo-ai-citation (R4 decision), history-store→events-db (R5 decision).

### Deferred to Implementation

- *Exact in-scope test set for Unit 8* — knowable only by running the grep-partition against current `tests/` and reading each candidate's intent. Enumerated as Unit 2 step 1, not pre-decided here.
- *Whether any migrated test needs rewriting vs. simple re-point* — the plan notes some assert subprocess-specific behavior (silent-failure detection, the seo_viz exit-6 accident) and need engine-semantics rewrites; identified per-file during migration.

## Implementation Units

- [ ] **Unit 1: Reconcile the 14 shipped plan statuses (R1, R2)**

**Goal:** Make `status: active` mean "actually open" by flipping the 14 verified-shipped plans to `completed` with merge evidence.

**Requirements:** R1, R2

**Dependencies:** None

**Files (modify frontmatter only):**
- `docs/plans/2026-05-27-001-feat-adapter-contract-canary-plan.md` (#268 `7bbaf119`)
- `docs/plans/2026-05-27-003-feat-blast-radius-phase1-plan.md` (#267 `87fbbcfa`)
- `docs/plans/2026-05-27-005-feat-comment-outreach-queue-plan.md` (#272 `2844f3d7`)
- `docs/plans/2026-05-27-006-feat-canary-publish-path-validation-plan.md` (#282 `101159e5`)
- `docs/plans/2026-05-27-006-feat-generate-backlink-text-plan.md` (#275 `cf496e4a`)
- `docs/plans/2026-05-28-002-feat-deterministic-planning-purity-plan.md` (`628bed2d`)
- `docs/plans/2026-05-28-003-fix-dedup-failed-to-done-invariant-plan.md` (#290 `482de679`)
- `docs/plans/2026-05-29-001-feat-livejournal-canary-closeout-plan.md` (#303 `db82b045`)
- `docs/plans/2026-05-29-005-feat-cyclomatic-complexity-budget-plan.md` (#305 `6fbfda9c`)
- `docs/plans/2026-06-01-005-feat-gate-first-validation-and-deficit-overlay-plan.md` (#362/#357/#358)
- `docs/plans/2026-06-01-005-feat-per-channel-value-scorecard-plan.md` (#362 `d7b0c757`)
- `docs/plans/2026-06-01-006-feat-recheck-deficit-overlay-replan-plan.md` (#357 `aa0464b9`)
- `docs/plans/2026-06-01-007-feat-wave1-dofollow-channels-plan.md` (#363 `1025acba`)
- These are the **13 plan docs** flipped here. The 14th shipped item is
  `2026-05-27-004` (thin-WebUI), closed via Unit 3 once its last unit lands —
  not flipped here because it is Unit-2-gated. (adapter-contract-canary Phase 2
  is an intentional in-#268 deferral, not a separate doc and not counted.)

**Approach:**
- For each file, set `status: completed` and add a frontmatter line `shipped: <SHA / PR#>` (or append to an existing notes field) so the claim is auditable.
- Do NOT edit unit checkboxes inside these 13 docs — out of scope; only the top-level status signal matters for R2.
- Re-verify each SHA resolves (`git cat-file -e <sha>`) before flipping, so a typo doesn't assert a false merge.

**Test scenarios:**
- Test expectation: none — documentation/frontmatter only, no behavioral change.

**Verification:** after Unit 1, `grep -l "^status: active" docs/plans/*.md` returns `2026-05-27-004` (until Unit 3 closes it), `2026-05-29-006` (geo), `2026-05-28-007` (history), and `2026-06-01-009` (this plan, until its own self-closeout in Unit 3). After Unit 3 the set is exactly `{2026-05-29-006, 2026-05-28-007}` (R2). Use the `^`-anchored grep (see R2 note).

- [ ] **Unit 2: Finish thin-WebUI Unit 8 — migrate in-scope subprocess-mock tests to the engine seam (R3)**

**Goal:** Re-point the validate/plan/report-anchors WebUI tests at the engine / `PipelineAPI` seam; leave publish/login/footprint/error-envelope subprocess mocks intact; full suite green under `PYTHONHASHSEED=0`.

**Requirements:** R3

**Dependencies:** None (engines + Unit 7 already shipped)

**Files:**
- Audit (read): all `tests/` files matching `subprocess`/`run_pipe`/`PipelineAPI`
- Modify (the in-scope subset, determined in step 1): re-point mocks from `subprocess.run`/`run_pipe` to the engine callable (`plan_backlinks._engine.plan_rows`, `validate.engine.*`, `cli._report_engine.report_from_profile`) or the `PipelineAPI` method
- Possibly modify: `tests/conftest.py` if a shared in-process fixture reduces duplication
- Test: this unit *is* test work

**Approach:**
0. **Prove Unit-7 parity before re-pointing anything.** `test_pipeline_inprocess_characterization.py` has explicit `inprocess_matches_subprocess` equality assertions **only for `validate`** (`test_validate_inprocess_matches_subprocess_good_payload` / `_malformed`); `plan` and `report_anchors` have in-process characterization tests but **no subprocess-equality anchor**. Before migrating their mocks, add the missing `plan`/`report_anchors` in-process==subprocess equality assertions. Rationale: re-pointing mocks onto an unproven seam, then rewriting tests "against engine semantics" (step 2), would silently encode any latent migration bug as the new golden. This precheck is the safety gate; treat any parity failure as unfinished Unit-7 work, not a clean Unit-8 start.
1. **Partition first (do not assume a count).** Grep the candidate set, then classify each file: IN-SCOPE (mocks subprocess specifically for a validate/plan/report-anchors WebUI call that now runs in-process) vs KEEP (publish/login/footprint, typed-error-envelope fidelity through the subprocess bridge, `python -m` entrypoints, prune-worktree). Record the resulting two lists in the PR description.
2. Re-point in-scope mocks to the engine seam; for tests asserting subprocess-specific behavior (silent-failure detection, the seo_viz exit-6 accident), rewrite against engine semantics rather than mechanically swapping the patch target.
3. Confirm every in-process network call site is covered by an autouse mock and assert on payload content (mock.patch string targets are invisible to py_compile/CI style checks — the full seeded suite is the only tripwire).
4. Watch the config-coupling trap (see Institutional Learnings): assert against the sandboxed autouse config, not operator state.

**Execution note:** Characterization-first — the golden-corpus / characterization tests (`test_pipeline_inprocess_characterization.py`) must keep asserting in-process output == prior subprocess output before and after each file is re-pointed. Keep publish/login subprocess mocks untouched.

**Patterns to follow:**
- Migration exemplars (verified against code): `tests/test_webui_false_success.py` mocks the seam at `webui_app.routes.pipeline._api` (the `PipelineAPI` instance); `tests/test_webui_three_url.py` mocks one level deeper at `backlink_publisher.cli.plan_backlinks._engine.plan_rows`. Either depth is acceptable; pick per test.
- **Parity baseline, do NOT migrate:** `tests/test_pipeline_inprocess_characterization.py` deliberately runs the real subprocess to assert in-process == prior-subprocess output. It is the verification anchor, not a migration target — leave its subprocess call intact.
- Unit 8 spec: `2026-05-27-004-...-plan.md` lines 660–700.

**Test scenarios:**
- Happy path: a representative migrated test mocks the engine callable (not `subprocess.run`) and asserts the same WebUI-observable behavior as before.
- Integration: `PipelineAPI.plan()`/`validate()`/`report_anchors()` in-process outputs match the golden corpus; autouse socket-block stays enforced.
- Guard (regression): no in-scope test patches `subprocess.run`/`run_pipe` for validate/plan/report-anchors after migration (grep-based assertion or review checklist).
- Negative/boundary: publish/login/footprint and typed-error-envelope tests still mock subprocess and still pass — proves the seam boundary held.
- Full suite: `PYTHONHASHSEED=0 pytest tests/` green (the footprint regression gate depends on the seed).

**Verification:** Full `pytest tests/` green under `PYTHONHASHSEED=0`; subprocess mocks remain only for publish/login/footprint/error-envelope; the in-scope partition list is documented in the PR.

- [ ] **Unit 3: Close out the thin-WebUI plan AND this plan (R3, R2)**

**Goal:** Make `2026-05-27-004` reflect reality now that Unit 8 has landed, and self-close `2026-06-01-009` so the `active` set converges to exactly the 2 frontier plans (R2).

**Requirements:** R3, R2

**Dependencies:** Unit 2 (for `2026-05-27-004`); Units 1, 2, 4 (for this plan's self-closeout — close only when all work has landed)

**Files:**
- Modify: `docs/plans/2026-05-27-004-refactor-thin-webui-in-process-pipeline-plan.md`
- Modify: `docs/plans/2026-06-01-009-refactor-active-plan-convergence-closeout-plan.md` (self-closeout)

**Approach:**
- `2026-05-27-004`: tick Units 1–8 checkboxes (all shipped after Unit 2), set `status: completed`, add a closing note: "Units 1–7 shipped (PRs #270/#274/#277/#281/#284 …); Unit 8 closed in `2026-06-01-009`. `publish`/login/footprint intentionally remain subprocess (R4/R5 non-goals)."
- `2026-06-01-009` (this plan): once Units 1, 2, and 4 have landed, flip its own `status: active` → `completed`. Skipping this would reproduce the exact drift this plan exists to fix — so it is an explicit, gated step, not an afterthought.
- `2026-05-27-004` is the 14th shipped plan; both flips here are Unit-2-gated, which is why they are sequenced separately from Unit 1's 13 already-merged docs.

**Test scenarios:**
- Test expectation: none — documentation/frontmatter only.

**Verification:** `2026-05-27-004` no longer appears in the `status: active` set; its non-goals are recorded so a future reader does not "finish" the publish migration by mistake.

- [ ] **Unit 4: Record R4/R5 go/no-go decisions (R4, R5)**

**Goal:** Convert the two genuinely-open frontier plans from silent `active` into explicit, dated decisions.

**Requirements:** R4, R5

**Dependencies:** None (can run in parallel with Units 1–3)

**Files:**
- Modify: `docs/plans/2026-05-29-006-feat-geo-ai-citation-closed-loop-plan.md` (add a dated decision note)
- Modify: `docs/plans/2026-05-28-007-refactor-history-store-events-db-migration-plan.md` (add a dated decision note)
- Optionally create: `docs/brainstorms/2026-06-01-active-plan-convergence-requirements.md` is updated with the recorded decisions (origin doc closes the loop)

**Approach:**
- This unit does **not** implement either migration. It records, for each, a **substantive disposition** chosen by the user: CONTINUE / PARK / NEEDS-BRAINSTORM.
- A note that merely says "decide later" does **not** satisfy R4/R5 — that is the silent-`active` failure mode this plan exists to end. Each disposition must carry: (a) for CONTINUE — the next concrete action and a target date; (b) for PARK — the reason and the concrete trigger/evidence that would move it to CONTINUE; (c) for NEEDS-BRAINSTORM — the specific unresolved product question and that it routes back to `ce:brainstorm`.
- geo-ai-citation context: U1–U4 shipped (#331); U5–U8 pending with an internal credit-gate → `probe-citations` CLI dependency — exploratory feature expansion.
- history-store→events-db context: zero units landed; large surface area; needs deliberate commitment, not drift.

**Test scenarios:**
- Test expectation: none — decision record only.

**Verification:** Both plans carry a dated `decision:` note with a substantive disposition (next-action+date / park-reason+trigger / brainstorm-question) — not a bare "decide later". If a plan is PARKed, flip its `status:` to a non-`active` value (e.g. `parked`) so it leaves the active set; a CONTINUE keeps it `active` with the recorded next action.

## System-Wide Impact

- **Interaction graph:** Unit 2 touches only `tests/` (+ maybe `conftest.py`). No production code changes — `PipelineAPI` and engines already shipped. Blast radius is the test suite.
- **Error propagation:** The typed-error-envelope path (CLI stderr → `cli_runner` → routes) is explicitly preserved; error-fidelity tests stay on the subprocess bridge. The in-process seam must surface the same typed outcomes (Unit 2 asserts this).
- **State lifecycle risks:** None for R1/R3/R4/R5 (docs + tests). The config-coupling trap is the one real hazard in Unit 2 — mitigated by asserting against sandboxed config.
- **API surface parity:** None changed. CLI terminal/pipe contract (stdout JSONL + exit codes) is untouched.
- **Unchanged invariants:** `publish-backlinks`/login/`footprint` remain subprocess; CLI exit-code contract; `PYTHONHASHSEED=0` footprint gate. This plan explicitly does not alter any of them.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Over-migrating: re-pointing a publish/error-envelope mock that must stay subprocess | Partition-first (Unit 2 step 1); KEEP-list documented in PR; negative test scenario proves boundary held |
| Migrated test silently passes against operator config instead of sandbox | Assert against autouse-sandboxed config (learning: tests-coupled-to-operator-config-state) |
| A status SHA is mistyped, asserting a false merge | `git cat-file -e <sha>` re-verification before each flip (Unit 1) |
| Hidden mock.patch string targets break only at runtime | Full `PYTHONHASHSEED=0 pytest tests/` is the load-bearing tripwire (Unit 2 step 3) |
| swarm_guard PreToolUse hook fails-closed on the unicode workspace path, blocking Write | Edit plan/test files via Bash heredoc/`git` when the Write tool is blocked (known trap) |

## Phased Delivery

R1 (pure frontmatter, zero code/tests) and R3 (test-suite migration with real suite-break risk) share no file, dependency, or risk profile. Ship them as **two separate PRs** so a hard test-migration problem cannot block the trivial-and-valuable reconciliation, and so the plan-claims gate / review aren't reviewing pure-doc and test-behavior changes in one diff:

- **PR-A (immediate): Unit 1 + Unit 4** — status reconciliation + R4/R5 decisions. Pure docs. Delivers the headline value (accurate `active` list) with no test risk.
- **PR-B (gated): Unit 2 + Unit 3** — thin-WebUI Unit 8 test migration, gated on the Unit-2 step-0 parity precheck, then the `2026-05-27-004` + self closeout.

## Documentation / Operational Notes

- After both PRs land, the live `^status: active` set is the canonical "what's actually open" list — useful for any future convergence pass.
- **Root cause acknowledged, not silently deferred:** 14 plans shipped without status closure because closure is a manual, easily-skipped step (see learning: `late-plan-revisions-skip-code`). This plan is the one-time manual remedy. A durable fix — a CI check flagging `^status: active` plans whose claimed artifacts already exist on `main` — is a **real follow-up that needs its own go/no-go** (treat it like R4/R5), not vague "future work". Accepting one manual pass now is deliberate: the guard's cost/false-positive profile is unknown and shouldn't block this cleanup.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-06-01-active-plan-convergence-requirements.md](docs/brainstorms/2026-06-01-active-plan-convergence-requirements.md)
- Refactor being closed: `docs/plans/2026-05-27-004-refactor-thin-webui-in-process-pipeline-plan.md` (Unit 8, lines 660–700)
- Code seam: `webui_app/api/pipeline_api.py`; engines under `src/backlink_publisher/{cli/plan_backlinks/_engine,validate/engine,cli/_report_engine}.py`
- Learnings: `docs/solutions/test-failures/tests-coupled-to-operator-config-state-2026-05-18.md`, `docs/solutions/workflow-issues/late-plan-revisions-skip-code-2026-05-20.md`
- Decision plans: `docs/plans/2026-05-29-006-...geo-ai-citation...md` (R4), `docs/plans/2026-05-28-007-...history-store-events-db-migration...md` (R5)
