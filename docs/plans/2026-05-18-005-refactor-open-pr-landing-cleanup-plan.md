---
title: Open PR Landing & Conflict Cleanup — #40 → #34 → #35
type: refactor
status: completed
date: 2026-05-18
---

# Open PR Landing & Conflict Cleanup — #40 → #34 → #35

> **Post-execution note (2026-05-18 04:10):** During execution, parallel agents landed everything in scope plus more. Final outcome — 0 open PRs. Sequence that actually shipped:
>
> | PR | Disposition | SHA on main |
> |---|---|---|
> | #40 | merged externally before this plan's first step | `c336167` |
> | #41 | merged externally (Unit 2 = JsonStore, split from PR #42) | `e0affc0` |
> | #43 | merged externally (env-var overrides + session-scope test isolation) | `fa1bc0f` |
> | #44 | merged externally (conftest real_content_fetch marker) | `2729931` |
> | #34 | merged externally after this session pushed `a14fbf7` (empty CI nudge) to its branch | `b1a16fb` |
> | #45 | merged externally (Unit 3+4 webui split, split from PR #42) | `a52eacc` |
> | #35 | merged externally | `f1110f2` |
> | #46 | merged externally (tests-coupled-to-config solutions entry) | `872c783` |
> | #42 | **closed** externally as superseded by #41 + #45 (content-duplicate on main with different SHAs) | — |
>
> This session's concrete contributions: (a) wrote this plan, (b) reset `fix/content-fetch-stream-to-head` worktree to drop a redundant cross-session commit `034ab54` (event-substrate-plan; content already on main as `edc8205`), (c) pushed empty commit `a14fbf7` to that branch to trigger CI, (d) authored the disposition question that led to PR #42 being closed (the parallel agent independently arrived at the same conclusion ~15s earlier).
>
> The plan's value was strategic, not operational: it identified that PR #42 had a real conflict, identified the right disposition (close-not-merge once Unit 2 + Unit 3+4 landed via #41 + #45), and identified that PR #34 had never run CI. Final main HEAD: `872c783`.

## Overview

