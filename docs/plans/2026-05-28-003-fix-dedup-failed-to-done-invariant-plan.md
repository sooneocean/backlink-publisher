---
date: 2026-05-28
sequence: "003"
type: fix
status: active
origin: docs/brainstorms/2026-05-28-dedup-failed-to-done-invariant-requirements.md
claims: {}
---

# fix: Enforce `failed→done` ratchet in dedup `record_done`

## Problem Frame

After PR #279 (dedup gate) and PR #285 (reliability policy layer), a latent state
corruption path exists in **observe mode**:

1. A fresh run with `BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED=1` dispatches a
   row into `publish_with_policy`.
2. The policy layer returns `status="skipped_policy"` or `status="skipped_circuit_open"`.
   The adapter was never called. `result.error` is truthy.
3. `publish_backlinks.py` calls `record_failure(row, platform, error_class=None)`.
4. `_record_terminal("failed")` transitions the dedup key `attempting → failed`.
5. Operator fixes the channel binding and runs `--resume`. `gate()` (observe mode)
   calls `record_intent → intent_write` — INSERT OR IGNORE, no-op on existing key.
6. Dispatch succeeds. `record_done → _record_terminal("done")` hits the early-exit
   guard `if rec.state in ("done", "failed"): return`. Key stays `"failed"` forever.
7. When enforce mode is later activated, the gate treats these `"failed"` keys as
   re-publishable → **duplicate posts**.

**Invariant**: After `record_done` is called for a successfully published row (adapter
returned no error), `DedupStore().get(key).state` MUST be `"done"`.

`record_done` is a ratchet — it can only advance a key toward `"done"`, never leave it
at a lower state.

**Secondary finding**: `publish_backlinks.py` in-band error path does not update the
checkpoint item (leaves it `"pending"`), while `_resume.py` does
(`_ckpt.update_item(..., "failed", ...)`). The comment at `_resume.py:326` falsely
claims parity with the fresh seam.

(see origin: docs/brainstorms/2026-05-28-dedup-failed-to-done-invariant-requirements.md)

## Scope

- Only `_dedup_gate._record_terminal` and the `publish_backlinks.py` in-band error
  path are changed.
- No schema migration, no new store fields, no new CLI flags.
- No change to enforce mode (gate_and_claim re-claims "failed" → "attempting" before
  dispatch; already works correctly).
- `store.py transition` is not changed; `allow_from_terminal` is an existing kwarg.

## Implementation Units

### Unit 1 — Fix `_record_terminal` guard (R1)

**File**: `src/backlink_publisher/cli/_dedup_gate.py`

**Change**: Lines 235–251 — revise the early-exit guard so `"failed" → "done"` is
allowed while preserving all other invariants.

Current guard (broken for failed→done):
```python
if rec.state in ("done", "failed"):
    return
if rec.state == "uncertain" and state == "failed":
    return
store.transition(key, state, live_url=live_url, verify_ok=verify_ok, run_id=run_id)
```

Required guard:
```python
if rec.state == "done":
    return                              # confirmed success — immutable
if rec.state == "uncertain" and state == "failed":
    return                              # never downgrade a held key
# "failed" → "done" is a valid path (policy-skip then later success).
# allow_from_terminal=True is required because store._TERMINAL includes "failed";
# without it, transition raises ValueError (swallowed silently by the except arm).
allow = rec.state == "failed" and state == "done"
store.transition(key, state, live_url=live_url, verify_ok=verify_ok, run_id=run_id,
                 allow_from_terminal=allow)
# Note: "failed" → "failed" falls through; store.transition raises ValueError
# (swallowed by the outer except). This matches pre-existing behavior.
```

Update the existing comment on the old `"done", "failed"` branch to reflect the
narrowed guard (`"done"` only, not `"failed"`).

**Monolith check**: `_dedup_gate.py` is 253 lines and is **not** tracked in
`monolith_budget.toml` — no budget update needed for Unit 1. `publish_backlinks.py`
IS tracked (ceiling 370, currently ~348 SLOC); Unit 3 adds ~8 SLOC — within budget.

