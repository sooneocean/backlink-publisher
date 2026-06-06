---
date: 2026-06-01
topic: active-plan-convergence
---

# Active-Plan Convergence Audit

## Problem Frame

The repo carries 73 brainstorms / 120 plans but only 10 captured solutions. A
request for "comprehensive optimization scan + ideas" would produce brainstorm
#74 — a near-duplicate of `2026-05-28-comprehensive-optimization-*` from four
days prior. The real bottleneck is execution and **status hygiene**, not idea
supply.

Reconciling the 16 `status: active` plans against actual code + git history
shows **14 are already shipped** (merged, artifacts on disk) — the `status`
field simply was never flipped. The "active" count is a false-backlog signal
that manufactures the perception of unfinished work. Only **2.5 plans** are
genuinely open.

## Findings

**Shipped but still marked `active` (14) — close these (status -> completed):**

| Plan | Evidence |
|---|---|
| 2026-05-27-001 adapter-contract-canary | #268 7bbaf119 — canary/ pkg + CLI verb |
| 2026-05-27-003 blast-radius-phase1 | #267 87fbbcfa — cull + cells parser + gate |
| 2026-05-27-005 comment-outreach-queue | #272 2844f3d7 — all 8 units |
| 2026-05-27-006 canary-publish-path-validation | #282 101159e5 — target-specific verdicts |
| 2026-05-27-006 generate-backlink-text | #275 cf496e4a — CLI + http_guard, 5 units |
| 2026-05-28-002 deterministic-planning-purity | 628bed2d — arch doc + AGENTS.md ref |
| 2026-05-28-003 dedup-failed-to-done-invariant | #290 482de679 — guard narrow + 4 tests |
| 2026-05-29-001 livejournal-canary-closeout | #303 db82b045 — register flip + retire docs |
| 2026-05-29-005 cyclomatic-complexity-budget | #305 6fbfda9c — budget.toml + gate test |
| 2026-06-01-005 gate-first-validation | #362/#357/#358 — G2/G3/G5 + gate-probe |
| 2026-06-01-005 per-channel-scorecard | #362 d7b0c757 — scorecard/ + CLI (Wave-0 MVP) |
| 2026-06-01-006 recheck-deficit-overlay | #357 aa0464b9 — recheck-overlay verb |
| 2026-06-01-007 wave1-dofollow-channels | #363 1025acba — 3 adapters (canary-pending) |

(14th: adapter-contract-canary Phase 2 is an intentional deferral inside #268, not open work.)

**Genuinely open (3):**

| Plan | Real progress | Blocker |
|---|---|---|
| 2026-05-27-004 thin-webui in-process | ~95% — Phase 1 + Units 4-6 shipped; Units 7-8 (subprocess-mock test migration) remain | none |
| 2026-05-29-006 geo-ai-citation closed loop | U1-U4 shipped (#331); U5-U8 pending | internal: credit-gate -> probe-citations CLI sequencing |
| 2026-05-28-007 history-store -> events-db migration | zero units landed; code still calls _history_store.update() | none, but no momentum; large refactor |

## Requirements

**Status reconciliation (housekeeping)**
- R1. Flip the 14 shipped plans' frontmatter status: active -> status: completed, each annotated with its merge SHA/PR for traceability. Mechanical, ~5 min.
- R2. After R1, the live status: active set must contain only the 3 genuinely-open plans, so the count reflects reality.

**Close the near-done frontier**
- R3. Finish thin-webui in-process (05-27-004) Units 7-8: complete the read-only CLI in-process migration and migrate the subprocess-mock tests. Highest-value zero-blocker ship — closes a long refactor and removes subprocess-mock tech debt.

**Decide the two remaining frontiers (do not auto-start)**
- R4. geo-ai-citation U5-U8: confirm whether to continue (exploratory feature expansion with an internal credit-gate -> CLI dependency) before planning the next unit.
- R5. history-store -> events-db migration: explicit go/no-go decision. Zero progress + large surface area means it needs a deliberate commitment, not drift.

## Success Criteria
- grep -l "status: active" docs/plans/*.md returns exactly the 3 open plans (not 16).
- thin-webui refactor fully landed (no subprocess-mock tests for read-only CLIs) OR explicitly parked with a written reason.
- geo and history-migration each have an explicit continue / park decision recorded, instead of sitting silently "active".

## Scope Boundaries
- No new feature brainstorm. This audit deliberately produces zero net-new product ideas — that would worsen the 73:10 idea-to-shipped imbalance.
- Do not auto-start R4/R5; they are decisions, not tasks.
- Phase-2 deferrals already documented inside shipped PRs (canary L3, scorecard GA4/GSC) stay deferred — not reopened here.

## Key Decisions
- Treat stale status as the root cause of the "too much unfinished work" perception, not a true backlog. Fix the signal (R1) before adding anything.
- Highest-leverage code move is R3 (finish the 95%-done refactor), not greenfield.

## Next Steps
-> /ce:plan for R1+R3 (status reconciliation + thin-webui Unit 7-8 closeout), then surface R4/R5 as explicit go/no-go decisions.

## Outcome (2026-06-01)
- Plan: `docs/plans/2026-06-01-009-refactor-active-plan-convergence-closeout-plan.md` (PR-A = Unit 1+4 docs; PR-B = Unit 2+3 test migration).
- R1 DONE: 13 shipped plans flipped active -> completed (SHA-annotated). `^status: active` set now = {2026-05-27-004 (closes PR-B), 2026-06-01-009 (self-closes PR-B)}.
- R4 DECISION: geo-ai-citation (2026-05-29-006) -> PARK. Trigger: backlink/AI-citation corpus volume becomes non-trivial.
- R5 DECISION: history-store->events-db (2026-05-28-007) -> PARK. Trigger: cross-process state-divergence incident or proven history_store bottleneck.
- R3 PENDING: PR-B (thin-webui Unit 8 test migration, gated on the Unit-2 parity precheck).
