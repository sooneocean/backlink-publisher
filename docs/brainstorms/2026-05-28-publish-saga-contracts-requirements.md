---
date: 2026-05-28
topic: publish-saga-contracts
---

# Publish Pipeline Saga Contracts

## Problem Frame

The publish pipeline spans six CLI entrypoints and a multi-step per-row loop
inside `publish-backlinks`. Currently, each step's success/failure boundary,
retry behaviour, and compensation are implicit — scattered across exception
handlers, exit codes, and inline comments. This makes the pipeline hard to
audit, test boundary conditions for, or explain to operators when something
goes wrong mid-run.

This document formalises the pipeline as a saga: each step gets an explicit
**success condition**, **failure condition**, **retry policy**, and
**compensating action**. The goal is a durable contract that review, tests,
and runbooks can reference — not a code refactor.

---

## Pipeline Overview

```
seeds.jsonl
    │
    ▼
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│  plan-backlinks │────▶│  validate-backlinks  │────▶│  publish-backlinks   │
│  (Step 1)       │     │  (Step 2)            │     │  (Step 3)            │
└─────────────────┘     └──────────────────────┘     └──────────────────────┘
                                                              │
                              ┌───────────────┬──────────────┼─────────────┐
                              ▼               ▼              ▼             ▼
                        report-anchors    footprint    phase0-seal   (epilogue)
                        (Step 4)          (Step 5)     (Step 6)
```

`publish-backlinks` (Step 3) contains a per-row inner saga:

```
For each row:
  3a Pre-flight ──▶ 3b Checkpoint ──▶ [per-row loop]
                                            │
  3c Token-drift ──▶ 3d Dedup-claim ──▶ 3e Reachability
                                            │
  3f Canary-gate ──▶ 3g Policy-gate ──▶ 3h Adapter-dispatch
                                            │
  3i Post-verify ──▶ 3j Dedup-terminal ──▶ 3k Checkpoint-update
```

---

## Requirements

### R1 — Step-level saga contracts

Every step (1–6 and 3a–3k) MUST be documented with four properties:

| Property | Definition |
|---|---|
| **Success condition** | Observable state that marks the step complete and allows the saga to advance |
| **Failure condition** | Observable state that marks the step failed; includes exit-code classification |
| **Retry policy** | Whether and how the step may be retried; idempotency prerequisite documented |
| **Compensating action** | How state produced by this step is undone if the saga later aborts; "no programmable compensation" is a valid answer, but must state the reason and the operator fallback |

### R2 — Pipeline steps (Steps 1, 2, 4, 5, 6)

**Step 1 — `plan-backlinks`**

| | |
|---|---|
| **Success** | Emits at least one valid JSONL row to stdout; exit 0 |
| **Failure** | Invalid seed schema → exit 2 (UsageError); LLM timeout / provider error → exit 3 (DependencyError); empty output with no seeds → exit 0 (advisory, not failure) |
| **Retry policy** | Fully idempotent — same seeds produce equivalent plans. Safe to re-run from scratch. |
| **Compensation** | Discard output buffer / redirect file. No side-effects to undo. |
| **Gap** | LLM provider errors are currently not classified into retry-eligible (transient) vs permanent (quota exhausted); saga should distinguish them. |

**Step 2 — `validate-backlinks`**

| | |
|---|---|
| **Success** | Each input row emits a validated row with reachability + anchor status; exit 0 |
| **Failure** | Schema violation → exit 2; network unreachable during SSRF check → exit 3; partial row failures surfaced as warnings, saga continues |
| **Retry policy** | Idempotent. Re-run with same input reproduces same output. Transient network errors: manual retry after connectivity restored. |
| **Compensation** | Discard output. No external state modified. |
| **Gap** | Partial failures (some rows invalid, others valid) do not currently produce a mixed-success exit code; operator cannot distinguish "all failed" from "some failed" without reading stderr. |

**Step 4 — `report-anchors`**

| | |
|---|---|
| **Success** | Anchor coverage report emitted; exit 0 |
| **Failure** | No published data in input → exit 0 (empty report, not error); read error on events DB → exit 3 |
| **Retry policy** | Idempotent (read-only). Re-run at any time. |
| **Compensation** | None. Read-only step. |

