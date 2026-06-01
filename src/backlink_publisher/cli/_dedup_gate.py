"""Dedup recording + gate hooks for the publish path.

Recording (U2, observe): ``record_intent`` / ``record_done`` / ``record_failure``
write dedup state on every dispatch across **both** the fresh (``publish_backlinks``)
and resume (``_resume``) seams. The gate (U7): ``gate`` / ``gate_with_force`` decide
skip/hold/dispatch in enforce mode, ``enforce_enabled`` / ``enforce_precondition_or_exit``
gate the flip. The single funnel keeps both seams identical.

All **recording** is observe-safe: a dedup-store failure is logged and swallowed so
it can never break a publish run. The intent write runs before dispatch (so a crash
leaves ``attempting``); the terminal write runs on the dispatch outcome. The
**enforce gate** is fail-CLOSED instead: a store error holds the row (never a
possible double-post).

Failure → state mapping (R8, conservative): only ``http_5xx`` (the may-have-committed
class) maps to ``uncertain``; every other error class is ``failed`` (re-publishable).
``classify_exception`` is message-based and cannot truly see send-state, so this is a
conservative hold, not a precise one. ``uncertain`` is never auto-downgraded to
``failed`` (a later non-5xx failure can't disprove the original may-have-landed).

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U2, U7).
"""

from __future__ import annotations

import os
from typing import Any

from ..idempotency import DedupKey, DedupRecord, DedupStore
from .._util.logger import get_logger

#: Single account per channel today; carried in the key so a future second
#: account on the same platform is a distinct key (see plan Key Decisions).
_ACCOUNT_DEFAULT = "default"

#: Phase-B switch (R18). Unset/anything-but-"1" = observe (record only, dispatch
#: everything). "1" = enforce (the gate decides skip/hold/dispatch). Strict "1"
#: match mirrors BACKLINK_PUBLISHER_ALLOW_NETWORK — a behavior-changing switch
#: should not flip on an accidental truthy-ish value.
ENFORCE_ENV = "BACKLINK_PUBLISHER_DEDUP_ENFORCE"

_log = get_logger("dedup")


def enforce_enabled() -> bool:
    """True iff the operator opted into Phase-B enforce."""
    return os.environ.get(ENFORCE_ENV) == "1"


def enforce_precondition_or_exit() -> None:
    """R19b: when enforce is on, refuse to publish until the dedup store covers
    the back-catalogue (else an already-live, unrecognized post would re-publish).
    No-op in observe mode. Exits 3 (operator action required) with counts only —
    never campaign URLs. Read the readiness with ``--check-enforce-readiness``."""
    if not enforce_enabled():
        return
    from ..idempotency.reconcile import ACK_QUARANTINE_ENV, check_enforce_readiness
    from .._util.errors import emit_error

    r = check_enforce_readiness()
    if r.ok:
        _log.info(
            f"enforce precondition OK: {r.covered_count}/{r.event_key_count} "
            f"published key(s) covered; quarantine={r.quarantine_count}"
        )
        return
    parts: list[str] = []
    if r.missing_count:
        parts.append(
            f"{r.missing_count} published key(s) absent from the dedup store "
            "— run `publish-backlinks --backfill-dedup`"
        )
    if r.quarantine_count and not r.quarantine_acknowledged:
        parts.append(
            f"{r.quarantine_count} event(s) have unmappable/retired adapter "
            f"strings — set {ACK_QUARANTINE_ENV}=1 to acknowledge"
        )
    emit_error(
        "enforce blocked — dedup store not ready: " + "; ".join(parts),
        exit_code=3,
    )


def _key_for_row(row: dict[str, Any], platform: str) -> DedupKey | None:
    target = (row or {}).get("target_url")
    if not target or not platform:
        return None
    try:
        return DedupKey(platform=platform, target_url=str(target), account=_ACCOUNT_DEFAULT)
    except Exception:  # canonicalization on a malformed URL must not break publish
        return None


