---
title: "feat: Harden publish pipeline saga contracts (runbook + G3/G4/G7 + tests)"
type: feat
status: complete
date: 2026-05-28
deepened: 2026-05-28
merged: 2026-05-28
merge_commit: "189d9010"
origin: docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md
---

# feat: Harden Publish Pipeline Saga Contracts

## Overview

The publish pipeline's success/failure boundaries, retry policies, and compensating
actions are currently implicit. The brainstorm produced a formal contract specification
(`docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md`) identifying eight
gaps (G1–G8). This plan closes three high-value gaps with minimal risk (G3, G4, G7),
adds a runbook for operators, and adds contract tests that make the documented invariants
machine-checkable.

**Scope and current status** (all of U1–U5 MERGED in **PR #291** → main on 2026-05-28T07:59 via squash-commit `189d9010`; original branch commits `16c09a93` + `28e9434c`; 4 files +597 lines; preceded by PR #290 `482de679` which enforces `failed → done` ratchet — see Risks):

- **[SHIPPED]** U1: Operator runbook — `docs/runbooks/2026-05-28-publish-saga.md` (220 lines on saga branch; path differs from this plan's original `docs/runbook-publish-saga.md`)
- **[SHIPPED]** U2: G4 — `skipped_canary` field emitted in `dedup_reconciliation` RECON
- **[SHIPPED]** U3: G3 — `checkpoint_disabled: true` emitted in `publish_reconciliation` RECON when checkpoint creation fails
- **[SHIPPED, contract richer than plan]** U4: G7 — `publish_reconciliation` RECON emits `dropped.unverified` (count) and `dropped_ids.unverified` (id list); **exit code 5** fires when any unverified row exists. Surface is the actual implemented contract; original plan body has been reconciled accordingly during the 2026-05-28 deepening pass.
- **[SHIPPED]** U5: Contract tests — `tests/test_saga_contracts.py` (368 lines, 14 tests) machine-check exit codes, RECON fields, and dedup-state contracts

**Out of scope (tracked as deferred gaps in origin doc):**
- G1: LLM error classification
- G2: Mixed-success exit code for validate-backlinks
- G5: 5xx retry in enforce-mode
- G6: `compensate()` adapter method
- G8: SIGKILL lease release

*(Origin brainstorm has stale claims about G7 — tracked once under Documentation / Operational Notes below; not repeated here or in Risks.)*

## Problem Frame

The gaps being closed are "silent lie" bugs: the system reports success or counts rows
incorrectly, making operator diagnosis harder than it needs to be.

- **G4**: Canary-skipped rows vanish from all counts. Operator cannot reconcile total input
  rows against success + failed + skipped.
- **G3**: Checkpoint failure silently kills resume capability. Operator doesn't know until
  a second `--resume` invocation fails with "run not found."
- **G7**: Post-publish verification failure marks the row as `done` in the dedup store, but
  the `verify_ok=False` result is silently discarded from the RECON output. Operator cannot
  distinguish "published and verified" from "published but links not confirmed" without
  querying the dedup store directly. The `verify_ok` column already exists in the store;
  the gap is that RECON never surfaces it.

(see origin: `docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md` §R5)

## Requirements Trace

- R1 (from origin): Each saga step has observable success/failure conditions
- R5.G3: Epilogue emits `checkpoint_disabled: true` when checkpoint creation failed
- R5.G4: Epilogue `dedup_reconciliation` RECON includes `skipped_canary` counter
- R5.G7: Epilogue emits `dropped.unverified` (count) and `dropped_ids.unverified` (id list) in the `publish_reconciliation` RECON; exit code 5 fires on any unverified row (no new dedup state created)
- Success criterion: operator reading the runbook can identify what compensation occurred
  and what manual action is required for any failure mode

## Scope Boundaries

- `unverified` rows are treated as non-re-dispatchable (terminal). Operators must manually
  confirm and transition them.
- `--force` re-dispatch of `unverified` rows is explicitly out of scope for this plan.
- No changes to exit codes (deferred to G2 planning).
- No changes to `validate-backlinks` or any step outside `publish-backlinks` and the dedup store.
- `phase0-seal` and `footprint` contracts are documentation-only (already read-only steps).
- **Accepted permanent-state cost** *(surfaced by 2026-05-28 deepening adversarial pass)*:
  a row can land in `done + verify_ok=False` (status suffix `_unverified`) after a
  transient verification flake. Once there, resume skips it (`done` is terminal); `--force`
  is out of scope; no in-saga remediation path exists. The only ways out are (a) the
  recheck service updating `verify_ok` (workflow not described in this plan), or (b)
  out-of-band manual edit of the dedup-store SQLite file. PR #290 (`482de679`, merged
  immediately before #291) enables `failed → done` transitions, so a row can also reach
  the `done + _unverified` permanent state via failed-then-recovered paths. This is a
  deliberately accepted cost of the "saga exits 5; no auto-retry of verification" contract.

## Context & Research

### Relevant Code and Patterns

- **Epilogue function**: `src/backlink_publisher/cli/_publish_helpers.py:623–720`
  (`_publish_epilogue`) — called once at run end, emits two RECON lines via
  `publish_logger.recon()`. All new epilogue fields go here, not inline in `main()`.
- **RECON logger**: `src/backlink_publisher/_util/logger.py:141–153` — `recon()` writes
  `{"level": "RECON", ...}` bypassing log-level filtering.
- **Dedup store state**: `src/backlink_publisher/idempotency/store.py:68,72`
  — `State = Literal["attempting", "done", "failed", "uncertain"]` and
  `_TERMINAL = frozenset({"done", "failed"})`. `uncertain` is a hold state, not terminal.
- **State transitions**: `store.py:507–565` (`transition()`) and `store.py:216–259`
  (`_record_terminal()`).
- **Checkpoint failure**: `src/backlink_publisher/cli/publish_backlinks.py:141–156`
  — silent except branch sets `run_id = None`; no RECON field emitted.
- **Canary gate**: `src/backlink_publisher/cli/_publish_helpers.py:130–189` (`_canary_gate()`).
  `skipped_quarantined_count` already accumulated at `publish_backlinks.py:205`.
- **Dedup gate record_done path**: `src/backlink_publisher/cli/_dedup_gate.py` — surfaces
  the verify_ok signal to the dedup store.
- **Existing tests**: `tests/test_publish_backlinks.py` — `_run_publish()` helper at file
  top, `test_checkpoint_create_failure_degrades_gracefully` (line 458), 
  `test_publish_quarantined_hard_skip_is_skipped_not_failed` (line 506).
- **Dedup store tests**: `tests/test_idempotency_store.py`.

### Institutional Learnings

- **Two-seam rule** (`docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`):
  Any per-row output field has two dispatch paths — fresh (`base.py::to_publish_output`)
  and resume (`_resume.py::item_to_publish_output`). Fixing one seam without the other
  creates a silent bug on resume. **G7's `unverified` state must be verified on both
  paths.**
- **Epilogue extraction** (`docs/solutions/best-practices/extract-cli-epilogue-block-2026-05-26.md`):
  Exit codes 4 and 5 are dispatched inside `_publish_epilogue`, not at `main()` level.
  New epilogue fields go in `_publish_epilogue`, not re-inlined into `main()`.
- **Exit code convention**: `UsageError` exits 1; `argparse choices=` exits 2. No
  closed-set CLI parameters should use `choices=`.

## Key Technical Decisions

- **G7 surfaces via status-suffix on the per-row output, not a new dedup state, and not a row-attribute `verify_ok` lookup**: The actual implemented signal is the string suffix `_unverified` appended to `outputs[*].status` at three write sites (see System-Wide Impact). The dedup store also records `verify_ok` via `record_done(..., verify_ok=...)` at `publish_backlinks.py:391`, but the epilogue aggregation reads the output-row status suffix — not a dedup-store query and not a row attribute. Adding `unverified` as a fourth dedup state was the initial plan, but the architecture review found this would conflict with `transition()`'s hardcoded allowlist at `store.py:531` and `gate_and_claim()`'s string-literal branching. The minimal correct fix already in place is to count rows whose `status.endswith("_unverified")` and emit the count + id list in `publish_reconciliation` RECON. This surfaces the distinction operators need without any state machine change.
- **G4: `skipped_canary` already a named parameter in `_publish_epilogue`**: Research confirms
  `skipped_quarantined_count=0` is already the 8th parameter of `_publish_epilogue`. U2
  only needs to add the field to the `dedup_reconciliation` RECON dict — no signature change.
- **`skipped_canary` goes in `dedup_reconciliation` RECON, not `publish_reconciliation`**:
  Canary-skipped rows never reach the adapter dispatch stage, so they are a pre-dispatch
  routing decision — logically grouped with `skipped_already_published` and `held_uncertain`
  in `dedup_reconciliation`, not with the adapter success/failure counts.
- **`checkpoint_disabled` goes in `publish_reconciliation` RECON**: It's a run-level
  property (not per-row), and `publish_reconciliation` is the run-outcome line. Adding it to
  `dedup_reconciliation` would conflate row-routing metadata with run-setup metadata.
- **`_publish_epilogue` signature extension over a new kwargs dict**: Extend the function
  signature with a named `checkpoint_disabled: bool = False` parameter. Using `**kwargs`
  would obscure the contract; adding a named parameter keeps type-checking and grep-ability.
- **G7 `verify_ok` propagation via `record_done(verify_ok: bool)` in `_dedup_gate.py`**:
  The gate is the only place that knows both the dispatch result and the verification result.
  Propagating `verify_ok` through to `store.transition()` is the minimum-surface change.

- **G7 RECON pattern is always-present, NOT omit-on-zero**: An earlier draft of this plan
  prescribed an `unverified_count` field with omit-on-zero semantics. The actual implementation
  uses the `dropped` sub-dict (`{"failed": N, "unverified": N}`) which is always present
  with both keys, zero permitted. Sibling fields `dropped.failed` and `dropped_ids.*`
  follow the same pattern, and forcing G7 alone to omit-on-zero would create operator
  cognitive load when parsing RECON output. Always-present + always-non-negative is the
  correct pattern. *(Reconciled during the 2026-05-28 deepening pass.)*

- **G7 contract includes exit code 5 on any unverified row**: The implementation in
  `_publish_helpers.py` exits the saga via `emit_envelope_and_exit("InternalError", 5, ...)`
  whenever `len(unverified) > 0`, after first printing each failing row's id and status to
  stderr. This is a stronger enforcement than the origin brainstorm's Step 3i (which only
  said "exit non-zero"); the precise exit code is now part of the saga's external contract
  and is asserted by contract tests on the saga branch.

- **Reuse of exit code 5 (vs allocating exit 6) is a deliberate trade-off**: Exit 5 was
  previously documented as "no payloads were published" and now ALSO fires on "some posts
  went live but verification failed". The two conditions share `error_class=InternalError`
  in the JSON envelope and can only be discriminated by stderr content. Rationale for
  reuse rather than allocating a new exit code:
  (a) Both firing conditions warrant operator review — neither is a clean terminal state,
      so collapsing them under "InternalError / exit 5" keeps the exit-code semantic
      "something needs human attention before the saga is safe to re-run as-is".
  (b) Exit codes 0–6 are the documented contract surface (CLI section in `AGENTS.md`);
      pushing to a 7th code would force a contract-table revision and would require all
      downstream consumers to learn a new code.
  (c) Stderr enumerates each unverified `(id, status)` pair, giving operators and CI
      wrappers an in-band discriminator. The envelope is structured (`error_class`,
      `message`), so callers parsing envelope-aware can also distinguish.
  Acknowledged cost: a caller that branches only on exit code and treats exit 5 as
  "safe to retry the whole batch" will now double-post on transient verification flakes.
  **PR #291 merged 2026-05-28 without an AGENTS.md Known Traps entry for this dual semantic** —
  the AGENTS.md update is now a high-urgency post-merge follow-up; until it lands,
  downstream consumers reading the documented contract have no signal that exit 5
  changed meaning.

## Open Questions

### Resolved During Planning

**[Original planning, prior to deepening]**

- **Where does `skipped_canary` count live?** Already accumulated as
  `skipped_quarantined_count` in `publish_backlinks.py:205`. Just needs to be threaded into
  `_publish_epilogue` and emitted in the RECON line.
- **Should `unverified` rows appear in `dropped.unverified` in the publish_reconciliation
  RECON?** Yes — the existing `publish_reconciliation` already has `dropped.unverified`
  (see `_publish_helpers.py:657–669`). G7 makes it non-zero in practice for the first time.
- **Does `_publish_epilogue` need both path changes for G7?** The two-seam rule applies to
  the **three** per-row WRITE sites that stamp the `_unverified` status suffix
  (`publish_backlinks.py:383` fresh + `_resume.py:178,419` resume); the epilogue READ side
  is single-source (`_publish_helpers.py:714`) and does not need parallel changes. All
  three write sites already stamp the same suffix, so **the two-seam rule is satisfied** —
  not bypassed. (See System-Wide Impact → "API surface parity / two-seam rule for G7" for
  full coverage.)

**[Resolved during the 2026-05-28 deepening pass]**

- **G7: `outputs` row signal is the `status` string suffix, NOT a row attribute named `verify_ok`**.
  Grep-verified at `publish_backlinks.py:383` (fresh path: `outputs[-1]["status"] += "_unverified"`),
  `_resume.py:178` (resume happy path), and `_resume.py:419` (resume fallback). The epilogue
  reader at `_publish_helpers.py:712-714` consumes the suffix:
  `unverified = [s for s in successful if s.get("status", "").endswith("_unverified")]`.
  No dedup-store query is required at epilogue time; the question of "what attribute" is
  resolved as "the status string". The dedup-store `verify_ok` column persists for separate
  introspection and `record_done()` audit purposes but is not the epilogue signal.
- **G3: Dry-run never sets `checkpoint_disabled`** — verified by contract test
  `test_contract_g3_dry_run_does_not_set_checkpoint_disabled` (`tests/test_saga_contracts.py:265`)
  which asserts the field is absent in dry-run RECON output.

### Deferred to Implementation

*(none remaining — both prior items resolved during the 2026-05-28 deepening pass)*

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not
> implementation specification. The implementing agent should treat it as context, not
> code to reproduce.*

**G4 data flow (skipped_canary):**

```
publish_backlinks.py (main loop)
  skipped_quarantined_count += 1          ← already exists at line 205
  ↓
_publish_epilogue(... skipped_canary=skipped_quarantined_count)   ← new parameter
  ↓
dedup_reconciliation RECON line:
  {"level":"RECON","event":"dedup_reconciliation","skipped_already_published":N,
   "held_uncertain":N,"dispatched":N,"skipped_canary":N}            ← new field
```

**G3 data flow (checkpoint_disabled):**

```
publish_backlinks.py (checkpoint except branch, line ~154)
  checkpoint_disabled = True               ← new flag variable
  ↓
_publish_epilogue(... checkpoint_disabled=checkpoint_disabled)    ← new parameter
  ↓
publish_reconciliation RECON line:
  {"level":"RECON","event":"publish_reconciliation",...,
   "checkpoint_disabled":true}             ← new field (omit if False)
```

**G7 RECON surfacing — as actually shipped on `feat/saga-hardening` (no state machine change):**

```
publish_backlinks.py:383 (fresh path) ─┐
_resume.py:178 (resume happy) ─────────┼─→ outputs[i]["status"] += "_unverified"
_resume.py:419 (resume fallback) ──────┘
                ↓
_publish_helpers.py:714 (epilogue, single reader):
  successful = [r for r in outputs if r.get("error") is None]
  unverified = [s for s in successful
                if s.get("status", "").endswith("_unverified")]
                ↓
_publish_helpers.py:724–731 (publish_reconciliation RECON line):
  {"level":"RECON","event":"publish_reconciliation","success_count":N,
   "fail_count":N,...,
   "dropped":     {"failed": N, "unverified": N},        ← always present, 0 permitted
   "dropped_ids": {"failed": [...], "unverified": [...]}}
                ↓
_publish_helpers.py:763–771 (exit-code dispatch):
  if unverified:                                          ← any unverified rows
    for u in unverified: print(f"verification failed: id=...", file=stderr)
    emit_envelope_and_exit("InternalError", 5, ...)       ← exit code 5

Note: store.py, _dedup_gate.py, and idempotency/ are NOT changed.
verify_ok=False is still persisted by record_done() at publish_backlinks.py:391 and
_resume.py:374 for audit / recheck consumption, but the epilogue does not consult it —
the status-string suffix is the sole epilogue signal.
```

## Implementation Units

```mermaid
TB
  U1[U1: Runbook doc — SHIPPED]
  U2[U2: G4 skipped_canary — SHIPPED]
  U3[U3: G3 checkpoint_disabled — SHIPPED]
  U4[U4: G7 status-suffix + exit 5 — SHIPPED]
  U5[U5: Contract tests — SHIPPED]
  U2 --> U5
  U3 --> U5
  U4 --> U5
```

- [x] **Unit 1: Operator Runbook** *(SHIPPED at `docs/runbooks/2026-05-28-publish-saga.md` in PR #291, commit `16c09a93`)*

220-line operator-facing reference: quick-reference table for all 6 pipeline steps + per-row
3c–3k, exit-code table, G1–G8 gap registry as "known limitations", links back to origin
brainstorm. Final shape on `feat/saga-hardening` is the contract.

---

- [x] **Unit 2: G4 — Emit `skipped_canary` in `dedup_reconciliation` RECON** *(SHIPPED)*

`dedup_reconciliation` RECON now emits `skipped_canary: N` (always present, zero permitted)
so canary-skipped rows are accounted for in row totals. SHIPPED in PR #291 (`16c09a93`) as
a one-line addition to the RECON dict in `_publish_helpers.py`; covered by
`test_contract_g4_*` (3 tests).

---

- [x] **Unit 3: G3 — Emit `checkpoint_disabled` in epilogue RECON** *(SHIPPED in PR #291, commit `16c09a93`)*

`publish_reconciliation` RECON emits `checkpoint_disabled: true` when checkpoint creation
fails (omit-on-false), so operators can detect that resume capability was lost without
needing to grep stderr warnings. Dry-run never enters the checkpoint branch, so the field
stays absent there. Covered by `test_contract_g3_*` (3 tests, including
`test_contract_g3_dry_run_does_not_set_checkpoint_disabled`).

---

- [x] **Unit 4: G7 — `dropped.unverified` + exit code 5 contract (SHIPPED on `feat/saga-hardening`)**

**Status:** Implemented and MERGED in PR #291 squash-commit `189d9010` on 2026-05-28. The 2026-05-28 deepening pass reconciled
this unit from its original "add `unverified_count` field" prescription to a post-hoc
description of the actual shipped contract. The contract is **stronger** than the original
plan proposed (informational counter → operator-blocking exit code 5).

**Goal:** Operators can distinguish "published and verified" from "published but links not
confirmed" by reading the `publish_reconciliation` RECON line. Unverified rows additionally
trip a non-zero exit code, surfacing the condition to any automation watching the saga.

**Requirements:** R5.G7

**Dependencies:** U2, U3 complete (avoid concurrent changes to `_publish_helpers.py`)

**Files (as actually touched on the saga branch):**
- `src/backlink_publisher/cli/_publish_helpers.py` — aggregation + RECON emission + exit-5
  dispatch around line 712 onward
- `src/backlink_publisher/cli/publish_backlinks.py` — fresh-path status-suffix write site
  (`outputs[-1]["status"] += "_unverified"` at line 383)
- `src/backlink_publisher/cli/_resume.py` — resume-path status-suffix write sites (lines 178
  and 419) — *no edits required by U4; already correct from prior dedup-gate work*
- `tests/test_saga_contracts.py` — contract tests
  `test_contract_g7_unverified_count_zero_on_success` and
  `test_contract_g7_unverified_count_nonzero_on_verify_failure`

**Implemented contract (descriptive, not prescriptive):**

- Per-row write side: when `verify_ok=False`, the row's `status` string gets `_unverified`
  appended at **three sites** — fresh path (`publish_backlinks.py:383`), resume happy path
  (`_resume.py:178`), resume fallback (`_resume.py:419`). All three sites stamp the same
  suffix; there is no other channel.
- Epilogue read side: a single aggregation at `_publish_helpers.py:712-714` partitions
  successful rows and isolates those whose status ends with `_unverified`. No per-row
  attribute lookup; no dedup-store query.
- RECON emission (`_publish_helpers.py:724-731`): `publish_reconciliation` carries
  `dropped: {"failed": N, "unverified": N}` and `dropped_ids: {"failed": [...], "unverified": [...]}`
  — both always-present, zero permitted, mirroring the surrounding `dropped.failed` and
  `dropped_ids.failed` pattern.
- Exit-code enforcement (`_publish_helpers.py:763-771`): when any unverified rows exist,
  the saga prints each `(id, status)` pair to stderr and calls
  `emit_envelope_and_exit("InternalError", 5, ...)`. Exit code 5 is therefore part of the
  G7 contract surface, alongside the RECON fields. *(This is stronger than the origin
  brainstorm Step 3i's "exit non-zero" wording; see Origin brainstorm follow-up in the
  Overview section.)*
- No changes to `store.py`, `_dedup_gate.py`, or `idempotency/`. The dedup-store
  `verify_ok` column is still persisted by `record_done(verify_ok=...)` at
  `publish_backlinks.py:391` and `_resume.py:374` for audit and recheck purposes, but the
  epilogue does not consult it.

**Patterns followed:**
- `dropped.failed` / `dropped_ids.failed` siblings in the same `publish_reconciliation`
  RECON dict
- Status-suffix convention shared with other downstream consumers
  (see `events/kinds.py:147` — "Success status whose concrete kind (confirmed vs unverified)
  is resolved by...")

**Test scenarios (as implemented on saga branch):**
- Happy path: all rows verify_ok=True → `dropped.unverified == 0`; `dropped_ids.unverified == []`
- Failure path: one row fails verification → `dropped.unverified == 1`; saga exits with
  code 5; stderr contains `verification failed: id=... status=..._unverified`
- (Implicit, covered by U5 integration assertions) `dropped.unverified +
  dropped.failed + success_count` equals the dispatched row count

**Verification:**
- `pytest tests/test_saga_contracts.py::test_contract_g7_unverified_count_nonzero_on_verify_failure -v`
- `git diff origin/main..origin/feat/saga-hardening -- src/backlink_publisher/idempotency/`
  produces no output (state machine unchanged)
- Reading `_publish_helpers.py:712-771` shows the always-present RECON pattern and the
  exit-5 dispatch in `emit_envelope_and_exit`

---

- [x] **Unit 5: Saga Contract Tests** *(SHIPPED at `tests/test_saga_contracts.py` in PR #291, commit `16c09a93` — 14 tests, 368 lines)*

Machine-checkable assertions for the saga contracts: exit codes (0/2/3/4), RECON field
presence on both `dedup_reconciliation` and `publish_reconciliation`, G3/G4/G7 contract
surfaces (including `dropped.unverified` zero/nonzero and dry-run-doesn't-set-checkpoint-disabled),
and row-accounting completeness. Reuses `_run_publish()` from `test_publish_backlinks.py`;
test names prefixed `test_contract_` for grep-ability.


---

## System-Wide Impact

- **Interaction graph:**
  - `_publish_epilogue` signature changes (U3) — only called from
    `publish_backlinks.py:main()`. No other callers; risk contained.
  - U2 (`skipped_canary`): six-line change to `_publish_helpers.py` RECON dict only.
    No signature change. Lowest-risk unit.
  - U4 (G7): epilogue reads `outputs` list and partitions by `status.endswith("_unverified")`.
    Centralised aggregation; no fan-out. No changes to `store.py`, `_dedup_gate.py`, or
    `idempotency/`.

- **Error propagation:**
  - G3/G4 are additive RECON fields — no error propagation change.
  - **G7 changes error propagation**: any row whose verification fails now causes the
    saga to exit with code 5 via `emit_envelope_and_exit("InternalError", 5, ...)` at
    `_publish_helpers.py:769-771`. Stderr enumerates each failing `(id, status)`. Calling
    scripts and CI runners that previously assumed "exit 0 = success" must treat exit 5
    as a verification-only failure mode (the post is live; the link check failed).

- **State lifecycle risks:**
  - G7: No state machine change. The dedup store still moves rows to `done` on dispatch
    success; the `verify_ok` column on the `done` row carries the verification result for
    audit and `recheck` service consumption. Read-only at epilogue time.

- **API surface parity / two-seam rule for G7:**
  - **Write side has three sites** (not two), all already correctly stamping the suffix
    on the saga branch:
    1. `publish_backlinks.py:383` — fresh path (`outputs[-1]["status"] += "_unverified"`)
    2. `_resume.py:178` — resume happy path (verification re-run on resumed `attempting` rows)
    3. `_resume.py:419` — resume fallback path
  - **Read side is single-source**: `_publish_helpers.py:712-714` partitions `outputs`
    by the suffix. Because all writers stamp the same suffix and the reader consumes only
    that suffix, the two-seam rule is satisfied: any future change to "what marks a row
    unverified" must touch all three write sites simultaneously, but the reader needs no
    update. Add this to `docs/solutions/integration-issues/` if/when a future seam is
    introduced.
  - `verify_ok` is also persisted by `record_done(verify_ok=...)` in both fresh and resume
    paths for non-epilogue consumers (recheck service, audit queries), but is not the
    epilogue signal.

- **Integration coverage:**
  - Saga contract tests (`tests/test_saga_contracts.py`) cover both zero and nonzero
    `dropped.unverified` cases plus exit-code-5 dispatch. The "RECON matches dedup-store"
    integration assertion proposed in the original plan was not required because the
    epilogue does not consult the dedup store for this field.

- **Unchanged invariants:**
  - `done` and `failed` dedup-state transition semantics are unchanged.
  - Exit codes 0, 2, 3, 4 are unchanged; exit code 5 is **extended** with a new firing
    condition (any unverified row), but the code itself was previously defined for "no
    payloads published".
  - All existing tests in `test_publish_backlinks.py` and `test_idempotency_store.py`
    must still pass (PR #291 CI gate).

## Risks & Dependencies

| Risk | Status / Mitigation |
|------|---------------------|
| G3: `checkpoint_disabled` flag in dry-run mode emits confusing `true` | **Resolved**: `test_contract_g3_dry_run_does_not_set_checkpoint_disabled` (`tests/test_saga_contracts.py:265`) asserts the field is absent in dry-run RECON. |
| G7: `outputs` row objects don't expose `verify_ok` at epilogue time | **Resolved (deepening 2026-05-28)**: signal is `status.endswith("_unverified")` string suffix, not a row attribute; no dedup-store fallback needed. |
| Monolith budget: `_publish_helpers.py` ceiling 660 (was 580) | **Within budget**: `radon raw -s` on `feat/saga-hardening` reports SLOC=628 (32-line headroom). The G3+G4+G7 changes that landed in PR #291 total +6 lines on `_publish_helpers.py`. |
| G7 exit-5 surprises external automation that previously only saw exit 0 on success | **Documented as new contract** in U4 body and Documentation/Operational Notes; runbook entry should call this out explicitly. Suggests a small follow-up to AGENTS.md Known Traps. |
| Origin brainstorm Step 3i still describes G7 as "exit non-zero" without specifying 5; Gap Registry not updated | **Out of plan-005 scope** — see single follow-up entry in Documentation / Operational Notes. |
| G7 magic string `"_unverified"` is hand-typed at 3 write sites; no `UNVERIFIED_SUFFIX` constant or helper. A future fourth seam (batch dispatcher / adapter wrapper / new resume mode) could typo the suffix and silently break the contract — the reader's `status.endswith("_unverified")` would return `False` and exit 5 would not fire on the bad row. | **Deferred to a separate plan (008-refactor-unverified-suffix-constant)** for extraction of `UNVERIFIED_SUFFIX = "_unverified"` + `mark_row_unverified(row)` helper. Not part of plan 005's smallest-safe-diff scope; the existing three sites are correct as shipped. |

## Documentation / Operational Notes

**Post-merge follow-ups** (PR #291 already merged at `189d9010` on 2026-05-28 without the
items below; ordered by urgency):

- **HIGH urgency** — Update `AGENTS.md` Known Traps section to document the dual semantic
  of exit code 5 (legacy "no payloads were published" + new "some posts went live,
  verification failed"). State that both share `error_class=InternalError` in the
  envelope; stderr enumerates unverified `(id, status)` pairs as the in-band
  discriminator. Explicitly warn callers against blind `if [ $? -eq 5 ]; then retry; fi`
  patterns — retrying on the new condition causes double-posting. **This was scoped as a
  pre-merge blocker during the 2026-05-28 deepening pass but PR #291 merged before the
  update landed; ship the AGENTS.md change in a small follow-up PR as soon as possible.**
- Reference `docs/runbooks/2026-05-28-publish-saga.md` from `AGENTS.md` in the "Operations"
  or "Debugging" section so operators can find it.

**Other follow-ups:**

- *(Future Work, not scoped here)* Incident-review template at
  `docs/runbooks/_template-incident-review.md` — uniform scaffold so future incidents
  inherit a structure. Recommended sections: Date / Affected surface / Symptom /
  Detection signal / Root cause / Contributing factors / Compensation taken / Permanent
  fix / Tests added / Runbook updates / Memory or solutions notes / Owners and follow-ups.
  Cap ≤60 lines. Land as a separate small PR if/when there is real demand.
- Origin brainstorm touch-up (separate, small PR): mark G7 RESOLVED in
  `docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md` Gap Registry,
  and tighten Step 3i's "exit non-zero" wording to specify exit 5.
- *(Future Work, not scoped here)* `UNVERIFIED_SUFFIX = "_unverified"` constant +
  `mark_row_unverified(row)` helper at the publish-side seam — write up as separate
  plan `2026-XX-008-refactor-unverified-suffix-constant`. Trigger only if a fourth
  write seam is added or a typo regression actually occurs; otherwise YAGNI.
- External CI scripts / wrapper tools outside this repo that branch on the saga exit code
  are outside enumerable scope; the AGENTS.md Known Traps entry is the canonical
  notification surface for downstream consumers.

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-28-publish-saga-contracts-requirements.md](../brainstorms/2026-05-28-publish-saga-contracts-requirements.md)
- Epilogue function: `src/backlink_publisher/cli/_publish_helpers.py:623–720`
- Dedup store: `src/backlink_publisher/idempotency/store.py`
- Dedup gate: `src/backlink_publisher/cli/_dedup_gate.py`
- Checkpoint logic: `src/backlink_publisher/cli/publish_backlinks.py:141–156`
- Two-seam learning: `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`
- Epilogue extraction: `docs/solutions/best-practices/extract-cli-epilogue-block-2026-05-26.md`
