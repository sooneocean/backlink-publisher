---
title: "feat: Publish Reliability Policy Layer — Browser-Tier Hardening"
type: feat
status: completed
shipped: caa03d6 (#285)
date: 2026-05-28
origin: docs/brainstorms/2026-05-27-publish-reliability-policy-requirements.md
claims: {}  # opt-out: no unmerged SHAs; PR #279 (dedup) already merged
---

# feat: Publish Reliability Policy Layer — Browser-Tier Hardening

## Overview

Browser-tier publish has four distinct failure modes — session death, account
ban, browser crash, transient 5xx — that currently cause **silent drops** with
no circuit-breaking, no per-run observability, and no coordinated policy.  20+
HTTP API channels are unaffected; the pain concentrates on 4 browser-tier
channels (Medium, Velog, Devto, Mastodon).

This plan adds a thin **coordinated policy layer** between the publish-loop
dispatcher and the adapter chain.  The six primitives (observability, health
gate, circuit breaker, throttle, dedup, retry) must not be patched
independently — retry feeding a banned account and retry causing duplicate
posts are the canonical harm cases.

PR #279 (cross-run idempotency / dedup) is **already merged** — the dedup
gate is wired in `cli/_dedup_gate.py` and `publish_backlinks.py`.  This plan
adds the missing pieces: observability, circuit breaker, and a thin policy
wrapper that coordinates them at the right seam.

### Key decisions (from brainstorm + 6-persona review)

1. **v1 browser tier only** — architecture is channel-agnostic, but activation
   gated on `_is_browser_tier(platform)`.  20 HTTP API channels skip the
   policy layer entirely.

2. **Minimal circuit breaker** — fixed cooldown interval; first confirmed ban
   (`AuthExpiredError` with ban-class signal) trips the breaker and blocks
   that channel for the duration.  Half-open probing deferred.

3. **Dedup already handled** — PR #279 (`_dedup_gate.py`) covers cross-run
   duplicate prevention.  This plan does NOT add a second dedup layer.

4. **Phase order: R16 → B → C → (D already merged)** — observability baseline
   before behavioral changes; health gate before circuit breaker.

### Seam diagram

```
publish_backlinks.py:297   (fresh, real)      ─┐
cli/_resume.py:260         (resume)            ─┤─ publish_with_policy() ── adapter_publish()
                                                │
publish_backlinks.py:233   (dry-run)           ─┘─ adapter_publish() direct (transparent, no policy)
```

`publish_with_policy` is the new seam.  The dry-run call at line 233
deliberately bypasses policy — no circuit accounting, no health gating —
because dry-run is a read-only preview.

### Medium adapter chain depth

Medium is a 3-adapter fallback chain: `MediumAPI → MediumBrave → MediumBrowser`.
One `publish_with_policy("medium", …)` call walks the full chain through
`adapter_publish` / `dispatch()`.  The circuit breaker is keyed on
`"medium"` (the platform), not on the specific adapter that fires.

### State storage: flock-across-RMW (NOT JsonStore)

`JsonStore.update` uses a `threading.Lock` (`webui_store/base.py:78`).
`threading.Lock` does **not** cross OS-process boundaries.  The
`publish-backlinks` CLI is a standalone OS process; simultaneous runs (two
terminal windows, a WebUI-triggered run + CLI run) share nothing in memory.

**Required pattern**: flock-across-RMW — identical to `velog_graphql._acquire_lock`
(lines 322–353):

```python
fd = _acquire_lock(lock_path)          # fcntl.LOCK_EX, 60-s poll
try:
    state = _read_state(state_path)    # read inside lock
    # ... check + mutate ...
    _write_state(state_path, state)    # write inside lock
finally:
    _release_lock(fd)
```

The lock file (`.lock`) guards the read→check→write critical section.
The state file (`.json`) holds the data.  The two are separate.

### Fail directions

| Gate | Corruption/missing file | Direction | Rationale |
|---|---|---|---|
| **Circuit breaker** | state file corrupt / unreadable | **fail-CLOSED** (treat as tripped) | Safety mechanism; corrupt state = unknown exposure → block |
| **Health gate** | `channel-status.json` JSONDecodeError | **fail-CLOSED** (already implemented) | `JsonStore.load` catches `JSONDecodeError`, returns `{}`, channels read as `"unbound"` → blocked |
| **Dedup gate** | `dedup.db` read error | **fail-CLOSED** (already implemented by #279) | `_dedup_gate.py` is fail-CLOSED on store errors |

The health gate's existing fail-CLOSED behaviour is **correct and must be
preserved** — the plan does NOT change `channel_status.py`.  The circuit
breaker state file must implement its own fail-CLOSED logic (corrupt →
treat as all channels tripped).

Note: `FileNotFoundError` on `channel-status.json` is **NOT** an error —
it means the user has never bound any channel and the gate correctly blocks.
Only `JSONDecodeError` (file exists but corrupted) is the corruption case.

---

## Requirements Trace

### Observability (R16)

- R1. Emit a structured `publish_attempt` event on every dispatch through the
  policy layer: platform, outcome class (`success` / `auth_expired` /
  `auth_banned` / `external_error` / `transient`), duration_ms, run_id.
  → Unit 1

- R2. Browser-tier only in v1; HTTP API channels pass through without emission.
  → Unit 1

- R3. Events written to stderr (structured JSON, `opencli_logger`) so they are
  captured by WebUI's `subprocess.PIPE stderr` and appear in history.
  → Unit 1

### Policy Layer (B — health gate wire-in)

- R4. New module `src/backlink_publisher/publishing/reliability/policy.py`
  exposes `publish_with_policy(platform, payload, config, *, dry_run=False)`.
  → Unit 2

- R5. `publish_with_policy` checks `channel_status.get_status(platform)` before
  dispatching.  Status `"unbound"` or `"expired"` → return a SKIP sentinel
  (`AdapterResult(status="skipped_policy", …)`).  The health gate is already
  fail-CLOSED; `publish_with_policy` does not re-implement it.
  → Unit 2

- R6. Wire the two real call sites:
  `publish_backlinks.py:297` (fresh) and `cli/_resume.py:260` (resume).
  The dry-run call at `:233` remains `adapter_publish(…, dry_run=True)` —
  no policy wrapper.
  → Unit 2

- R7. `_is_browser_tier(platform) → bool` helper in `policy.py`; currently
  returns True for `{"medium", "velog", "devto", "mastodon"}`.  HTTP API
  channels return False and skip the policy layer.
  → Unit 2

- R8. `publish_with_policy` with a non-browser-tier platform delegates
  directly to `adapter_publish` (no health gate, no circuit check, no event
  emission) — preserving the existing behaviour exactly.
  → Unit 2

### Circuit Breaker (C)

- R9. State file: `<config_dir>/publish-circuit-state.json`.  Lock file:
  `<config_dir>/publish-circuit-state.lock` (flock, 0600, `LOCK_EX`).
  → Unit 3

- R10. State schema: `{platform: {tripped: bool, tripped_at_iso: str|null,
  cooldown_s: int}}`.  Default cooldown: 300 s (configurable via env var
  `BACKLINK_PUBLISHER_CIRCUIT_COOLDOWN_S`; default 300).
  → Unit 3

- R11. Trip condition (v1): `AuthExpiredError` whose message contains
  `"ban"` or `"banned"` or `"suspended"` (case-insensitive).  Plain
  session-expiry (`AuthExpiredError` without ban signal) does **not** trip
  the breaker.
  → Unit 3

- R12. Tripped breaker → return `AdapterResult(status="skipped_circuit_open",
  …)` without dispatching.  The channel stays skipped for `cooldown_s`
  seconds after `tripped_at_iso`.  After the cooldown the breaker
  auto-resets to `tripped=False` on the next attempt (no half-open state).
  → Unit 3

- R13. On corruption (JSONDecodeError / any read failure): treat all platforms
  as tripped (fail-CLOSED).  Log the corruption at WARNING level.
  → Unit 3

- R14. The flock covers the entire read→check→(maybe-trip)→write cycle,
  matching the `velog_graphql._acquire_lock` pattern.  Lock timeout 60 s
  (raise `ExternalServiceError` on timeout — do not deadlock).
  → Unit 3

- R15. `reset_circuit(platform, config)` public function for operator use /
  testing.  → Unit 3

### Tests

- R16. Unit tests for `publish_with_policy` using mock `adapter_publish`:
  health-gate skip (unbound channel), circuit-open skip, successful dispatch,
  non-browser-tier passthrough.  → Units 2–3

- R17. Circuit breaker tests: trip on ban signal, cooldown expiry resets,
  fail-CLOSED on corruption, flock re-entrancy under `threading.Barrier`
  (concurrent single-process).  → Unit 3

- R18. Event emission test: correct fields emitted on success and on each
  error class.  → Unit 1

---

## Context

### Codebase anchor points

| Symbol | Path | Role |
|---|---|---|
| `adapter_publish` | `publishing/adapters/__init__.py:publish` | adapter chain entry point |
| `dispatch()` | `publishing/_registry_dispatch.py` | walks fallback chain |
| `get_status()` | `webui_store/channel_status.py:175` | health gate read |
| `_acquire_lock` | `publishing/adapters/velog_graphql.py:322` | flock pattern to copy |
| `_dedup_gate.py` | `cli/_dedup_gate.py` | dedup (PR #279, already wired) |
| `ErrorClass` | `publishing/adapters/retry.py:ErrorClass` | error taxonomy |
| `AuthExpiredError` | `_util/errors.py` | session / ban exception |
| `ExternalServiceError` | `_util/errors.py` | adapter crash / infra failure |

### Retry interaction

`retry_transient_call` fires **inside each adapter**, below `dispatch()`.
It only retries HTTP 429 (rate-limit); it never retries `AuthExpiredError` or
`ExternalServiceError`.  The circuit breaker at the policy layer fires **above
`dispatch()`**, so it sees the final outcome after internal retries complete.
There is no retry-vs-circuit race for v1 (ban is not a retried error class).

### Monolith budget

`publish_backlinks.py` ceiling: **370 SLOC** (`monolith_budget.toml`).
The wiring change in Unit 2 replaces one `adapter_publish(…)` call with
`publish_with_policy(…, dry_run=False)` — net SLOC delta ≈ +0 to +5.  No
budget bump expected.  New `publishing/reliability/` module starts unbudgeted;
add an entry if it exceeds 200 SLOC.

### test_r9_extension_readiness constraint

`tests/test_r9_extension_readiness.py` enforces that `cli/*.py` and
`schema.py` are NOT edited when adding a new publishing platform.  This plan
adds a reliability module, not a new platform, so the constraint does not apply.
The policy layer wiring touches `publish_backlinks.py` and `cli/_resume.py`,
which is explicitly allowed for non-platform additions.

---

## Scope Boundaries

- No retry logic changes (retry lives inside adapters; out of scope for v1).
- No half-open circuit breaker (cooldown-only v1; half-open deferred).
- No HTTP API channels in v1 (browser-tier activation only).
- No cross-run dedup changes (PR #279 already handles this).
- No `channel_status.py` behaviour changes (health gate is already correct).
- No new CLI entrypoints (policy layer is internal).

---

## Units

> **Gate:** Units are sequential — do not start Unit N+1 before Unit N's tests
> pass.  Unit 2 depends on Unit 1's event module existing.  Unit 3 extends
> `publish_with_policy` built in Unit 2.

---

### Unit 1 — R16 Observability Baseline ✅ not started

**Goal:** Emit structured `publish_attempt` events from the policy layer for
every browser-tier dispatch so there is a baseline signal before behavioral
changes in Units 2–3.

**New files:**
- `src/backlink_publisher/publishing/reliability/__init__.py` (empty)
- `src/backlink_publisher/publishing/reliability/events.py`

**`events.py` public API:**

```python
from backlink_publisher.publishing.reliability.events import (
    emit_attempt,   # emit_attempt(platform, outcome, duration_ms, run_id)
    Outcome,        # "success" | "auth_expired" | "auth_banned"
                    # | "external_error" | "transient"
)
```

`emit_attempt` writes one JSON line to `opencli_logger` (structured, same
pattern as `_publish_helpers.py` log calls).  It is a pure side-effect
function — no return value, never raises.

**Tests:** `tests/test_reliability_events.py` — assert correct fields emitted,
assert no exception on every Outcome value.

---

### Unit 2 — Policy Layer + Health Gate ✅ not started

**Goal:** Introduce `publish_with_policy` and wire it into the two real
call sites.  Dry-run remains unwrapped.

**New file:** `src/backlink_publisher/publishing/reliability/policy.py`

**Key functions:**

```python
def _is_browser_tier(platform: str) -> bool:
    return platform in {"medium", "velog", "devto", "mastodon"}

def publish_with_policy(
    platform: str,
    payload: dict,
    config: Config,
    *,
    dry_run: bool = False,
    banner_emit=None,
    mode: str = "draft",
) -> AdapterResult:
    """Policy wrapper around adapter_publish for browser-tier channels.

    Non-browser-tier: delegates to adapter_publish() directly (no policy).
    Dry-run: must NOT be routed here (caller's responsibility).
    """
    if not _is_browser_tier(platform):
        return adapter_publish(payload=payload, mode=mode, config=config,
                               dry_run=dry_run, banner_emit=banner_emit)

    # 1. Health gate (already fail-CLOSED in channel_status.py)
    from webui_store.channel_status import get_status
    status_info = get_status(platform)
    if status_info.get("status") not in ("bound",):
        return AdapterResult(status="skipped_policy", ...)

    # 2. Circuit breaker (Unit 3 adds this block)

    # 3. Dispatch + observe
    t0 = time.monotonic()
    try:
        result = adapter_publish(payload=payload, mode=mode, config=config,
                                 dry_run=False, banner_emit=banner_emit)
        emit_attempt(platform, Outcome.SUCCESS, ...)
        return result
    except AuthExpiredError as exc:
        emit_attempt(platform, Outcome.AUTH_EXPIRED, ...)
        raise
    except ExternalServiceError as exc:
        emit_attempt(platform, Outcome.EXTERNAL_ERROR, ...)
        raise
```

**Wiring in `publish_backlinks.py`:**
- Line ~297: replace `adapter_publish(payload=..., mode=mode, config=config, dry_run=False, banner_emit=...)` with `publish_with_policy(platform, payload=..., config=config, mode=mode, banner_emit=...)`
- Line ~233 (dry-run): **unchanged** — keep `adapter_publish(…, dry_run=True)` direct call

**Wiring in `cli/_resume.py`:**
- Line ~260: same replacement as above

**Tests:** `tests/test_reliability_policy.py`
- `test_non_browser_tier_passthrough` — assert `adapter_publish` called directly
- `test_health_gate_blocks_unbound` — mock `get_status` → `"unbound"` → skipped
- `test_health_gate_allows_bound` — mock `get_status` → `"bound"` → dispatches
- `test_event_emitted_on_success` — assert `emit_attempt` called with `SUCCESS`
- `test_event_emitted_on_auth_expired` — assert `emit_attempt` + exception propagates

---

### Unit 3 — Minimal Circuit Breaker ✅ not started

**Goal:** Add flock-based per-channel circuit breaker state.  Trip on confirmed
ban; auto-reset after cooldown.  Fail-CLOSED on corruption.

**New file:** `src/backlink_publisher/publishing/reliability/circuit.py`

**Key functions:**

```python
def is_tripped(platform: str, config: Config) -> bool:
    """Return True if platform's circuit is open (tripped and within cooldown).
    On any read error, returns True (fail-CLOSED)."""

def trip(platform: str, config: Config) -> None:
    """Trip circuit for platform. flock-across-RMW."""

def reset_circuit(platform: str, config: Config) -> None:
    """Reset tripped circuit (operator / test use). flock-across-RMW."""

def _is_ban_signal(exc: AuthExpiredError) -> bool:
    msg = str(exc).lower()
    return any(w in msg for w in ("ban", "banned", "suspended"))
```

**State files (both at `config.config_dir`):**
- `publish-circuit-state.json` — `{platform: {tripped: bool, tripped_at_iso: str|null}}`
- `publish-circuit-state.lock` — flock sentinel (never read; just held during RMW)

**Cooldown:** `int(os.environ.get("BACKLINK_PUBLISHER_CIRCUIT_COOLDOWN_S", "300"))`

**Wiring into `policy.py` (Unit 2 skeleton → fill in block 2):**

```python
# 2. Circuit breaker
if is_tripped(platform, config):
    return AdapterResult(status="skipped_circuit_open", ...)
try:
    result = adapter_publish(...)
    ...
except AuthExpiredError as exc:
    if _is_ban_signal(exc):
        trip(platform, config)
    emit_attempt(platform, Outcome.AUTH_BANNED if _is_ban_signal(exc) else Outcome.AUTH_EXPIRED, ...)
    raise
```

**flock pattern** (from `velog_graphql._acquire_lock`):

```python
import fcntl, json, os, time
from pathlib import Path

def _flock_rmw(lock_path: Path, state_path: Path, mutate_fn):
    """Read-modify-write under LOCK_EX flock. mutate_fn(state) -> new_state."""
    fd = _acquire_lock(lock_path)  # raises ExternalServiceError on 60-s timeout
    try:
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
        except (json.JSONDecodeError, OSError):
            state = {}  # corruption → empty state; caller interprets as tripped
        new_state = mutate_fn(state)
        state_path.write_text(json.dumps(new_state, indent=2))
    finally:
        _release_lock(fd)
```

`is_tripped()` does NOT mutate — it uses a separate read-only path that also
returns True on any exception (fail-CLOSED).

**Tests:** `tests/test_reliability_circuit.py`
- `test_not_tripped_by_default`
- `test_trip_sets_tripped`
- `test_cooldown_blocks_after_trip`
- `test_cooldown_auto_reset_after_expiry` (mock `time.time`)
- `test_fail_closed_on_corrupt_state` (write garbage to state file)
- `test_reset_clears_trip`
- `test_ban_signal_detection` — "banned", "suspended", "Ban Account" → True; plain expiry → False
- `test_policy_trips_on_ban_signal` (integration via `publish_with_policy`)
- `test_policy_skips_on_open_circuit`
- `test_concurrent_trip_barrier` — `threading.Barrier(2)` races two trips, assert state consistent

---

## Deferred

- **Half-open circuit breaker**: cooldown resets automatically; no probe-and-close logic.
- **Per-account keying**: single `"default"` account today; key is just platform.
- **Throttle integration**: `MEDIUM_THROTTLE_MIN/MAX` and `velog` daily cap
  already live in their adapters; the policy layer does not duplicate them.
- **Non-browser-tier circuit breaking**: out of scope for v1.
- **HTTP-level 5xx circuit**: the `ExternalServiceError` path emits an event
  but does not trip the breaker (only confirmed ban trips it in v1).
