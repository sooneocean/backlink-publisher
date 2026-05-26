---
title: "refactor: decompose validate_output_payload (N2, CC F(43)→low)"
type: refactor
status: active
date: 2026-05-26
claims:
  # Only paths that exist on origin/main — the drift gate verifies reachability.
  # The net-new test file (tests/test_schema_output_payload_characterization.py)
  # is intentionally NOT claimed (it does not exist on origin/main yet).
  paths:
    - src/backlink_publisher/schema.py
  shas: []
---

# refactor: decompose `validate_output_payload` (N2)

## Overview

`schema.py::validate_output_payload` has cyclomatic complexity **F(43)** — the
highest in the repo. It is a flat sequence of ~9 independent validation blocks
that each append to a shared `errors` list. Extract each block into a small
module-level `_check_*(row) -> list[str]` helper so the main function becomes a
flat concatenation. **Behaviour-preserving** structural refactor: identical
error messages, identical order, no logic change. Item **N2** from the
2026-05-26 code-quality stocktake.

## Problem Frame

The function is contract-critical (validates the planned output-payload JSONL
row that the whole publish pipeline depends on) and security-adjacent (the
`seo.canonical_url` regex is "the SOLE defense layer for forwarder adapters that
inject the value into HTML / YAML / GraphQL contexts without their own
escaping" — per the in-code comment + `tests/test_adapter_blogger_api_xss_contract.py`).
High CC on such a function is a maintainability and extensibility risk: adding a
10th validation rule means growing an already-43-branch function.

Decomposition makes each rule independently testable and readable, and drops the
per-function CC well below the radon C threshold without changing a single
observable behaviour.

## Requirements Trace

- R1. `validate_output_payload` cyclomatic complexity drops from F(43) to ≤ C
  (radon grade), achieved purely by extraction (no logic change).
- R2. Observable behaviour is byte-identical: same error message strings, same
  append order, same pass/fail verdicts for every input.
- R3. The under-tested validation blocks gain explicit characterization coverage
  *before* the extraction, so any behavioural drift is caught.
- R4. Full suite stays green; no collision with the active concurrent session.

## Scope Boundaries

- **Only** `validate_output_payload` and its extracted helpers. `validate_input_payload`
  (D(24), the sibling) is **out of scope** — a separate follow-up if desired.
- No change to the validation *rules*, error message wording, ordering, the
  `canonical_url` regex, or the module's public surface (`validate_output_payload`
  keeps its signature; `validate_publish_payload` keeps wrapping it).