- [ ] Revise early-exit guard in `_record_terminal`
- [ ] Update inline comment to reflect narrowed `"done"`-only exit
- [ ] Run `python -m radon raw -s src/backlink_publisher/cli/_dedup_gate.py` and
      update `monolith_budget.toml` if the ceiling is exceeded

---

### Unit 2 — Regression and stability tests (R2)

**File**: `tests/test_dedup_record_on_publish.py`

Add four test cases at the end of the existing file. These MUST run against the
real `DedupStore` (tmp_path + store fixture pattern matching existing tests in this
file — e.g., `test_uncertain_not_downgraded_to_failed_by_later_non_5xx` at line 328).
Do NOT mock the store.

**(a) Core regression — FAILS before fix, PASSES after**

1. Seed store: `store.intent_write(key)` then `store.transition(key, "failed")`.
2. Call `record_done(row, platform, live_url=url, verify_ok=True, run_id=rid)`.
3. Assert `DedupStore().get(key).state == "done"` and `live_url == url`.

**(b) Full seam variant — FAILS before fix, PASSES after**

1. Seed store at `"failed"` (same as a).
2. Call `gate(row, platform, run_id=rid)` in observe mode (no enforce env var).
3. Assert `verdict == "dispatch"` (gate must not skip or hold).
4. Call `record_done(row, platform, live_url=url, verify_ok=True, run_id=rid)`.
5. Assert `DedupStore().get(key).state == "done"`.

This confirms `gate()` in observe mode does not accidentally re-claim the key
(intent_write is INSERT OR IGNORE) and the full path produces `"done"`.

**(c) Immutability guard — PASSES both before and after (stability check)**

1. Call `record_done` on a row that already has `state == "done"` (set up via
   a first successful `record_done` call, not via raw store methods).
2. Assert state stays `"done"`, no exception propagated to caller.

**(d) Uncertain upgrade — PASSES both before and after (stability check)**

1. Seed store at `"uncertain"` via `intent_write` + `store.transition(key, "uncertain")`.
2. Call `record_failure(row, platform, error_class=None)` — must be a no-op
   (uncertain → failed downgrade blocked).
3. Assert state still `"uncertain"`.
4. Call `record_done(row, platform, live_url=url, verify_ok=True)`.
5. Assert state `"done"`.

**Existing patterns to follow**:
- `test_uncertain_not_downgraded_to_failed_by_later_non_5xx` (line 328) — stores
  built with `DedupStore(store_path=tmp_path / "dedup.db")`.
- `test_uncertain_can_still_settle_to_done` (line 344) — `record_done` called
  directly on a real store.

- [ ] Add test (a) `test_failed_key_advances_to_done_on_record_done`
- [ ] Add test (b) `test_observe_gate_then_record_done_advances_failed_to_done`
- [ ] Add test (c) `test_done_key_immutable_on_second_record_done`
- [ ] Add test (d) `test_uncertain_upgrade_to_done_still_works`
- [ ] Run `pytest tests/test_dedup_record_on_publish.py` — verify (a) and (b) FAIL
      before Unit 1 is applied, PASS after; (c) and (d) PASS both times

---

### Unit 3 — Fresh seam checkpoint fix + comment fix (R4 + R5)

**File 1**: `src/backlink_publisher/cli/publish_backlinks.py`

In the `if result.error:` branch (lines ~352–356), add a `_try_update_ckpt_failed`
call after `record_failure`:

```python
if result.error:
    fail_count += 1
    record_failure(row, platform, error_class=None, run_id=run_id)
    error_class_for_ckpt = (
        "policy_skip"
        if result.status in ("skipped_policy", "skipped_circuit_open")
        else "unexpected"
    )
    run_id = _try_update_ckpt_failed(
        run_id, row.get("id", ""), str(result.error), error_class_for_ckpt
    )
```

`_try_update_ckpt_failed` is defined at `_publish_helpers.py:301` and is **not**
currently in the `publish_backlinks.py` import block. Add it to the
`from ._publish_helpers import (...)` block before using it.

