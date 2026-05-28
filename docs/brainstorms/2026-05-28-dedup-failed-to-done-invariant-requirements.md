---
date: 2026-05-28
topic: dedup-failed-to-done-invariant
---

# Dedup Invariant: `record_done` Must Always Reach `"done"`

## Problem Frame

After PR #279 shipped the dedup gate and PR #285 shipped the reliability policy
layer, a latent state corruption path exists in **observe mode**:

1. A fresh `publish-backlinks` run with `BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED=1`
   dispatches a row to `publish_with_policy`.
2. The policy layer returns `status="skipped_policy"` or `status="skipped_circuit_open"`
   (channel not bound / circuit open) — the adapter was **never called**.
3. `publish_backlinks.py:356` detects `result.error` is truthy and calls
   `record_failure(row, platform, error_class=None)`.
4. `_record_terminal("failed")` transitions the dedup key from `"attempting"` → `"failed"`.
5. The checkpoint item is **not** updated (stays `"pending"`) — fresh seam diverges from
   the resume seam here (see [Secondary Finding]).
6. Operator fixes the channel binding (or waits for circuit reset) and runs `--resume`.
7. `gate()` in observe mode calls `record_intent` → `intent_write` → **no-op** because
   the key already exists at `"failed"`.  The key stays `"failed"`.
8. Dispatch succeeds.  `record_done` → `_record_terminal("done")` reads the key:

```python
if rec.state in ("done", "failed"):
    return  # do not re-transition          ← BLOCKS the transition
```

9. Key permanently stays `"failed"` even though the post landed successfully.

**Why this matters**: "failed" means "confirmed not landed — safe to re-dispatch" in
the dedup semantics.  When the operator later enables `BACKLINK_PUBLISHER_DEDUP_ENFORCE=1`,
the gate treats these keys as re-publishable and **will re-publish already-live posts**,
creating duplicates.

Enforce mode is unaffected today because `gate_and_claim` re-claims the key from
`"failed"` → `"attempting"` before dispatch, so `record_done` finds `"attempting"` (not
`"failed"`) and transitions correctly.  The bug is observe-mode only, and manifests when
enforce mode is later activated on a corpus that accumulated observe-mode policy skips.

## Invariant

> **After `record_done` is called for a row that was successfully published (adapter
> returned a result with no error), `DedupStore().get(key).state` MUST be `"done"`.**

Equivalently: `record_done` is a **ratchet** — it can only advance a key toward `"done"`,
never leave it at a lower state. The one exception is a key that is already `"done"`
(no re-write needed) or `"uncertain"` → `"done"` (allowed, upgrades the held key).

The current early-exit guard in `_record_terminal` violates this invariant for the
`"failed"` state:

```python
# current (wrong for failed → done):
if rec.state in ("done", "failed"):
    return
```

## Requirements

**Core invariant fix**

- R1. Revise the early-exit guard in `_dedup_gate._record_terminal` so that
  `"failed" → "done"` is allowed.  `"done"` is still permanently terminal (no
  re-transition from `"done"`).  `"uncertain" → "failed"` remains blocked (never
  downgrade a held key).  The revised logic:

  ```python
  if rec.state == "done":
      return                              # already confirmed success — immutable
  if rec.state == "uncertain" and state == "failed":
      return                              # never downgrade a held key
  # "failed" → "done" is now a valid path (policy-skip then later success).
  # Pass allow_from_terminal=True when advancing from a terminal state to "done"
  # so store.transition does not raise ValueError (store.py:_TERMINAL includes "failed").
  allow = rec.state == "failed" and state == "done"
  store.transition(key, state, live_url=live_url, verify_ok=verify_ok, run_id=run_id,
                   allow_from_terminal=allow)
  # Note: "failed" → "failed" falls through here; store.transition raises ValueError
  # (caught by the outer except and silently swallowed). This matches pre-existing behavior.
  ```

- R2. Add a test suite in `tests/test_dedup_record_on_publish.py` (or a new file if
  it grows past the monolith ceiling) that covers all three state-transition cases:

  **(a) Core regression — must FAIL before the fix, PASS after:**
  1. Seed the store: `store.intent_write(key)` then `store.transition(key, "failed")`.
  2. Call `record_done(row, platform, live_url=url, verify_ok=True, run_id=rid)`.
  3. Assert `DedupStore().get(key).state == "done"` and `live_url` is set.

  **(b) Full seam variant — also must FAIL before the fix, PASS after:**
  1. Seed the store at `"failed"` (same as above).
  2. Call `gate(row, platform, run_id=rid)` in observe mode (no enforce env var).
  3. Assert `verdict == "dispatch"` (gate must not skip or hold).
  4. Call `record_done(row, platform, live_url=url, verify_ok=True, run_id=rid)`.
  5. Assert `DedupStore().get(key).state == "done"`.
  This confirms gate() does not accidentally re-claim the key and that the full call
  chain (gate → dispatch → record_done) produces the correct terminal state.

  **(c) Immutability guard — passes both before and after (stability check):**
  1. Call `record_done` twice on the same row (second call with key already at `"done"`).
  2. Assert state stays `"done"`, no exception propagated to caller.
  Note: use `record_done` (not `store.seed`) for setup so the path matches production.

  **(d) Uncertain upgrade — pre-existing behavior, passes before and after (stability check):**
  1. Seed the store at `"uncertain"` via `intent_write` + `transition(key, "uncertain")`.
  2. `record_failure` must be a no-op (key stays `"uncertain"`).
  3. `record_done` must succeed (key advances to `"done"`).
  Note: uncertain → done is not modified by R1; this test guards against accidental regression.

