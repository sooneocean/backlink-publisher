---
title: "feat: Document deterministic planning / non-deterministic execution principle"
type: feat
status: completed
shipped: 628bed2d
date: 2026-05-28
origin: docs/brainstorms/2026-05-28-deterministic-planning-purity-requirements.md
claims:
  paths:
    - docs/architecture/deterministic-planning-principle.md
    - AGENTS.md
  shas:
    - 886cc76138
---

# Plan: Document Deterministic Planning / Non-Deterministic Execution Principle

## Summary

Create a concise architecture principle document (`docs/architecture/deterministic-planning-principle.md`) codifying the boundary between deterministic planning steps (pure, testable, same-input-same-output) and non-deterministic execution (platform-dependent, side-effectful) in the backlink-publisher pipeline. Add a reference in `AGENTS.md` for discoverability.

## Requirements

Origin: `docs/brainstorms/2026-05-28-deterministic-planning-purity-requirements.md` (R1–R10).

The plan delivers all 10 requirements in a single document (R1–R7 define the document's content; R8–R9 cite existing precedent; R10 defines guidance principles).

## Implementation Units

### U1. Create architecture principle document

**Goal:** Write the architecture principle document at `docs/architecture/deterministic-planning-principle.md` that defines the deterministic/non-deterministic boundary.

**Requirements:** R1, R2, R3, R4, R5, R6, R7, R8, R9, R10

**Dependencies:** None

**Files:**
- Create: `docs/architecture/deterministic-planning-principle.md`

**Approach:**
- The document lives at `docs/architecture/` — a dedicated location for architecture decisions separate from plans and brainstorms (which are ephemeral). If the directory does not exist, create it.
- Structure follows the requirements doc's groupings:
  1. **Pipeline command boundary table** — deterministic/non-deterministic classification for each command with rationale
  2. **Non-deterministic dependency callouts** — content_fetch, LLM, image gen, linkcheck as external inputs
  3. **Exceptions and edge cases** — validate-backlinks' dual nature, read-only diagnostic commands
  4. **Existing precedent** — validate/engine.py purity contract as the model
  5. **Guidance for new development** — decision tree for where new functionality belongs
- Use the same tone and format as AGENTS.md (concise, table-heavy, reference-style). No tutorial prose.
- The document is **advisory** — it codifies the principle, not enforces it. Preface with a status indicator.

**Patterns to follow:**
- `validate/engine.py` docstring purity contract (the model for what "pure" means)
- AGENTS.md reference style (tables, brief bullets, no tutorial prose)
- `docs/architecture/` as a convention (create if absent)

**Test scenarios:** N/A — documentation only.

**Verification:** The document exists, covers all 10 requirements, and can be read in under 3 minutes.

---

### U2. Add discoverability reference in AGENTS.md

**Goal:** Add a brief reference to the new architecture document in AGENTS.md so it is discoverable from the project's canonical reference file.

**Requirements:** Implicit — the principle doc must be findable without knowing its path.

**Dependencies:** U1

**Files:**
- Modify: `AGENTS.md`

**Approach:**
- Add an entry in the "Known Quirks" section or the "Import Conventions" area referencing `docs/architecture/deterministic-planning-principle.md` with a one-line description ("Architecture principle: deterministic planning / non-deterministic execution boundary").
- No need for a new top-level section — a single line in an appropriate existing section.

**Test scenarios:** N/A — documentation only.

**Verification:** `grep -r "deterministic-planning-principle" AGENTS.md` finds the reference.

---

## Scope Boundaries

- Code refactoring to enforce the separation — this plan documents the principle only
- Adding purity tests or test fixtures for planning — deferred to a follow-up
- CI gates to enforce purity — deferred; principle must be established first
- Changes to existing code behavior or monolith budgets
- Any changes to `publish-backlinks` or its adapters

### Deferred to Follow-Up Work

- Adding purity test fixtures for planning modules that mock all non-deterministic dependencies
- CI or lint gate flagging new network-imports in engine modules

---

## Key Technical Decisions

1. **Document location:** `docs/architecture/` (not `ARCHITECTURE.md` at root). This keeps architecture documents grouped and avoids bloating the root README/AGENTS.md. If the directory doesn't exist, create it — it's the right semantic home for principle docs vs. transient plans/brainstorms.
2. **Single doc, not AGENTS.md expansion.** AGENTS.md is already 500+ lines of procedural/operational reference. Adding the full principle text would bloat it further. A brief pointer in AGENTS.md + the full document in `docs/architecture/` is the right split.
3. **Advisory tone.** The principle doc states what the boundary IS but does not enforce it through CI gates or test assertions. Enforcement is deferred to follow-up work after the principle has been adopted.