**Step 5 — `footprint`**

| | |
|---|---|
| **Success** | Footprint within configured budget; exit 0 |
| **Failure** | Budget exceeded → exit non-zero (operator must review distribution); |
| **Retry policy** | Not applicable — diagnostic only. Fix the upstream data, re-run. |
| **Compensation** | None. Read-only step. |

**Step 6 — `phase0-seal`**

| | |
|---|---|
| **Success** | Seal file committed; all Phase 0 criteria met; exit 0 |
| **Failure** | Criteria unmet (footprint, anchor counts) → exit non-zero; commit error → exit 3 |
| **Retry policy** | Idempotent once criteria are met. Re-run after fixing violations. |
| **Compensation** | Delete seal file. This is the only pipeline step whose compensation modifies durable local state in a meaningful way. |

### R3 — publish-backlinks outer steps (3a–3b)

**Step 3a — Pre-flight**

| | |
|---|---|
| **Success** | Args parsed; all leases acquired; all adapters verified; exit continues |
| **Failure** | Bad args → exit 2; lease conflict (concurrent run on same platform) → exit 3; adapter not configured / missing credential → exit 3 |
| **Retry policy** | Retry after root cause resolved (add credential, wait for concurrent run to finish). Lease is advisory only — not a hard distributed lock. |
| **Compensation** | Leases released on process exit (atexit). Gap: SIGKILL does not trigger atexit; PID liveness probe (see [[feedback_lease_pid_liveness_fallback]]) is the mitigation but is not currently enforced in all code paths. |

**Step 3b — Checkpoint creation**

| | |
|---|---|
| **Success** | Checkpoint file written with run_id; in-memory run context available |
| **Failure** | Filesystem error → WARNING logged, saga continues without checkpoint (resume capability lost); checkpoint file is not created |
| **Retry policy** | Non-fatal; not retried. |
| **Compensation** | Delete checkpoint file on full abort (if it was created successfully). Currently: checkpoint files are cleaned by `--cleanup` subcommand; no automatic cleanup on saga abort. Failed checkpoint creation does not require cleanup. |
| **Gap** | Checkpoint creation failure silently degrades resume capability. The epilogue should emit `checkpoint_disabled: true` if checkpoint creation failed (tracked by whether Step 3b succeeded), so operators can distinguish "run is resumable" from "run is not resumable". |

### R4 — publish-backlinks per-row steps (3c–3k)

These steps run inside the per-row loop. A failure in any step for a given row records that row as failed and continues to the next row — it does NOT abort the outer saga.

**Step 3c — Token drift check**

| | |
|---|---|
| **Success** | Credentials are valid as of check time; continues to 3d |
| **Failure** | Token revoked → AuthExpiredError → channel marked expired → outer saga exits 3 immediately |
| **Retry policy** | Not retried. Operator must refresh credentials and resume. |
| **Compensation** | None. No state changed by this step. |
| **Gap** | Check runs once at row start. If credentials expire mid-dispatch (between 3c and 3h), the error is caught at 3h as AuthExpiredError but the row may have already claimed the dedup slot. The dedup claim is released on failure (3d compensation), so no stranded claim — but the exit-3 on auth expiry still stops the whole run; partial dispatch without credential recheck is a latent race. |

**Step 3d — Dedup gate claim**

| | |
|---|---|
| **Success** | Row claimed in dedup store (state = `attempting`) or skipped (already done / held) |
| **Failure** | Dedup store write error → row skipped with WARNING; observe mode: dispatches anyway; enforce mode: holds row |
| **Retry policy** | Idempotent. If claim write fails, re-running the row re-attempts the claim. |
| **Compensation** | On row failure (any subsequent step 3e–3h): dedup store state updated to `failed`. If Step 3i fails (verification), state is updated to `unverified` (pending Gap G7); otherwise to `failed`. This releases the claim so the row can be re-attempted on resume. Currently: `failed` is implemented; `unverified` is not. |

**Step 3e — Reachability check**