`run_id` is captured from the return value to maintain the existing pattern in
`_record_publish_failure` callers (preserves `run_id = None` on checkpoint failure).

**File 2**: `src/backlink_publisher/cli/_resume.py`

Fix the incorrect comment at line 330:

Current (wrong):
```python
# In-band adapter failure (returned, not raised) — record terminal so
# the row doesn't strand as `done`, mark the checkpoint item failed,
# and do NOT record done. Parity with the fresh seam (publish_backlinks
# has the same guard); without it a returned-error result would seed a
# `done` dedup row and enforce would permanently skip a post that never
# landed.
```

Replace with (accurate):
```python
# In-band adapter failure (returned, not raised) — record terminal so
# the row doesn't strand as `done`, mark the checkpoint item failed,
# and do NOT record done. Without this a returned-error result would
# seed a `done` dedup row and enforce would permanently skip a post
# that never landed. Parity with the fresh seam is now established
# by R4 (publish_backlinks.py uses _try_update_ckpt_failed).
```

- [ ] Add `_try_update_ckpt_failed` to `publish_backlinks.py` import if missing
- [ ] Add `_try_update_ckpt_failed` call with `error_class_for_ckpt` in the
      `result.error` branch of `publish_backlinks.py`
- [ ] Fix incorrect comment at `_resume.py:326`

---

## Key Decisions

- **Fix `_record_terminal`, not `record_intent`** — root cause is the guard that
  blocks `"failed" → "done"`. Fixing `record_intent` to overwrite `"failed"` on
  re-enter would be a wider change (INSERT OR IGNORE semantics are load-bearing for
  the dedup contract). (see origin)
- **`allow_from_terminal=True` for failed→done only** — `store._TERMINAL` includes
  `"failed"`. Without this flag, `transition` raises `ValueError`, which
  `_record_terminal`'s except arm swallows silently, leaving the key at `"failed"`.
  The flag is already a kwarg on `transition`; no signature change needed. (see origin)
- **`error_class='policy_skip'` for policy-blocked rows** — `skipped_policy` and
  `skipped_circuit_open` are deliberate gate decisions, not adapter failures.
  Labelling them `"unexpected"` misrepresents operator-visible failure counts.
  New value is additive; `_resume.py` in-band error path (genuine adapter failures)
  is unchanged. (see origin)
- **Use `_try_update_ckpt_failed` (not raw `checkpoint.update_item`) for R4** —
  `run_id` can be `None` when checkpoint creation failed. `_try_update_ckpt_failed`
  already has the `run_id=None` guard at line 309. (see origin)
- **Include R4+R5 in same PR** — same seam; the incorrect comment at `_resume.py:326`
  directly contributed to the invariant being missed during original PR review.
  (see origin)

## Sequencing

Unit 2 can be written before Unit 1 to verify the regression tests fail. In practice,
implement in order 2 → 1 → 3 (write tests first, apply fix, then add checkpoint parity).
Units 1+2 are independent of Unit 3.

## Test Scenarios Summary

| Test | File | Before fix | After fix |
|---|---|---|---|
| (a) failed→done via record_done | test_dedup_record_on_publish.py | FAIL | PASS |
| (b) gate→record_done seam (failed start) | test_dedup_record_on_publish.py | FAIL | PASS |
| (c) done immutability | test_dedup_record_on_publish.py | PASS | PASS |
| (d) uncertain→done pre-existing | test_dedup_record_on_publish.py | PASS | PASS |
| Full suite regression | tests/ | — | no new failures |

## Risks

- **Monolith ceiling**: `_dedup_gate.py` may be monitored. Net +3 lines — check
  after edit. If ceiling is tight, the comment update can absorb some lines.
- **`error_class='policy_skip'` downstream consumers**: Grep `error_class` usages
  in `_resume.py`, projector, and `--list-runs` before shipping to confirm `"policy_skip"`
  doesn't hit an unexpected branch. Expected: `error_class` is stored as a string in
  the checkpoint DB and surfaced as-is in `--list-runs` output — additive, safe.