def is_crashed_in_flight(
    row: dict[str, Any], platform: str, *, store: DedupStore | None = None
) -> bool:
    """True iff this row's dedup key is a stale ``attempting`` row.

    A stale ``attempting`` means a prior run died mid-dispatch for this key (its
    owner PID is gone, or the row aged past the TTL backstop). Because
    :func:`record_intent` writes ``attempting`` BEFORE the post is created, the
    post MAY already be live — the crash window is indistinguishable from a
    crash-before-post. Resume uses this to print the same "verify before
    resuming" warning it already prints for ``http_5xx`` checkpoint items, so a
    hard-crash (which never set an ``error_class``) is no longer silent.

    Pass ``store`` to reuse one :class:`DedupStore` across a loop (resume checks
    every to-process item) instead of opening a fresh connection per call.

    Observe-safe: an unusable key, missing record, or any store error returns
    False — a pure read for an advisory warning that must never break a resume."""
    key = _key_for_row(row, platform)
    if key is None:
        return False
    try:
        store = store or DedupStore()
        rec = store.get(key)
        return rec is not None and store.is_stale_attempting(rec)
    except Exception as exc:  # advisory-only: never break the run
        _log.debug(f"dedup crashed-in-flight check skipped: {exc}")
        return False


def terminal_for_error_class(error_class: str | None) -> str:
    """``http_5xx`` may have committed server-side → hold (``uncertain``);
    everything else is confirmed-not-landed → ``failed`` (re-publishable)."""
    return "uncertain" if error_class == "http_5xx" else "failed"


def record_intent(row: dict[str, Any], platform: str, *, run_id: str | None) -> None:
    """Observe-safe ``absent -> attempting`` before dispatch. A lost race (key
    already present) is a no-op here; the terminal recorder handles existing rows."""
    key = _key_for_row(row, platform)
    if key is None:
        return
    try:
        DedupStore().intent_write(
            key, run_id=run_id, owner_pid=os.getpid(), owner_run_id=run_id
        )
    except Exception as exc:  # observe-only: never break the run
        _log.debug(f"dedup intent_write skipped: {exc}")


def gate(
    row: dict[str, Any], platform: str, *, run_id: str | None, force: bool = False
) -> tuple[str, DedupRecord | None]:
    """Single pre-dispatch funnel for BOTH seams. Returns ``(verdict, record)``
    where verdict is ``dispatch``/``skip``/``hold``/``conflict``.

    **Observe** (default): records intent (``absent -> attempting``, best-effort)
    and always returns ``dispatch`` — publish behavior is unchanged.

    **Enforce** (``BACKLINK_PUBLISHER_DEDUP_ENFORCE=1``): the atomic
    :meth:`DedupStore.gate_and_claim` decides — ``done`` skips, ``uncertain`` /
    live-``attempting`` holds, ``absent`` / ``failed`` / stale-``attempting`` is
    claimed and dispatched.

    ``force=True`` (an honored manifest force-flag, U7c) overrides a ``uncertain``
    hold (reclaim + dispatch) but turns a live ``done`` into a ``conflict`` verdict
    (R11: forcing an already-live key would double-post — rejected, not claimed).

    Fail-closed: if the enforce gate cannot read/claim the store, it HOLDS the
    row (never dispatches) — the operator opted into strong dedup, so a store
    fault must not silently degrade to a possible double-post. (Observe stays
    fail-open: it swallows and dispatches.)"""
    key = _key_for_row(row, platform)
    if key is None:
        # No usable key (missing platform/target) — cannot dedup; always dispatch.
        return "dispatch", None

    if not enforce_enabled():
        record_intent(row, platform, run_id=run_id)
        return "dispatch", None

    try:
        decision = DedupStore().gate_and_claim(
            key, run_id=run_id, owner_pid=os.getpid(), owner_run_id=run_id,
            force=force,
        )
        return decision.verdict, decision.record
    except Exception as exc:  # enforce: fail-closed (hold), never silent double-post
        _log.error(f"dedup enforce gate error — holding row (fail-closed): {exc}")
        return "hold", None


