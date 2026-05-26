---
title: "Read-side projector silently dropped successes — status-vocabulary drift at the input-classification seam"
date: 2026-05-26
category: logic-errors
module: backlink-publisher / events_projection
problem_type: logic_error
component: service_object
symptoms:
  - "events.db (the read-side state-of-truth) was missing real publish successes that the source state files clearly recorded"
  - "Downstream readers (ledger, health dashboard, footprint) under-reported confirmed publishes with no error, no warning, and a green test suite"
  - "The gap only appeared once production began writing a status string the projector's classifier had never been taught"
root_cause: logic_error
resolution_type: code_fix
severity: high
related_components:
  - database
  - testing_framework
tags:
  - silent-drop
  - classification-contract
  - events-db
  - quarantine
  - wal-deadlock
  - input-seam
  - state-of-truth
  - projector
---

# Read-side projector silently dropped successes — status-vocabulary drift at the input-classification seam

## Problem

The events projector reduces JSON state files (checkpoints, history, drafts) into `events.db`, the read-side state-of-truth that the ledger, health dashboard, and footprint all query. It silently dropped real CLI publish successes: production checkpoints wrote the terminal status `"done"`, but the projector's classifier only recognized `"succeeded"`. The unrecognized status fell through a silent `else` branch and never became an event — so confirmed publishes vanished from the backbone with zero signal.

## Symptoms

- `events.db` under-counted confirmed publishes versus what the source checkpoint files plainly recorded.
- Every downstream reader inherited the undercount; none raised.
- The full test suite stayed green — no test asserted that an unrecognized source status survives projection as *something* (an event or a visible quarantine).
- The defect was latent until the source vocabulary drifted (`succeeded` → `done`); nothing failed at the moment the new status was introduced upstream.

## What Didn't Work

- **Treating it as an output-contract problem.** The instinct was to pin the set of `kind` strings the projector *emits* (the output seam). That alone would not have prevented this bug: the drop happened at the **input-classification seam** — the mapping from *source-record status* to outcome — before any kind was chosen. An output-only registry leaves the input vocabulary unguarded.
- **Blanket "convert every silent `else` to an error/quarantine."** Some `else` branches are *intentional* no-ops (e.g. the history and drafts reducers deliberately skip records they do not own). Quarantining those would have produced a flood of false positives. The fix needed a way to distinguish "unrecognized — make noise" from "recognized as deliberately ignored."

## Solution

Introduce a dependency-free registry (`events/kinds.py`) and route every reducer's status handling through one classifier with a **three-outcome** contract:

```
classify(source_type, status) ->
    kind            # a registered event kind
  | NO_EMIT         # recognized, deliberately produces no event (e.g. non-owned record)
  | QUARANTINE      # UNRECOGNIZED status from an authoritative source — never drop
```

- **Unknown status from an authoritative source quarantines, never drops.** Unrecognized statuses are written to a `quarantine_log` row (quarantine-and-continue: never silently drop, never halt the run). The write is idempotent via a single NOT-NULL `dedup_key` (sha256 over the identity tuple, with NULL fields folded so rows lacking a run id still dedupe) plus `INSERT OR IGNORE`.
- **`NO_EMIT` makes intentional skips explicit.** Deliberate no-ops are declared in the registry, so the silent `else` is gone but legitimate skips do not generate quarantine noise.
- **Mass-quarantine alarm.** The projection result carries `quarantined` and `records_considered` counters; when the quarantine ratio crosses a threshold the run records a `degraded` health flag, so a flood of unknown statuses cannot pass as a clean run.
- **CI gates lock the vocabulary**, so the contract cannot silently rot again:
  - a literal-ban AST scan (writers must pass a registry symbol, never a bare string, scoped to the emit/append call sites so `list.append("x")` is not flagged);
  - a bidirectional reader check (a reader may only query registered kinds, and a registered kind a reader omits must be explicitly allowlisted);
  - a status→outcome pinning table.

### WAL nested-connection deadlock (caught during implementation)

The first cut wrote each quarantine immediately from inside the reducer loop. The quarantine writer opened its own connection while the reducer still held the WAL write lock → `database is locked` → the quarantine itself was lost. That would have turned the silent-drop fix back into a silent drop.

Fix: **defer the quarantine writes.** Collect pending quarantines during the loop and flush them only *after* the reducer transaction commits and releases the lock.

```
# during the reducer loop
pending_quarantines.append(record)        # do NOT write here

# after the reducer transaction commits
_write_quarantines(store, pending_quarantines)   # private connection, lock free
```

## Why This Works

The original defect was a **classification gap at the input seam**: a hardcoded set of recognized statuses with a silent fall-through for everything else. Drift in the *upstream* vocabulary (a perfectly reasonable rename on the producer side) silently disabled a path on the *consumer* side. Routing all statuses through one classifier with an explicit third outcome means an unrecognized input can no longer be silently discarded — it is either a known kind, a declared no-op, or a loud quarantine. The CI gates make the registry the single source of truth, so adding a kind or a status is a deliberate, reviewed edit rather than an ambient string literal that can drift out from under a reader.

## Prevention

- **Guard both seams, not just output.** When one component consumes another's vocabulary (statuses, enums, event types), the contract that prevents silent loss lives at the **input-classification** boundary, not only at the output. Pin the mapping *into* your domain, not just the strings you emit.
- **Never let an authoritative-source value hit a silent `else`.** Fall-through on data from a source of record is a silent-data-loss class. Make the outcome one of: a recognized result, an *explicitly declared* no-op, or a loud/quarantined unknown. "Recognized-but-ignored" and "unrecognized" must be different code paths.
- **Test presence *and* absence per path.** A green suite that never asserts "an unknown status still produces a visible row" enshrines the drop. Add a test that feeds an unrecognized status and asserts it is quarantined (not silently absent), plus a mass-quarantine ratio test so a flood degrades health.
- **Write-under-lock check for any nested connection.** Before opening a second connection (or a connection from inside a callback) against a WAL SQLite DB, confirm no enclosing transaction still holds the write lock. If it might, defer the secondary write until after commit.
- This belongs to the repo's recurring "missed one dispatch/classification path" family — see Related Issues. When touching any status/kind classifier, enumerate *every* path and add a per-path test.

## Related Issues

- `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md` — the *output*-seam twin of this *input*-seam bug: a value silently dropped at a serialization/classification seam with no signal. Same prescription (single canonical chokepoint + per-path presence/absence tests).
- `docs/solutions/logic-errors/save-config-write-paths-bypass-preservation-2026-05-15.md` — canonical "silent-drop-by-default on authoritative state" precedent; its structural fix (default drop-unknown → preserve-unknown) mirrors this one's "unknown input must be loud, not skipped."
- `docs/solutions/test-failures/negative-assertion-locks-in-bug-2026-05-15.md` and `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — green tests that enshrined a silent drop because none asserted unknown input survives; motivate the CI status→outcome and bidirectional-reader gates.
- `docs/solutions/logic-errors/invert-drift-check-when-invariant-becomes-dynamic-2026-05-18.md` — mechanics cross-reference for where to place registry/literal-ban assertions (test-time vs module-level) and half-loaded-import hazards in a dependency-free registry.