Three PRs are open on `main` at session start (2026-05-18, after #33/#36/#37/#38/#39 squash-merged earlier today). User goal: "尽可能把 PR 都 merge 掉，让冲突状况消失。" All three are reported `MERGEABLE` by GitHub; the work is to sequence merges so that GitHub's optimistic mergeability holds in practice, plus keep the multi-worktree local environment in sync so post-merge state matches origin.

The plan is small, well-bounded, and bounded by GitHub's existing CI/merge mechanics. No feature behavior changes — this is a landing-sequence refactor of the PR queue.

## Problem Frame

### What's open

```
main (fcaeb41)
 ├─ PR #40  fix/pytest-bug-sweep-2026-05-18      base=main      MERGEABLE/UNSTABLE (CI running)
 ├─ PR #34  fix/content-fetch-stream-to-head     base=main      MERGEABLE/CLEAN
 └─ PR #35  fix/work-themed-link-count           base=#34's branch  MERGEABLE/CLEAN  (stacked)
```

### Risk surfaces

1. **PR #40 has a far merge-base** (`93fcd4a`, before #33/#36/#37/#38/#39 landed). Its diff-vs-main shows -5800 lines / 43 files "deleted", which is alarming at first glance. Investigation: those are files added on `main` after PR #40 branched, and `git merge-tree` returns exit 0 (clean) — git's three-way merge correctly keeps them. The three event-substrate docs PR #40 contains as duplicates of main's copies are md5-identical (`d93e7c7b…` and `c365676b…`). So GitHub's CLEAN is correct, but the visual signal is misleading enough to warrant explicit verification before clicking merge.

2. **PR #34 + PR #35 are a stack**. Once #34 squash-merges to main, GitHub will (a) usually auto-retarget #35 to main and (b) detect that #35's first three commits (`ffe68cb0`, `c0cfd90d`, `88ce280b`) carry identical content to #34's squashed commit and drop them — but only if the diff truly matches. The five unique #35 commits should then apply cleanly. Need to confirm post-retarget mergeability, not assume.

3. **CI must be green for #40 before merging**. `mergeStateStatus: UNSTABLE` reflects in-progress checks (`test (3.11)` + `test (3.12)`). Project pattern (per all merged today: #33/#36/#37/#38/#39) is squash-merge after CI passes.

4. **Local worktrees are out of sync**. `git worktree list` shows 9 worktrees, several on branches that will become stale after these merges (e.g., `bp-bugsweep-001` at `081b38d` is behind PR #40's tip `633bf8b` by 2 commits; the main repo's working tree is on `refactor/webui-contract-tests` — PR #39's already-merged head). Not blocking the merges, but the project has a recurring incident pattern (`feedback_worktree_concurrent_switching.md`) of external processes erasing untracked work on stale worktrees. Post-merge sync hygiene reduces this risk.

5. **One untracked file in the main checkout**: `docs/brainstorms/2026-05-18-ko-language-and-html-input-requirements.md`. Not a merge concern; surface it so it isn't accidentally lost during worktree sync.

### What "conflict situation disappears" means here

- All three open PRs land on main (or are explicitly closed with rationale).
- No stale base refs remain pointing at deleted branches.
- Local worktrees that should track main reflect post-merge main.
- Memory index `MEMORY.md` is updated so the stale "PR #36/#38/#39 active" entry doesn't cause future planning errors.

## Requirements Trace

- **R1.** PR #40 lands on `main` with CI green on Python 3.11 + 3.12 (test-only change, low risk).
- **R2.** PR #34 lands on `main` with CI green (content_fetch root-fix + two docs commits).
- **R3.** PR #35 lands on `main` after #34, with base retargeted and CI green.
- **R4.** Post-merge `origin/main` has zero open PRs (or only PRs the user explicitly defers, with reasons recorded).
- **R5.** Local worktrees that exist to track upstream branches are fast-forwarded; worktrees holding intentionally-local work (Telegraph Phase 0 rehearsal, store unit2, pr39-codex-fix) are left untouched but documented.
- **R6.** `~/.claude/projects/.../memory/MEMORY.md` is updated to remove the stale "PR #36/#38/#39 active" reference and add the new post-merge SHA + open-PR count.

## Scope Boundaries

**In scope**
- Merging the three currently-open PRs in dependency order.
- Local worktree fast-forward sync after merges complete.
- Memory index update for SHA/branch facts.

**Out of scope (explicit non-goals)**
- Modifying PR contents (rebasing #34 or #35 to clean up history, splitting #34's bundled docs+code, editing PR bodies).
- Resolving Telegraph Phase 0 routines (`reference_phase0_remote_routines.md`) or the 6/8 verdict — those are time-bound by remote-trigger schedule, not by PR landing.
- Touching the `bp-local-unit2/4/5/6` rehearsal worktrees, `bp-store`, `bp-pr39`, or `bp-phase1-rescue` — those are intentional local-only branches per `reference_phase0_local_rehearsal_branches.md`.
- Branch deletion on origin (GitHub's "Delete branch" button) — leave for the user to click; out of scope so we don't lose the local refs unintentionally.
- Investigating CI failures on PR #40 if they appear — that becomes a new planning input, not part of this plan.

## Context & Research

### Relevant code and patterns

- **Squash-merge convention**: All five PRs merged earlier today (#33, #36, #37, #38, #39) are squash-merge commits on main (single squashed commit, headline ends in `(#NN)`). Follow this pattern.
- **Stack handling precedent**: `docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md` already executed a "#9 → #10 stacked" landing sequence. Same shape as #34 → #35. That plan's Unit 2 specifically handled the `git rebase --onto main` + base-swap path; mirror it.
- **AGENTS.md (root)**: lessons-capture dual-track policy, set by PR #33. Nothing in this plan touches `docs/solutions/`, so the grep gates don't apply.
- **`gh pr merge --squash`**: the working tool for the actual merge — no `--admin` needed since required checks pass.

### Institutional learnings (relevant entries)

From `docs/solutions/`:
- `docs/solutions/best-practices/` carries several entries about CI dev-dep traps (the hypothesis / Flask family). Not directly triggered here because all three PRs touch existing tested code paths, but worth recalling if PR #40's CI fails on an unexpected import.

From operator-private memory (`MEMORY.md`):
- **`feedback_worktree_concurrent_switching.md`** — external processes can switch branches and erase untracked changes. The main repo currently has an untracked brainstorm file; preserve it before any worktree operation. Use isolated worktree for any merge-side rebase work; if untracked files appear stashed under `stash^3`, recover from there.
- **`feedback_verify_external_commits_before_push.md`** — before pushing, grep memory for non-session commits. This plan does not push — it merges via `gh` — but the principle applies if a rebase commit is created.
- **`feedback_verify_repo_state_before_planning.md`** — already applied in Phase 0: SHAs/branches/PR status all re-queried from git, not copied from memory.
- **`feedback_solutions_category_frontmatter.md`** — not triggered; this plan touches no `docs/solutions/` frontmatter.

### Sources & references

- Origin document: none — direct user request, no `ce:brainstorm` for this work. The planning bootstrap (Phase 0.4) was implicit: scope, success criteria, and assumptions are all in this document.
- Prior precedent: [docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md](docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md) — same shape, executed and `status: completed`.
- Related PRs: [#34](https://github.com/redredchen01/backlink-publisher/pull/34), [#35](https://github.com/redredchen01/backlink-publisher/pull/35), [#40](https://github.com/redredchen01/backlink-publisher/pull/40).
- Today's already-merged context: #33 / #36 / #37 / #38 / #39.

## Key Technical Decisions

- **Merge order: #40 → #34 → #35.** Rationale: #40 is independent and lowest-risk (test-only, doc-only); landing it first removes one item from the queue and gives a clean "first win" signal. #34 is also independent of #40 but is the base of #35, so it must precede #35. Could also do #34 → #40 → #35 — choose #40 first because its CI is already running, so by the time we look it may already be green.

- **Squash merge, not merge commit.** Reason: matches today's project convention. Operationally: cleaner main history, single revertable commit per PR.

- **Wait for CI before each merge.** Even though #34 and #35 are CLEAN, project CI runs `test (3.11)` + `test (3.12)`. PR #34 currently shows empty `statusCheckRollup` — that's likely because checks haven't been re-triggered after a rebase; force a re-run by closing/reopening or by pushing a no-op if checks don't appear within a reasonable window. (Operational nuance, deferred to execution.)

- **Don't rebase PRs locally and force-push.** Reason: PR authors are tools/agents that may have working state on those branches. Let GitHub's merge engine handle the integration; only retarget #35's base via the GitHub API after #34 lands.

- **Memory update is in this plan, not deferred.** Reason: `MEMORY.md`'s stale "PR #36/#38/#39 active" line caused the Phase 0 verification to look wrong at the start of this session. Fixing it removes a recurring source of false context.

- **Don't touch `bp-store`, `bp-pr39`, `bp-phase1-rescue`, `bp-local-unit*` worktrees.** Reason: per `reference_phase0_local_rehearsal_branches.md`, these are intentional local rehearsals. The user explicitly does not want them merged or deleted right now.

## Open Questions

### Resolved during planning

- **Q: Is PR #40's "-5800 lines, 43 files deleted" diff a real problem?**
  A: No. `git merge-tree` returns clean; the "deleted" files are added on main after PR #40's merge-base, and three-way merge keeps them.
- **Q: Will PR #35 still merge cleanly after #34 lands?**
  A: Expected yes. Its first three commits are identical content to #34's three commits (`ffe68cb0` / `c0cfd90d` / `88ce280b`). GitHub's squash collapses these. Verified by Open Question deferred check below.
- **Q: What sequence number for the plan?**
  A: `005` — `001` through `004` are taken today (across main and unmerged branches).
- **Q: Does the untracked `2026-05-18-ko-language-and-html-input-requirements.md` need addressing?**
  A: Not in this plan. It's a separate concern (a future brainstorm) that should not block PR landing. Surface it; defer.

### Deferred to implementation

- **CI re-trigger mechanism for PR #34** if `statusCheckRollup` stays empty: try pushing an empty commit on the branch, or close/reopen the PR. Don't pre-commit to a method — observe at execution time.
- **Whether GitHub auto-retargets #35 to main on #34 merge** or whether we need to call `gh pr edit 35 --base main` explicitly. Observable post-#34-merge.
- **Whether #35's commits actually de-duplicate cleanly** post-retarget, or if a manual rebase is needed. If a rebase is needed, do it in an isolated worktree per `feedback_worktree_concurrent_switching.md`.
- **Whether CI on PR #40 passes**. If it fails, that opens a separate investigation track outside this plan's scope.

## Implementation Units

- [ ] **Unit 1: Land PR #40 (pytest bug sweep)**

**Goal:** Squash-merge PR #40 to `main` after CI passes.

**Requirements:** R1, R4

**Dependencies:** none

**Files:** none modified by this plan; merge target is `main`. PR #40 itself touches:
- `tests/test_plan_backlinks.py` (1 test, +11 / -4)
- `docs/bug-sweep-2026-05-18.md` (new report)
- `docs/plans/2026-05-18-003-fix-pytest-bug-sweep-plan.md` (frontmatter status update)
- `docs/brainstorms/2026-05-18-pytest-bug-sweep-requirements.md` (brainstorm)

**Approach:**
- Wait for `mergeStateStatus` to transition from `UNSTABLE` → `CLEAN`. UNSTABLE here = CI in progress, not failing.
- Verify both CI jobs (`test (3.11)`, `test (3.12)`) conclude `SUCCESS`.
- Squash-merge via `gh pr merge 40 --squash --delete-branch=false`. Keep the branch ref so `bp-bugsweep-001` worktree doesn't break.
- Confirm `main` HEAD advances and the squashed commit message ends in `(#40)`.

**Patterns to follow:** Same merge mechanic as PRs #33/#36/#37/#38/#39 earlier today.

**Test scenarios:**
- Happy path: CI green → `gh pr merge 40 --squash` returns 0 → `gh pr view 40 --json state` returns `MERGED` → `git fetch origin main && git log -1 origin/main` shows new squashed commit with `(#40)` suffix.
- Error path: CI fails → do NOT merge → record failure mode (which test, which Python) and surface to user as new planning input.
- Edge case: CI completes but `mergeStateStatus` flips to `BEHIND` because main moved during the wait → re-evaluate; may need to push merge-main-into-branch or rebase, owned by execution-time discovery.

**Verification:**
- `gh pr view 40 --json state,mergedAt` reports `MERGED` with today's timestamp.
- `origin/main` HEAD shows the squashed commit; full test suite on main is green at next CI run.

---

- [ ] **Unit 2: Land PR #34 (content_fetch stream-to-head)**

**Goal:** Squash-merge PR #34 to `main`. Independent of #40 but ordered after for queue clarity.

**Requirements:** R2, R4

**Dependencies:** Unit 1 (queue order only — not a technical dependency).

**Files:** PR #34 touches:
- `src/backlink_publisher/content_fetch.py` — root-fix for `body_too_large`
- `tests/test_content_fetch.py` — +1 net test
- `docs/solutions/best-practices/stream-to-needed-tag-not-cap-then-reject-2026-05-15.md` — new
- `docs/brainstorms/2026-05-15-publish-idempotency-requirements.md` — pivoted
- `docs/ideation/2026-05-15-open-ideation.md` — Round 4

**Approach:**
- Confirm `mergeStateStatus: CLEAN` still holds after #40 lands (main moved, so re-check).
- Confirm CI status. Currently `statusCheckRollup: []`. May need to nudge a re-run (push empty commit, or close+reopen). Decide method at execution time based on observed behavior.
- Squash-merge via `gh pr merge 34 --squash --delete-branch=false`. Keep branch ref because PR #35 still points to it as base — premature deletion would orphan #35.

**Patterns to follow:** Same as Unit 1.

**Test scenarios:**
- Happy path: CI green → squash-merge succeeds → main advances → `gh pr view 34 --json state` reports `MERGED`.
- Edge case: `mergeStateStatus` becomes `BEHIND` after #40 lands → re-fetch and recompute; expected still mergeable because #40 doesn't touch `content_fetch.py`.
- Error path: CI fails on content_fetch tests after rebase → surface to user; don't force-merge.

**Verification:**
- `gh pr view 34 --json state` reports `MERGED`.
- `tests/test_content_fetch.py` runs green on main's next CI.

---

- [ ] **Unit 3: Land PR #35 (work-themed link-count + RECON)**

**Goal:** Retarget PR #35 to `main` (if not auto-retargeted) and squash-merge.

**Requirements:** R3, R4

**Dependencies:** Unit 2 must complete first (#35's current base is #34's head branch).

**Files:** PR #35 unique commits (after de-duping #34's 3 commits) touch:
- `src/backlink_publisher/cli/plan_backlinks.py` — work-themed and zh-short link-count padding + RECON event
- `src/backlink_publisher/language_check.py` — strip URLs/HTML before EN_HINTS scoring
- `src/backlink_publisher/schema.py` — possibly; verify at merge time
- `tests/` — multiple test files
- `docs/plans/2026-05-15-003-fix-work-themed-link-count-6-8-plan.md` — plan companion

**Approach:**
- After Unit 2 lands, observe `gh pr view 35 --json baseRefName,mergeStateStatus,mergeable`. Three possible states:
  1. **Auto-retargeted to main, CLEAN**: directly squash-merge.
  2. **Still pointing at `fix/content-fetch-stream-to-head`** (branch ref intact): run `gh pr edit 35 --base main` to retarget. GitHub recomputes mergeability.
  3. **Retargeted but DIRTY**: requires rebase. Do the rebase in an isolated worktree (per `feedback_worktree_concurrent_switching.md`), preserving any untracked files via `stash --include-untracked` first. Force-push to the PR branch. This is the only branch in scope where a force-push is acceptable, because the PR author is local and the branch is single-use.
- Once CLEAN: `gh pr merge 35 --squash --delete-branch=false`.

**Execution note:** This unit may need an isolated worktree if a rebase is required. Avoid touching the current main checkout during the rebase.

**Patterns to follow:** Mirror Unit 2 of `docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md` — that plan handled the analogous "stacked PR retarget after base merges" step.

**Test scenarios:**
- Happy path A (auto-retarget): GitHub flips `baseRefName` to `main`, dedup detects #34's commits, only 5 unique commits remain in the diff, CLEAN → squash-merge.
- Happy path B (manual retarget): `gh pr edit 35 --base main` → GitHub recomputes → CLEAN → squash-merge.
- Edge case (rebase needed): worktree-isolated rebase resolves any drift → force-push → CI re-runs → merge.
- Error path: rebase produces real conflicts (e.g., `plan_backlinks.py` was changed on main between #34's content and now) → surface to user with conflict file list; do not auto-resolve.

**Verification:**
- `gh pr view 35 --json state` reports `MERGED`.
- `gh pr list --state open --base main` returns zero open PRs (or only PRs the user explicitly defers).
- `tests/test_plan_backlinks.py` + `tests/test_language_check.py` green on main's next CI.

---

- [ ] **Unit 4: Local worktree sync + memory refresh**

**Goal:** Fast-forward worktrees that should track upstream branches; update operator-private memory to remove stale facts.

**Requirements:** R5, R6

**Dependencies:** Units 1–3 complete.

**Files:**
- `~/.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-backlink--publisher/memory/MEMORY.md` — update the Project entry
- `~/.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-backlink--publisher/memory/project_backlink_publisher_overview.md` — refresh SHA + open-PR snapshot

**Approach:**
- Worktree triage (run `git worktree list` first to confirm current state):
  - **Main checkout** (currently on `refactor/webui-contract-tests` = PR #39's head, already merged): switch to `main` and fast-forward. Stash the untracked `2026-05-18-ko-language-and-html-input-requirements.md` first using `git stash push -u -m "ko-lang brainstorm pre-fwd"`, then pop after the switch. Do NOT delete `refactor/webui-contract-tests` locally — leave that decision to the user.
  - **`/private/tmp/bp-main-plan`** (at `main` `fcaeb41`): plain `git pull --ff-only`.
  - **`bp-bugsweep-001`** (at `fix/pytest-bug-sweep-2026-05-18` `081b38d`): after #40 lands, this branch is dead-merged. Leave the worktree intact (memory references it) but note staleness.
  - **`bp-store`, `bp-pr39`, `bp-local-unit2/4/5/6`, `bp-phase1-rescue`**: leave untouched per `reference_phase0_local_rehearsal_branches.md`.
- Memory refresh:
  - Update `MEMORY.md`'s Project line: drop "PR #36/#38/#39", drop "main HEAD 2b1656f" (stale), record new main HEAD (post-#35 merge) and "0 open PRs" (or list any remaining).
  - Update `project_backlink_publisher_overview.md` body to reflect new SHA, the four-day landing wave (PR #33/#36/#37/#38/#39 + #34/#35/#40), and clear the "Telegraph Phase 0 进行中" status if 6/8 verdict has changed (verify at execution time).

**Patterns to follow:** The MEMORY.md format and frontmatter rules in `~/.claude/CLAUDE.md`. Keep `MEMORY.md` entries under ~150 chars.

**Test scenarios:**
- Happy path: All sync operations clean (`--ff-only` succeeds, no stash conflicts), memory files re-read after edit show correct new SHAs.
- Edge case: Untracked brainstorm file is recovered via stash pop without conflict.
- Error path: Stash pop conflicts → do NOT delete the stash; surface to user. Memory edit is idempotent and safe to retry.

**Verification:**
- `git worktree list` shows main checkout at the new post-#35-merge SHA.
- `cat ~/.claude/projects/.../memory/MEMORY.md | grep -E "PR #(34|35|36|38|39|40)"` shows no stale "active" markers for already-merged PRs.
- Untracked `ko-language` brainstorm file still present in the main checkout.

## System-Wide Impact

- **Interaction graph**: Each merged PR runs CI hooks (`test (3.11)`, `test (3.12)`); no deploy hooks fire (this repo doesn't auto-deploy on merge per visible workflows). PR #40 also lands the `docs/bug-sweep-*.md` report which is referenced by `docs/plans/2026-05-18-003-...` — frontmatter linkage stays intact.
- **Error propagation**: A failed CI on any PR halts that unit's merge; the next unit can still proceed because the PRs are functionally independent (only ordering matters for #34 → #35).
- **State lifecycle risks**: The `fix/content-fetch-stream-to-head` branch is intentionally kept alive (Unit 2's `--delete-branch=false`) until #35 lands. If a third party deletes it on origin between Unit 2 and Unit 3, GitHub's behavior is to auto-retarget #35 to main — acceptable, just observe.
- **API surface parity**: None — this plan changes no public API.
- **Integration coverage**: CI's existing test matrix covers all three PRs' changes. No additional coverage required by this plan.
- **Unchanged invariants**: `AGENTS.md` policy, `docs/solutions/` taxonomy, Telegraph Phase 0 routines (separate timer-driven schedule), and the Phase 0 local-rehearsal branches all remain untouched.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| PR #40's CI fails on Python 3.11 or 3.12 | Halt Unit 1; produce a separate investigation. Plan continues with #34 → #35 if user agrees the failure is unrelated. |
| PR #34 still shows empty `statusCheckRollup` and CI doesn't auto-run | Push an empty commit on the branch or close+reopen the PR to nudge a fresh CI run. Decide method at execution time. |
| PR #35 needs a rebase after #34 lands | Use isolated worktree for the rebase per `feedback_worktree_concurrent_switching.md`. Force-push is acceptable on this branch only (single-author, single-use). |
| Main checkout's untracked `ko-language` brainstorm is lost during worktree switch | Stash with `--include-untracked` before switching; verify recovery after. |
| Memory file edit drops a fact that's still useful | Diff `MEMORY.md` before commit; preserve the four Feedback entries (worktree-switching, solutions-frontmatter, verify-external-commits, verify-repo-state). |
| Another agent runs `git pull` / branch switch in the main worktree mid-merge | `feedback_worktree_concurrent_switching.md` — already a known pattern. If detected, recover from `stash^3` and retry. |

## Documentation / Operational Notes

- After all merges, update `MEMORY.md` and `project_backlink_publisher_overview.md` (Unit 4). Do not write a separate solutions entry — this is a routine landing wave, not a new lesson.
- If PR #40's CI surfaces a new failure class, capture it as a `feedback_*.md` entry per the dual-track policy, then promote later if recurring.
- No deploy notes; no rollback drill required (squash-merge is `git revert <sha>` away).

## Sources & References

- Origin document: none — direct user request.
- Prior precedent: [docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md](docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md)
- PRs in scope: [#34](https://github.com/redredchen01/backlink-publisher/pull/34), [#35](https://github.com/redredchen01/backlink-publisher/pull/35), [#40](https://github.com/redredchen01/backlink-publisher/pull/40)
- Recently landed (today's context): [#33](https://github.com/redredchen01/backlink-publisher/pull/33), [#36](https://github.com/redredchen01/backlink-publisher/pull/36), [#37](https://github.com/redredchen01/backlink-publisher/pull/37), [#38](https://github.com/redredchen01/backlink-publisher/pull/38), [#39](https://github.com/redredchen01/backlink-publisher/pull/39)
- Project memory: `~/.claude/projects/-Users-dex-YDEX-INPORTANT-WORK----0511-backlink--publisher/memory/MEMORY.md`