**Secondary finding: checkpoint divergence (scoped to this PR)**

- R4. In `publish_backlinks.py`, when `result.error` is truthy (in-band adapter failure
  including policy-skip results), call `_try_update_ckpt_failed(run_id, row.get("id", ""),
  str(result.error), error_class)` — using the existing helper in `_publish_helpers.py`
  which already guards `run_id=None` (i.e., no-ops silently when checkpoint creation
  failed earlier).  Derive `error_class` from `result.status`:
  `"policy_skip"` for `status in ("skipped_policy", "skipped_circuit_open")`;
  `"unexpected"` otherwise.  Without this, the fresh seam leaves checkpoint items at
  `"pending"` while the resume seam marks them `"failed"` — operators using
  `--list-runs` see misleading "pending" items for rows that were policy-blocked.

- R5. Fix the incorrect comment in `_resume.py:330` that claims "parity with the fresh
  seam (publish_backlinks has the same guard)" — it does not currently have it; fix
  should update the comment to say "parity now established by R4".

**Out-of-scope for this PR**

- No change to policy-skip behavior itself (still returns `skipped_policy` /
  `skipped_circuit_open`); only the dedup recording is corrected.
- No change to `gate_and_claim` (enforce mode already works correctly).
- No change to the circuit breaker reset or policy activation flow.

## Success Criteria

- R2(a) and R2(b) regression tests fail on the current code and pass on the fixed code.
- R2(c) and R2(d) stability tests pass both before and after the fix.
- Running `pytest tests/test_dedup_record_on_publish.py` shows no regressions (all
  prior tests still green).
- In observe mode: after a policy-skip + successful `--resume`, `DedupStore().get(key).state == "done"`.
- The `"done"` immutability guard and `"uncertain"→"failed"` downgrade prevention are
  verified by R2(c) and R2(d) respectively.

## Scope Boundaries

- Only `_dedup_gate._record_terminal` and `publish_backlinks.py` in-band error path are changed.
- No schema migration, no new store fields, no new CLI flags.
- No behavior change in enforce mode (already works correctly via `gate_and_claim` re-claim).
- The `store.py` `transition` method is not changed; `allow_from_terminal` is an existing parameter.
- Checkpoint state changes (R4) are internal: `--list-runs` output format is unchanged; only the
  accuracy improves (policy-skipped rows show as `"failed"` instead of staying `"pending"`).
  No change to operator-facing CLI behavior.

## Key Decisions

- **Fix `_record_terminal`, not `record_intent`** — the root cause is the guard that
  blocks "failed" → "done". Fixing `record_intent` to overwrite "failed" on re-enter
  would be a deeper change with wider impact on the store state machine; it also wouldn't
  fix the specific case (observe mode calls `intent_write` which is `INSERT OR IGNORE`
  on an existing key regardless).
- **Allow "failed" → "done" in `_record_terminal`** — the original guard's rationale was
  to prevent re-counting already-done keys in observe mode. That rationale doesn't apply
  to `"failed"`: `"failed"` means "confirmed not landed", and a later `record_done` call
  is direct evidence the post did land. The state machine must upgrade.
- **`store.transition` requires `allow_from_terminal=True` for `failed → done`** — confirmed:
  `store.py:_TERMINAL = frozenset({"done", "failed"})` and `transition` raises `ValueError`
  without the flag (line 549). `_record_terminal` swallows all exceptions, so omitting the
  flag would silently no-op the write, leaving the key at `"failed"`. No signature change
  needed — `allow_from_terminal` already exists as a kwarg.
- **`error_class='policy_skip'` for policy-blocked rows** — `skipped_policy` and
  `skipped_circuit_open` are deliberate gate decisions, not adapter failures. Labelling them
  `"unexpected"` in the checkpoint would misrepresent operator-visible failure counts and
  could confuse downstream projector logic.  New value `"policy_skip"` is additive; does not
  change the `_resume.py` in-band error path (which handles genuine adapter failures).
- **Include checkpoint divergence fix (R4) in same PR** — it's the same seam, and the
  incorrect comment directly contributed to the invariant being missed during PR review.

## Outstanding Questions

### Deferred to Planning

- [Affects R4][Technical] Confirm `_try_update_ckpt_failed` call signature: verify the
  helper accepts `error_class` as a positional-or-kwarg and that passing `"policy_skip"`
  does not break downstream consumers (projector, `--list-runs` output). Grep
  `_try_update_ckpt_failed` usages before coding R4.

## Next Steps
→ `/ce:plan` for structured implementation planning
