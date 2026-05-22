"""Channel binding status store — Plan 2026-05-19-001 Unit 1.

Tracks each browser-binding channel's lifecycle in
``<config_dir>/channel-status.json``.  Singleton is a ``_LazyStore``
proxy (Plan 2026-05-22 P7 C1) so the backing-file path is only resolved
on first access.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS
from backlink_publisher.config.loader import _config_dir
from webui_store.base import JsonStore, _LazyStore


_UNBOUND_DEFAULT: dict[str, Any] = {
    "status": "unbound",
    "bound_at": None,
    "storage_state_path": None,
    "last_verified_at": None,
}


channel_status_store: _LazyStore = _LazyStore(
    lambda: JsonStore(
        _config_dir() / "channel-status.json",
        default_factory=dict,
    )
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_channel(channel: str) -> None:
    if not channel or channel not in CHANNELS:
        raise UsageError(
            f"channel_status: unknown channel {channel!r} "
            f"(allowed: {sorted(CHANNELS)})"
        )


def _validate_storage_state_path(path: Path | str) -> Path:
    """Ensure path resolves inside _config_dir(). Raises UsageError for
    traversal / arbitrary absolute paths."""
    resolved = Path(path).resolve()
    config_root = _config_dir().resolve()
    try:
        resolved.relative_to(config_root)
    except ValueError as exc:
        raise UsageError(
            f"channel_status: storage_state_path {str(path)!r} must resolve "
            f"inside {str(config_root)!r}"
        ) from exc
    return resolved


def mark_bound(channel: str, storage_state_path: Path | str) -> None:
    """Record a successful bind for ``channel``. Validates channel
    whitelist + path locality. Initializes ``last_verified_at`` to
    ``None`` so the next Settings GET runs a fresh liveness probe."""
    _validate_channel(channel)
    resolved_path = _validate_storage_state_path(storage_state_path)

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        current[channel] = {
            "status": "bound",
            "bound_at": _now_iso(),
            "storage_state_path": str(resolved_path),
            "last_verified_at": None,
        }
        return current

    channel_status_store.update(_apply)


def mark_expired(channel: str) -> None:
    """Flip ``channel`` to status=expired. Preserves bound_at +
    storage_state_path so the UI can render 'last bound at YYYY-MM-DD'.
    Clears ``last_verified_at`` so the cached truth doesn't outlive the
    expired transition."""
    _validate_channel(channel)

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(channel, {})
        current[channel] = {
            "status": "expired",
            "bound_at": existing.get("bound_at"),
            "storage_state_path": existing.get("storage_state_path"),
            "last_verified_at": None,
        }
        return current

    channel_status_store.update(_apply)


def mark_verified(channel: str) -> None:
    """Stamp ``last_verified_at = now`` for ``channel`` (Plan 003 Unit 0).

    Called by the liveness probe (Plan 003 Unit 5) after a definite
    LOGGED_IN outcome. Other fields preserved. If the record doesn't
    exist yet (e.g., operator clicks "Verify Now" on an unbound channel),
    a minimal record is created with status remaining ``unbound`` —
    only ``last_verified_at`` carries information.
    """
    _validate_channel(channel)

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(channel, {})
        current[channel] = {
            "status": existing.get("status", "unbound"),
            "bound_at": existing.get("bound_at"),
            "storage_state_path": existing.get("storage_state_path"),
            "last_verified_at": _now_iso(),
        }
        return current

    channel_status_store.update(_apply)


def mark_identity_mismatch(
    channel: str, *, old_account: str, new_account: str
) -> None:
    """Record account-mismatch state for ``channel`` (Plan 003 Unit 0 / R6).

    Operator must explicitly resolve via Settings UI (keep old vs replace
    with new). ``reconcile_on_load`` does NOT demote this state to expired
    even if the underlying storage_state.json file is missing.

    Defensive guards (PR #83 adversarial review findings):
      - Empty / identical account strings are treated as no-ops rather
        than written to disk. ``alice/alice`` is not an identity mismatch;
        rendering the keep/replace UI for it would either confuse the
        operator or cause a destructive "replace" of a valid credential.
      - An existing ``identity_mismatch`` record is not overwritten — the
        first mismatch wins until the operator resolves it. Prevents
        retry loops or duplicate JSONL events from silently mutating the
        recorded accounts mid-resolution.
    """
    _validate_channel(channel)
    if not old_account or not new_account or old_account == new_account:
        return

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(channel, {})
        if existing.get("status") == "identity_mismatch":
            return current
        current[channel] = {
            "status": "identity_mismatch",
            "bound_at": existing.get("bound_at"),
            "storage_state_path": existing.get("storage_state_path"),
            "last_verified_at": existing.get("last_verified_at"),
            "identity_mismatch_old": old_account,
            "identity_mismatch_new": new_account,
        }
        return current

    channel_status_store.update(_apply)


def get_status(channel: str) -> dict[str, Any]:
    """Read API. Unknown channels return the unbound default (no
    KeyError) so UI rendering doesn't have to branch on membership."""
    data = channel_status_store.load() or {}
    rec = data.get(channel)
    if rec is None:
        return dict(_UNBOUND_DEFAULT)
    return rec


def list_all() -> dict[str, dict[str, Any]]:
    """Read API. Returns the full store as a dict."""
    return dict(channel_status_store.load() or {})


def reconcile_on_load() -> None:
    """Demote any bound record whose ``storage_state_path`` is missing
    on disk to status=expired (preserves bound_at + path for UX).

    Called by ``webui_app.create_app`` at startup (single-threaded
    path), not lazy on first access — avoids lazy-init thread races and
    makes the post-startup state strictly consistent with disk.
    """

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        for channel, rec in list(current.items()):
            if not isinstance(rec, dict):
                continue
            # identity_mismatch records require explicit operator resolution
            # (Plan 003 Unit 0 / R6). Reconcile must not auto-demote them.
            if rec.get("status") != "bound":
                continue
            path = rec.get("storage_state_path")
            if not path or not os.path.exists(path):
                current[channel] = {
                    "status": "expired",
                    "bound_at": rec.get("bound_at"),
                    "storage_state_path": rec.get("storage_state_path"),
                    "last_verified_at": None,
                }
        return current

    channel_status_store.update(_apply)


__all__ = [
    "channel_status_store",
    "mark_bound",
    "mark_expired",
    "mark_identity_mismatch",
    "mark_verified",
    "get_status",
    "list_all",
    "reconcile_on_load",
]
