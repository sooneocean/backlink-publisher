---
date: 2026-05-27
topic: bugfix-discipline
---

# Bugfix Discipline (AGENTS.md convention)

## Problem Frame

The principle "don't patch blindly — reproduce → root-cause → classify → smallest
safe fix → traceable evidence" already lives in the toolchain, and the *capture*
half is heavily exercised (≈55 committed `docs/solutions/` entries, ≈90 memory
feedback files — this is one of the project's most-used conventions). So the gap
is narrower than "nobody does this." Two real gaps remain. First, the discipline
is **opt-in and must-be-remembered**: `/investigate` has to be invoked, and capture
happens after the fact. Second, **"smallest safe fix" is expressed nowhere**, and
there is no single place that states, as a *fix-time contract*, what a bugfix in
this repo must do and carry.

This brainstorm targets the second gap (state the contract; name "smallest safe
fix"). Whether a prose convention can also close the *first* gap — opt-in /
enforcement — is an open decision, because a new AGENTS.md section is itself opt-in
and must-be-remembered (see Key Decisions and Outstanding Questions).

The fix has two parts, both in `backlink-publisher/AGENTS.md`: (1) an **addendum
to the existing "Lessons capture (dual-track)" section** that names the five steps,
defines "smallest safe fix," scales evidence to fix size, and points to existing
tools rather than re-explaining methodology; and (2) **one line added to the
existing PR checklist** so the contract surfaces at PR time, not only when someone
remembers to read the section. No new skill, no CI gate, no new file — the one
PR-checklist line is the single, deliberate forcing function.

## Requirements

**The convention**
- R1. Add the contract as an **addendum to the existing "Lessons capture
  (dual-track)" section** of `backlink-publisher/AGENTS.md` (not a new top-level
  section), stating the five-step contract: reproduce → identify root cause →
  classify failure type → apply the smallest safe fix → leave traceable evidence.
  Keep the restatement minimal — name the steps, don't re-teach the tools (see R6).
- R2. State the rule as applying to **all bugfixes**, with evidence depth scaling
  to the size of the fix (see R5 table). No fix is exempt from "reproduce + classify";
  only the *amount* of written evidence scales.
- R3. Define "smallest safe fix" concretely so it isn't aspirational: change only
  what the root cause requires; no opportunistic refactors, scope creep, or
  unrelated cleanups in a bugfix; prefer a fix that a reviewer can tie line-by-line
  to the identified cause.
- R7. Add **one line to the existing AGENTS.md PR checklist** referencing the
  addendum — the single forcing function so the contract appears at PR time. It
  must point to R1's addendum, not restate it (one source of truth).

**Classification**
- R4. Reuse the existing `docs/solutions/` taxonomy as the failure-type vocabulary
  (`test-failures`, `logic-errors`, `integration-issues`, `ui-bugs`,
  `workflow-issues`, `best-practices`, `developer-experience`). Do not invent a new
  classification scheme. Classifying a fix at fix-time is the same label it would
  carry if promoted via `/ce:compound`.

**Scaled evidence**
- R5. Specify the proportionality so depth is unambiguous:

  Scale on **fix size** (knowable at fix time), with one overlay trigger:

  | Fix size | Reproduce | Root cause | Classify | Evidence carried |
  |---|---|---|---|---|
  | One-liner / typo / rename / doc | One-line note of what was wrong (no test required) | One sentence | label | inline in commit/PR body |
  | Normal bug | Failing test or repro steps | Short paragraph | label | commit/PR body |

  **Overlay trigger (not a size tier):** if the bug is a *regression, recurring, or
  subtle* class — knowable only by judgment, not fix size — add a failing test to
  the suite, write a full paragraph on *why prior code allowed it*, and promote via
  `/ce:compound`. This rides on top of whichever size row applies.

  The floor (from R2) is "reproduce + classify, always" — for the one-liner row
  "reproduce" means a one-line note, not a test. Only the *written depth* scales;
  the obligation to understand the cause and label it does not. Note the
  self-classification risk: authors pick their own tier, so the floor (never exempt
  from reproduce + classify) is the load-bearing rule; the table is guidance, not a
  loophole.

**Connective tissue**
- R6. The section points to the existing tools as the *how*, keeping itself the
  *contract*: `/investigate` for the reproduce + root-cause phases; `/ce:compound`
  for promoting evidence into `docs/solutions/`. It must not duplicate or restate
  their internal methodology. **Caveat:** `/investigate` is a generic Claude Code
  skill, not a project command — reference it as an optional aid ("e.g. via
  `/investigate`"), and keep the five-step contract self-contained in prose so it
  stands even where that skill isn't installed. `/ce:compound` is project-canonical
  and safe to name as the promotion path.

## Success Criteria
- A reader of the addendum can, for any given fix, state in one read what they
  owe: a repro, a root cause, a `docs/solutions/` label, and an evidence depth.
- **Outcome-anchored:** a sampled set of bugfix PRs landed *after* this ships shows
  the repro note + `docs/solutions/` label present in the PR body. This is the
  observable test that the PR-checklist line (R7) actually changed behavior, not
  just that the doc reads well.
- The addendum adds no contradiction with existing `AGENTS.md` "Lessons capture"
  content — it extends that section, doesn't fork it.
- "Smallest safe fix" is expressed somewhere in the repo for the first time.

## Scope Boundaries
- No new skill, CLI verb, new file, or CI/test gate. The only enforcement is **one
  line in the existing PR checklist** (R7) — not a new PR template.
- No new top-level AGENTS.md section: the contract is an addendum to "Lessons
  capture (dual-track)" (R1).
- Does not modify `/investigate` or `/ce:compound`; only references them.
- Does not change the `docs/solutions/` categories or promotion flow.
- Not a replacement for the memory feedback files — those remain the private track.

## Key Decisions
- **Form = addendum to "Lessons capture" + one PR-checklist line**: a standalone
  section in a 476-line AGENTS.md would dilute attention and create a third
  drift-prone lessons surface; folding in keeps one continuous capture story. The
  single PR-checklist line is the cheapest forcing function that answers the
  reviewers' P0 — a prose convention nobody is prompted to read would inherit the
  exact opt-in failure mode it set out to fix.
- **Trigger = all fixes, scaled depth**: a blanket "full dossier every time" rule
  gets ignored on typos and loses credibility; scaling keeps it universal but honest.
- **Scale on fix size, treat regression/recurring as an overlay**: recurrence isn't
  knowable at fix time, so it can't be a fix-size tier; it rides on top as a
  promotion trigger. The R2 floor (never exempt from reproduce + classify) is
  load-bearing against self-classification gaming.
- **Reuse `docs/solutions/` taxonomy**: one classification vocabulary, fix-time
  label == promotion-time label, zero new concepts.
- **Reference, don't re-explain `/investigate` + `/ce:compound`**: avoids drift
  between the convention and the tools it leans on (`/investigate` referenced as an
  optional aid only — it's a generic skill, not a project command).

## Dependencies / Assumptions
- Assumes `docs/solutions/` categories and `/ce:compound` promotion remain the
  canonical lessons track (per current `AGENTS.md` "Lessons capture (dual-track)").

## Outstanding Questions

### Deferred to Planning
- [Affects R7][Technical] Locate the existing PR checklist in `AGENTS.md` and word
  the single line to point at the R1 addendum (verify the checklist exists; if it
  doesn't, the forcing-function decision needs revisiting).
- [Affects R5][Technical] Final wording of the scaling table once it sits next to
  the surrounding AGENTS.md voice (terse, contributor-facing).

## Next Steps
→ /ce:plan for structured implementation planning (small — one AGENTS.md addendum
  + one PR-checklist line).