def gate_with_force(
    row: dict[str, Any],
    platform: str,
    *,
    run_id: str | None,
    forced_keys: set | None,
    reason: str | None,
) -> tuple[str, DedupRecord | None]:
    """Gate wrapper applying manifest force-flags (U7c). If this row's key is in
    ``forced_keys``, force the gate (override a uncertain hold). A force on a live
    ``done`` key surfaces a conflict and aborts the run (R11, exit 1). An honored
    force writes a ``--forget``-parity audit entry. Returns ``(verdict, record)``;
    in observe mode (``forced_keys`` empty) this is just :func:`gate`."""
    key = _key_for_row(row, platform)
    force = bool(forced_keys) and key is not None and key.as_tuple() in forced_keys
    verdict, drec = gate(row, platform, run_id=run_id, force=force)
    if verdict == "conflict":
        from .._util.errors import emit_error

        emit_error(
            f"force-manifest conflict: {platform} key is already published "
            "(done); refusing to re-publish — use --forget if truly intended",
            exit_code=1,
        )
    if force and verdict == "dispatch" and key is not None:
        from ..idempotency import audit_log

        audit_log.append_entry(
            action="force", platform=key.platform, target_url=key.target_url,
            account=key.account, from_state=(drec.state if drec else "absent"),
            to_state="attempting", reason=reason, run_id=run_id,
        )
    return verdict, drec


def record_done(
    row: dict[str, Any],
    platform: str,
    *,
    live_url: str | None,
    verify_ok: bool,
    run_id: str | None,
) -> None:
    """Observe-safe terminal ``done`` write on a successful dispatch."""
    _record_terminal(row, platform, "done", live_url=live_url, verify_ok=verify_ok, run_id=run_id)


def record_failure(
    row: dict[str, Any],
    platform: str,
    *,
    error_class: str | None,
    run_id: str | None,
) -> None:
    """Observe-safe terminal ``failed``/``uncertain`` write on a failed dispatch."""
    _record_terminal(
        row, platform, terminal_for_error_class(error_class), run_id=run_id
    )


def _record_terminal(
    row: dict[str, Any],
    platform: str,
    state: str,
    *,
    live_url: str | None = None,
    verify_ok: bool | None = None,
    run_id: str | None = None,
) -> None:
    key = _key_for_row(row, platform)
    if key is None:
        return
    try:
        store = DedupStore()
        rec = store.get(key)
        if rec is None:
            # No intent row (intent write lost/failed) — observe-only, skip rather
            # than fabricate a row out of band.
            return
        if rec.state == "done":
            # Already confirmed success — immutable. Do not re-transition.
            return
        if rec.state == "uncertain" and state == "failed":
            # Never DOWNGRADE a held key to re-publishable: `uncertain` means a
            # prior attempt's 5xx may have committed server-side. A later non-5xx
            # failure on a re-dispatch does not prove the original didn't land, so
            # demoting to `failed` would let enforce re-publish a possibly-live
            # post. Hold it (adjudicate via --adjudicate-uncertain). uncertain ->
            # done is still allowed (a confirmed success settles the key).
            return
        # "failed" → "done" is a valid path (policy-skip on fresh run, then
        # operator fixes the channel binding and runs --resume successfully).
        # allow_from_terminal=True is required because store._TERMINAL includes
        # "failed"; without it, transition raises ValueError (swallowed silently
        # by the except arm below), leaving the key permanently at "failed".
        if rec.state == "failed" and state != "done":
            return  # failed→anything-except-done: already terminal, no-op
        allow_failed_to_done = rec.state == "failed"
        store.transition(
            key, state, live_url=live_url, verify_ok=verify_ok, run_id=run_id,
            allow_from_terminal=allow_failed_to_done,
        )
    except Exception as exc:  # observe-only: never break the run
        _log.debug(f"dedup terminal write skipped ({state}): {exc}")