- New `_check_*` helpers are private (leading underscore); not exported.
- Not touching `monolith_budget.toml` (schema.py is not a budgeted file).
- N3 (`projector` budget) is already planned separately
  (`2026-05-26-004-opt-projector-budget-rescue-plan.md`) — not this plan.

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/schema.py:268-375` — `validate_output_payload`, the
  target. The 9 blocks: required-field types, optional-field types,
  `OUTPUT_ONE_OF_GROUPS` at-least-one, `content_html` size cap, links structure,
  seo structure + canonical regex, link count 6–8, non-empty title/excerpt/slug,
  `_check_main_domain_presence` (already extracted — mirror this pattern).
- Module-level constants the helpers will read: `OUTPUT_REQUIRED_FIELDS` (line 72),
  `OUTPUT_ONE_OF_GROUPS` (93), `OUTPUT_OPTIONAL_FIELDS` (98), `LINK_KINDS` (104),
  `MAX_CONTENT_HTML_BYTES` (113), plus `_is_field_present` (116).
- **Pattern to mirror:** `_check_main_domain_presence(row) -> str | None` (line 158)
  is the existing in-file precedent for a private block-helper. New helpers return
  `list[str]` (most blocks emit 0–N errors) and the main fn does
  `errors.extend(_check_x(row))` in the original sequence.

### Institutional Learnings

- `[[feedback_multi_agent_turf_check]]` / `[[feedback_stash_message_as_concurrent_agent_handshake]]`
  — concurrent session active in the main worktree; do this from an isolated
  worktree off `origin/main`; never touch `stash@{0..2}` or parked branches.
- `[[feedback_pythonpath_src_for_sibling_worktree]]` — run pytest in the sibling
  worktree with `PYTHONPATH=src` (editable install binds the main worktree).
- `[[feedback_target_language_schema_and_dispatcher]]` — schema.py changes are
  high-blast-radius; preserve every dispatch/whitelist path. (This refactor adds
  none, but reinforces the byte-identical requirement.)

### Existing test coverage (measured)

- **Well-covered (safe):** `seo.canonical_url` regex has a dedicated contract file
  `tests/test_schema_seo_canonical_contract.py` (heavy). One-of groups,
  `content_html` size cap, and the `validate_publish_payload` wrapper are covered
  in `tests/test_schema_source_format.py`.
- **Thin / uncovered (characterization gap → Unit 1):** links structure (non-dict,
  missing url/anchor/kind/required, bad URL format, invalid kind), link count
  6–8 range, required-output-field missing/wrong-type, non-empty title/excerpt/slug,
  seo missing-field/wrong-type.

## Key Technical Decisions

- **Extract to module-level private functions, not nested closures** — mirrors
  `_check_main_domain_presence`, keeps each block independently unit-testable, and
  is what drops the radon CC (nested defs would not).
- **Helpers return `list[str]`; main fn concatenates in original order** — simplest
  shape that preserves message ordering (R2). The one existing `str | None` helper
  (`_check_main_domain_presence`) keeps its signature; the main fn keeps its current
  `if ... is not None: errors.append(...)` call for it.
- **Characterization-first** — add the missing-coverage tests and confirm them
  green against the *current* implementation before extracting, so the extraction
  has a behavioural net.

## Open Questions

### Resolved During Planning

- *Is the canonical XSS regex safe to move?* It stays inside the seo helper
  verbatim (same pattern string, same flags); the dedicated contract test file is
  the regression net. No regex change.
- *Does ordering matter?* Yes — some callers/tests may assert the full error list.
  Helpers are invoked in the exact current sequence; Unit 1 snapshots multi-error
  rows to lock ordering.

### Deferred to Implementation

- Exact helper names (e.g. `_check_output_required_fields` vs `_check_required_fields`)
  — pick at implementation time, keep them descriptive and `_`-prefixed.
- Whether `validate_input_payload` (D(24)) is worth the same treatment — assess
  after this lands; out of scope here.

## Implementation Units

- [ ] **Unit 1: Characterization tests for under-covered output-payload blocks**

**Goal:** Lock current behaviour of the thinly-covered blocks before refactoring.

**Requirements:** R3 (enables R2)

**Dependencies:** None.

**Files:**
- Create: `tests/test_schema_output_payload_characterization.py`

**Approach:**
- Build a matrix of malformed/edge rows, one per under-covered block, plus at
  least one multi-error row to lock append ordering. Assert on the **exact** error
  strings produced by the current implementation (and the full `errors` list for
  the ordering case).
- Start from a known-valid baseline row (6–8 links, valid seo, content present) and
  mutate one aspect per test.

**Execution note:** Characterization-first — write and run these against the
current (un-refactored) `validate_output_payload` and confirm green before Unit 2.

**Patterns to follow:**
- `tests/test_schema_source_format.py` (row-builder + `validate_output_payload(row)`
  assertion style); `tests/test_schema_seo_canonical_contract.py`.

**Test scenarios:**
- Happy path: a fully valid row → `errors == []`.
- Error path — required fields: missing a required field → `"missing required output field '<f>'"`; wrong type → `"field '<f>' must be <type>, got <actual>"`.
- Error path — links structure: `links[i]` not a dict → `"links[i] must be a dict"`; missing each of url/anchor/kind/required → `"links[i]: missing field '<req>'"`; non-`https?://` url → `"links[i]: invalid URL format: <url>"`; kind not in `LINK_KINDS` → `"links[i]: invalid kind '<k>'"`.
- Edge case — link count: 5 links and 9 links → `"link count <n> is not between 6 and 8"`; exactly 6 and 8 → no count error.
- Error path — non-empty text: whitespace-only `title`/`excerpt`/`slug` → respective `"<f> must not be empty"`.
- Error path — seo: missing `title`/`description`/`canonical_url` → `"seo: missing field '<f>'"`; non-string seo field → `"seo.<f> must be a string"`.
- Edge case — content_html size: a row whose `content_html` exceeds `MAX_CONTENT_HTML_BYTES` → the size-cap error (mirror existing below-cap test).
- Ordering: a row failing multiple blocks → assert the full `errors` list equals the current implementation's exact ordered output.

