---
title: "feat: Bugfix discipline convention in AGENTS.md"
type: feat
status: completed
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-bugfix-discipline-requirements.md
claims: {}
---

# feat: Bugfix discipline convention in AGENTS.md

## Overview

Codify the debugging discipline — *reproduce → root-cause → classify → smallest
safe fix → traceable evidence* — as a prose convention in
`backlink-publisher/AGENTS.md`. Two edits, both documentation-only:

1. An **addendum to the existing "## Lessons capture (dual-track)" section** stating
   the five-step contract, defining "smallest safe fix" (expressed nowhere in the
   repo today), and giving a fix-size scaling table.
2. A **new minimal "Before opening a PR" subsection** holding a one-line pointer to
   that addendum — the single forcing function so the contract surfaces at PR time,
   not only when someone remembers to read it.

No code, no new skill, no CI gate, no new file.

## Problem Frame

The discipline already lives in the toolchain, and its *capture* half is heavily
exercised (~55 `docs/solutions/` entries, ~90 memory feedback files). Two real gaps
remain (see origin: `docs/brainstorms/2026-05-27-bugfix-discipline-requirements.md`):

- The discipline is **opt-in and must-be-remembered** — `/investigate` has to be
  invoked, and capture happens after the fact.
- **"Smallest safe fix" is stated nowhere**, and there is no single *fix-time
  contract* naming what a bugfix must do and carry.

The plan states the contract (gap 2) and adds one PR-time forcing function to
partially close the opt-in problem (gap 1).

## Requirements Trace

- R1. Add the five-step contract as an **addendum within "## Lessons capture
  (dual-track)"** (not a new top-level section). Minimal restatement — name the
  steps, don't re-teach the tools. *(origin R1)*
- R2. State it applies to **all bugfixes**, evidence depth scaling to fix size; no
  fix exempt from "reproduce + classify". *(origin R2)*
- R3. Define "smallest safe fix" concretely: change only what the root cause
  requires; no opportunistic refactors/scope-creep/unrelated cleanups; a reviewer
  can tie the fix line-by-line to the cause. *(origin R3)*
- R4. Reuse the existing `docs/solutions/` taxonomy as the failure-type vocabulary;
  fix-time label == `/ce:compound` promotion-time label. *(origin R4)*
- R5. Include the fix-size scaling table; treat regression/recurring as an overlay
  trigger, not a size tier. *(origin R5)*
- R6. Reference `/investigate` (optional aid — generic skill, not a project command)
  and `/ce:compound` (canonical promotion path); don't duplicate their methodology;
  keep the contract self-contained in prose. *(origin R6)*
- R7. Add **one line in a new minimal "Before opening a PR" checklist subsection**
  that points to the R1 addendum (one source of truth) so the contract appears at
  PR time. *(origin R7, revised — see Key Technical Decisions)*

Success criteria (origin): a reader can state what they owe for any fix; no
contradiction with existing "Lessons capture" content; "smallest safe fix" expressed
for the first time; **outcome check** — sampled bugfix PRs landed after this ships
show the repro note + `docs/solutions/` label in the PR body.

## Scope Boundaries

- No new skill, CLI verb, new file, or CI/test gate. Only enforcement is the one
  PR-checklist pointer line (R7).
- The *contract* is an addendum, not a new top-level section. The "Before opening a
  PR" subsection is a new `###` heading (general PR guidance genuinely does not exist
  yet), kept under "## Lessons capture (dual-track)" so no new top-level section is
  introduced.
- Does not modify `/investigate` or `/ce:compound`; only references them.
- Does not change `docs/solutions/` categories or the promotion flow.
- Does not touch the adapter-specific "### PR checklist" at AGENTS.md §379.

## Context & Research

### Relevant Code and Patterns

- `backlink-publisher/AGENTS.md` §146–155 — "## Lessons capture (dual-track)"; the
  dual-track prose and the canonical `docs/solutions/` category list
  (`best-practices/`, `developer-experience/`, `integration-issues/`, `logic-errors/`,
  `test-failures/`, `ui-bugs/`, `workflow-issues/`) plus `/ce:compound` promotion. R1
  addendum and R4 vocabulary attach here.