| | |
|---|---|
| **Success** | target_url + all anchor links return HTTP 2xx |
| **Failure** | HTTP 4xx/5xx or connection error → row recorded as failed; saga continues to next row |
| **Retry policy** | Not retried inline. Row can be re-attempted via `--resume` after target becomes reachable. |
| **Compensation** | Dedup gate claim released (step 3d compensation applied). No external state to undo. |

**Step 3f — Canary gate**

| | |
|---|---|
| **Success** | Platform not quarantined, or quarantined with `hard_skip=false` (advisory warning emitted) |
| **Failure** | Quarantined + `hard_skip=true` → row skipped silently; not appended to failed outputs; not counted as success either |
| **Retry policy** | After canary recovery (platform un-quarantined), re-run / resume. |
| **Compensation** | None. Canary gate is a filter, not a mutating step. |
| **Gap** | Silently skipped rows (hard_skip) are neither successful nor failed — they are invisible in epilogue success/failure counts. Saga contract should require a `skipped_canary` counter in the epilogue so operators can account for all rows (success + failed + skipped_canary = total). |

**Step 3g — Policy gate (circuit breaker)**

| | |
|---|---|
| **Success** | Health check passes; circuit breaker is closed; continues to 3h |
| **Failure** | Circuit open → row skipped; health check fails → row deferred |
| **Retry policy** | After circuit closes (automatic or manual reset). |
| **Compensation** | None. Gate is a filter. |

**Step 3h — Adapter dispatch**

This is the only step that produces durable external state (a live post on a third-party platform).