**Verification:**
- New test file passes against the current implementation (no source change yet).

- [ ] **Unit 2: Extract validation blocks into `_check_*` helpers**

**Goal:** Reduce `validate_output_payload` CC from F(43) to ≤ C via pure extraction.

**Requirements:** R1, R2, R4

**Dependencies:** Unit 1 (the behavioural net must be green first).

**Files:**
- Modify: `src/backlink_publisher/schema.py`

**Approach:**
- Extract each independent block into a module-level `_check_*(row) -> list[str]`
  helper (required-field types, optional-field types, one-of groups, content_html
  size, links structure, seo structure, link count, non-empty text fields). Keep
  `_check_main_domain_presence` as-is.
- Rewrite `validate_output_payload` to build `errors` by extending with each
  helper's result **in the current order**, preserving the existing
  `_check_main_domain_presence` `is not None` call shape at the end.
- Move no constants; helpers read the existing module-level ones.

**Patterns to follow:**
- `_check_main_domain_presence` (schema.py:158) — existing private block-helper.

**Test scenarios:**
- Regression: entire existing suite + Unit 1 characterization file pass unchanged
  (this is the behaviour-identity proof — no new behavioural tests needed for Unit 2
  beyond what Unit 1 added).
- Each new `_check_*` helper is exercised by the corresponding Unit 1 scenario
  (they now call through the helper).

**Verification:**
- `radon cc -s schema.py` shows `validate_output_payload` at grade ≤ C and each new
  helper at A/B.
- `py_compile` clean; full suite (incl. Unit 1) green with zero diffs in error output;
  `validate_publish_payload` wrapper behaviour unchanged.

## System-Wide Impact

- **API surface parity:** `validate_output_payload` signature and return contract
  unchanged; `validate_publish_payload` (its wrapper) inherits behaviour unchanged.
- **Error propagation:** error strings and ordering preserved verbatim — downstream
  consumers in `cli/validate_backlinks.py` and the publish path see no difference.
- **Unchanged invariants:** the `seo.canonical_url` XSS-defense regex, all message
  wording, the 6–8 link-count rule, and the whitespace-as-absent semantics
  (`_is_field_present`) are explicitly unchanged.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Refactor silently changes an error string or order | Unit 1 characterization (incl. a multi-error ordering snapshot) added and green *before* extraction; full existing suite is the backstop. |
| Touching the security-critical canonical regex | Regex moves verbatim into the seo helper; dedicated `test_schema_seo_canonical_contract.py` is the regression net; no pattern/flag change. |
| Conflict with parked `wip/deep-optimization` (+11 lines on schema.py) | That branch is shelved/uncertain (`[[project_main_worktree_dirty_deep_optimization_wip.md]]`); a future deep-opt rebase resolves the small overlap manually. Not a blocker. |
| Concurrent-session collision | Work from an isolated worktree off `origin/main` (`bp-schema-decompose`); schema.py is not in the main worktree's dirty set (verified). |

## Sources & References

- Origin: 2026-05-26 code-quality stocktake, item N2 (`[[project_codebase_quality_stocktake_2026_05_26]]`).
- Related code: `src/backlink_publisher/schema.py` (`validate_output_payload`,
  `_check_main_domain_presence`), `tests/test_schema_source_format.py`,
  `tests/test_schema_seo_canonical_contract.py`.
- Sibling not-in-scope: `validate_input_payload` D(24).