- `backlink-publisher/AGENTS.md` §379–390 — the **adapter-specific** "### PR
  checklist" under "## Adding a new publisher adapter". Confirms there is no general
  PR checklist; R7's new subsection must NOT be merged into this one.
- `backlink-publisher/AGENTS.md` §157 — "## Plan-doc claims contract" marks the
  natural lower boundary for the Lessons-capture addendum insertion.

### Institutional Learnings

- `[[feedback_grep_before_writing_brainstorm_plan_claims]]` — grep before asserting
  repo structure in a plan. Applied: verified no general PR checklist / template /
  CONTRIBUTING exists, which revised R7's "existing checklist" premise.
- `[[feedback_plan_doc_on_cutoff_needs_claims_block]]` — plans dated ≥ 2026-05-20
  need a `claims:` block or explicit `claims: {}`; this plan uses `claims: {}`
  (doc-only, no SHA reachability to assert). Run `plan-check` to confirm exit 0.

### External References

- None. Documentation-convention change, fully grounded in repo context.

## Key Technical Decisions

- **R7 home revised — new "### Before opening a PR" subsection, not the existing PR
  checklist.** The only `### PR checklist` (§379) is the adapter-extension recipe;
  adding a bugfix line there would be semantically wrong. A new minimal general
  subsection is the honest PR-time surface. *(user decision during planning)*
- **Both surfaces, one source of truth.** Full contract in the Lessons-capture
  addendum; the PR subsection carries only a pointer line. Avoids a drift-prone
  duplicate. *(user decision)*
- **Keep the new subsection under "## Lessons capture (dual-track)"** rather than a
  new top-level `##`, honoring the origin scope boundary "no new top-level section."
- **Scale on fix size; regression/recurring is an overlay trigger.** Recurrence isn't
  knowable at fix time, so it can't be a size tier. The R2 floor (never exempt from
  reproduce + classify) is the load-bearing rule against self-classification gaming.
- **`/investigate` referenced as an optional aid only** — it is a generic Claude Code
  skill, not a project command; the prose contract must stand without it.

## Open Questions

### Resolved During Planning

- *Where does the forcing-function line live, given no general PR checklist exists?*
  → New "### Before opening a PR" subsection with a pointer to the addendum (user).
- *Does a general PR checklist / template / CONTRIBUTING exist to extend?* → No
  (verified via grep + `.github/` + repo root). R7 premise revised accordingly.
- *Claims block needed?* → Yes (dated past cutoff); `claims: {}` opt-out, doc-only.

### Deferred to Implementation

- Exact final wording / column widths of the R5 scaling table once it sits in
  AGENTS.md's terse contributor voice — adjust to match surrounding tone.
- Precise insertion offset within §146–155 (after the "Next curation review" line,
  before "## Plan-doc claims contract") — confirm at edit time against current
  line numbers, since concurrent agents may shift them.

## Implementation Units

- [ ] **Unit 1: Bugfix-discipline addendum in "Lessons capture (dual-track)"**

**Goal:** Add the fix-time contract (five steps + "smallest safe fix" definition +
scaling table) as an addendum within the existing Lessons-capture section.

**Requirements:** R1, R2, R3, R4, R5, R6

**Dependencies:** None.

**Files:**
- Modify: `backlink-publisher/AGENTS.md` (append a `### Bugfix discipline` block
  inside "## Lessons capture (dual-track)", after the "Next curation review" line
  at ~§155 and before "## Plan-doc claims contract" at §157)