| | |
|---|---|
| **Success** | Platform returns HTTP 2xx; published_url returned by adapter |
| **Failure** | `AuthExpiredError` → mark channel expired, exit 3; `ContentRejectedError` → row failed (permanent); `ExternalServiceError` → row failed; `BannerUploadError` → row failed; HTTP 429 → retried (see below); 5xx → row failed (not retried) |
| **Retry policy** | HTTP 429: exponential backoff, jitter 15%, max 3 attempts. 5xx: **not retried** (idempotency not guaranteed; risk of duplicate posts). All other failures: not retried inline. |
| **Compensation** | **Depends on platform:** |
| | • Platforms with DELETE API (e.g. Medium `/v1/posts/{id}`): compensation = call DELETE; currently not implemented in any adapter. |
| | • Platforms without DELETE API (Velog, Notes.io, etc.): **no programmable compensation**. Reason: platform does not expose a delete endpoint. Operator fallback: manual deletion via platform UI, documented in runbook. |
| | • Internal-only adapters (dry-run): compensation = no-op. |
| **Gap 1** | 5xx is not retried even when idempotency could be guaranteed via dedup gate (if the row was claimed *before* dispatch, a failed 5xx means the post likely wasn't created, so one retry is low-risk). The current blanket no-retry is conservative. This is a deliberate choice to avoid the complexity of conditional retry logic, but it may warrant reconsideration for enforce-mode scenarios (see Gap G5). |
| **Gap 2** | No adapter currently implements a `compensate()` method. Adding one is the natural next step for platforms with delete APIs. |

**Step 3i — Post-publish verification**

| | |
|---|---|
| **Success** | Published URL fetched and contains required anchor links within timeout (10–30s); row appended to stdout JSONL |
| **Failure** | Links not found in published page → row marked `unverified`; timeout → same; HTTP error on published URL → same |
| **Retry policy** | Not retried inline. Manual re-check possible via `recheck` service. |
| **Compensation** | If verification fails: adapter dispatch already succeeded (post is live). Compensation same as Step 3h — platform-specific or operator manual. |
| **Gap** | Currently, verification failure does not prevent the dedup store from recording the row as `done`. This means a failed verification row is indistinguishable from a successfully-verified row in dedup state. Saga contract should require a third terminal state: `unverified`. |

**Step 3j — Dedup terminal record**

| | |
|---|---|
| **Success** | Row state updated to `done` in dedup store; or `unverified` if Step 3i verification failed (pending Gap G7 implementation) |
| **Failure** | Write error → row state remains `attempting`; saga emits WARNING; row can be re-attempted on resume |
| **Retry policy** | Idempotent. Safe to write `done` again on re-run (no-op if already done). |
| **Compensation** | Revert state from `done` to `pending` (logical delete). Not currently implemented; would require a dedup store `undo_terminal()` operation. |

**Step 3k — Checkpoint update**

| | |
|---|---|
| **Success** | Checkpoint record updated with `published_url` and final status |
| **Failure** | Filesystem error → WARNING; checkpoint remains stale |
| **Retry policy** | Non-fatal. Not retried inline. |
| **Compensation** | Delete checkpoint row for this run_id on full abort. Handled by `--cleanup` subcommand. |

### R5 — Gap registry

The following gaps are documented in the contracts above. They are recorded here as a consolidated list for planning prioritisation. This document does **not** require any of them to be fixed — they are "describe current + flag gap" per the agreed scope.

| ID | Affected step | Gap | Recommended direction |
|---|---|---|---|
| G1 | Step 1 | LLM errors not classified (transient vs permanent) | Add `LLM_QUOTA_EXHAUSTED` vs `LLM_TRANSIENT` to error taxonomy |
| G2 | Step 2 | No mixed-success exit code | Add exit 4 (partial failure) or structured JSON epilogue with per-row status |
| G3 | Step 3b | Checkpoint failure degrades silently | Emit `checkpoint_disabled: true` in epilogue JSONL |
| G4 | Step 3f | Canary skips invisible in epilogue | Add `skipped_canary` counter to epilogue reconciliation |
| G5 | Step 3h | 5xx not retried even when dedup guarantees idempotency | In enforce-mode: allow one 5xx retry; document this in adapter contract |
| G6 | Step 3h | No adapter implements `compensate()` | Define `compensate(published_url) -> None` as optional adapter method |
| G7 | Step 3i | Verification failure doesn't produce distinct dedup state | Add `unverified` terminal state to dedup store |
| G8 | Step 3a | SIGKILL does not release leases | Implement PID liveness probe in lease acquisition (see [[feedback_lease_pid_liveness_fallback]]) |

---

## Success Criteria

- A reviewer reading this document can determine, for any given step, exactly what state to check to confirm success or failure.
- An operator reading this document can identify, for any failed or filtered step (including canary-skipped rows), what compensation occurred, what state was left behind, and what manual action is required.
- A planner implementing a new adapter can determine from the adapter dispatch contracts (Step 3h) what methods are required and which are optional (i.e., compensate() is optional for platforms without DELETE APIs; presence or absence determines whether deletion-based undo is possible).
- All gap entries are actionable: each has enough context for a planning doc to be written without returning to this brainstorm.

---

## Scope Boundaries

- This document describes contracts; it does not prescribe implementation of those contracts.
- The gap registry records recommended directions, not committed work.
- Platform-specific delete API surface coverage is not in scope here; each platform's compensate() implementation would be a separate unit of work.
- The inner sub-steps (3c–3k) model the *per-row* saga; the outer publish-backlinks command lifecycle is modelled only at the 3a–3b level.

---

## Key Decisions

- **Compensating actions for external platform steps = "no programmable compensation" by default**: External platforms do not uniformly expose delete endpoints. The honest contract is to document the gap and prescribe operator runbook as the fallback. Adding `compensate()` to individual adapters is Gap G6 and is a separate unit of work.
- **Retry policy described as-is, gaps flagged**: We do not prescribe retry policy changes in this document. The 5xx no-retry decision (Gap G5) is documented as deliberate (idempotency risk) with a path to improvement (dedup-gate precondition).
- **Dedup state as the saga's truth record**: The dedup store is the closest thing to a saga log. Gaps G7 and G3 both suggest extending it; those are the highest-value gap closes.

---

## Outstanding Questions

### Deferred to Planning

- [Affects G6][Needs research] Which platforms currently expose a DELETE endpoint with usable auth? Medium does (`DELETE /v1/posts/{postId}`). What about Substack, Hashnode, Dev.to? Research needed before `compensate()` method is designed.
- [Affects G7][Technical] Should `unverified` be a terminal state in the dedup store, or a separate verification-tracking table? Depends on whether unverified rows should be re-attempted or only re-verified.
- [Affects G5][Technical] Enforce-mode 5xx retry: should the retry happen at the adapter level (retry.py) or at the saga coordinator level (publish loop)? The distinction matters for adapter unit tests.

## Next Steps

→ `/ce:plan` for structured implementation planning