**Approach:**
- Add a `### Bugfix discipline` subsection. Content, in order:
  1. One sentence: the five-step contract (reproduce → root cause → classify →
     smallest safe fix → traceable evidence), applies to all bugfixes, depth scales.
  2. The "smallest safe fix" definition (R3) — the only genuinely net-new idea.
  3. The fix-size scaling table (R5) with the regression/recurring overlay note and
     the "floor = always reproduce + classify" line.
  4. A pointer sentence: classify using the `docs/solutions/` categories already
     listed in this section (R4); `/investigate` is an optional aid for the
     reproduce/root-cause phases; promote recurring lessons via `/ce:compound`.
- Keep it tight — do not restate `/investigate`'s or `/ce:compound`'s internals (R6).

**Patterns to follow:**
- Match the terse, bulleted voice of the surrounding Lessons-capture prose
  (§146–155) and the bold-lead-in style (e.g., **Promotion = rewriting…**).
- Reuse the exact category list already at §151 — do not re-enumerate divergently.

**Test scenarios:**
- Test expectation: none — documentation-only convention, no code path. No AGENTS.md
  content is asserted by the test suite (footprint/monolith gates cover `src/` only).

**Verification:**
- "## Lessons capture (dual-track)" now contains a `### Bugfix discipline` block with
  the five steps, the "smallest safe fix" definition, and the scaling table.
- The `docs/solutions/` category list is referenced, not re-spelled with drift.
- `/investigate` appears only as an optional aid; no methodology is duplicated.
- AGENTS.md still renders cleanly (headings nest correctly; table is well-formed).

- [ ] **Unit 2: "Before opening a PR" pointer subsection**

**Goal:** Add the single PR-time forcing function — a one-line pointer to the Unit 1
addendum.

**Requirements:** R7

**Dependencies:** Unit 1 (the pointer must reference an existing anchor).

**Files:**
- Modify: `backlink-publisher/AGENTS.md` (add a `### Before opening a PR` subsection
  immediately after the Unit 1 `### Bugfix discipline` block, still within
  "## Lessons capture (dual-track)")

**Approach:**
- Add a `### Before opening a PR` subsection with a single checkbox-style line, e.g.
  "For any bugfix, carry repro + root cause + a `docs/solutions/` label + smallest-
  safe-fix rationale (see **Bugfix discipline** above)." Pointer only — no restated
  contract (single source of truth).
- Do NOT touch or merge into the adapter "### PR checklist" at §379.

**Patterns to follow:**
- Checkbox-line style of the adapter "### PR checklist" (§381–388) for visual
  consistency, but kept general and pointing upward to the addendum.

**Test scenarios:**
- Test expectation: none — documentation-only, no code path.

**Verification:**
- A general `### Before opening a PR` subsection exists, distinct from the adapter
  checklist at §379, containing one line that points to the Bugfix discipline
  addendum and restates no contract content.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Concurrent agents shift AGENTS.md line numbers before edit | Anchor edits to heading text ("## Lessons capture (dual-track)", "## Plan-doc claims contract"), not line numbers; re-confirm offsets at edit time. |
| Pointer line and addendum drift apart over time | Pointer carries no contract content — it only references the addendum, so there is nothing to drift. |
| Convention is read once then ignored (origin P0) | Accepted and bounded: the PR-time pointer is the one forcing function; outcome success criterion (sampled PRs) makes the failure observable rather than silent. |
| `### Before opening a PR` under a "Lessons capture" `##` reads as loosely placed | Acceptable tradeoff to honor "no new top-level section"; revisit only if a general contributor-checklist section is later created. |

## Documentation / Operational Notes

- This *is* a documentation change; no separate docs to update.
- `bp-*/` worktrees carry stale AGENTS.md copies by design — edit only the canonical
  `backlink-publisher/AGENTS.md` (per workspace CLAUDE.md). No propagation needed.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-27-bugfix-discipline-requirements.md](docs/brainstorms/2026-05-27-bugfix-discipline-requirements.md)
- Anchor sections: `backlink-publisher/AGENTS.md` §146 (Lessons capture), §157
  (Plan-doc claims contract), §379 (adapter PR checklist — do not touch)
- Tools referenced: `/ce:compound` (promotion), `/investigate` (optional aid)
