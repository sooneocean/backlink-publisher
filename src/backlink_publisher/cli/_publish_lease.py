"""Lease management helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides publish-lease acquisition and release for concurrency protection.
"""

from __future__ import annotations

import os
from typing import Any


def _gate_banner_sentinel() -> Any:
    """Lazy resolver for the gate-banner sentinel path.

    Uses the env-aware ``_cache_dir()`` so the path lands in the test
    sandbox (not real ``~/.cache``) when ``BACKLINK_PUBLISHER_CACHE_DIR``
    is set.  Mirrors the ``frw_token_path()`` pattern in ``_util/secrets.py``.
    """
    from backlink_publisher import config as _cfg

    return _cfg._cache_dir() / "backlink-publisher" / "v0.3-gate-banner-seen"


_GATE_BANNER_TEXT_TEMPLATE = (
    "publish-backlinks now performs a publish-time reachability re-check "
    "on every row before dispatch. Use --skip-publish-time-check to "
    "restore prior behavior. This message will not repeat (sentinel: {sentinel})."
)


def _release_acquired_leases(store: Any, acquired: list[str], pid: int) -> None:
    from backlink_publisher._util.logger import publish_logger
    for plat in acquired:
        try:
            store.release_lease(plat, pid)
        except Exception as e:
            publish_logger.warning(f"Failed to release lease on {plat!r}: {e}")


def _acquire_publish_leases(platforms: set[str], dry_run: bool) -> None:
    if dry_run or not platforms:
        return

    import atexit
    from backlink_publisher.events.store import EventStore
    from backlink_publisher._util.errors import emit_error

    store = EventStore()
    pid = os.getpid()
    acquired = []

    for plat in sorted(platforms):
        if store.acquire_lease(plat, pid, ttl_seconds=3600):
            acquired.append(plat)
        else:
            _release_acquired_leases(store, acquired, pid)
            lease_details = store.get_lease(plat)
            owner_info = f"PID {lease_details['owner_pid']}" if lease_details else "unknown"
            emit_error(
                f"error: another publish process ({owner_info}) is currently active for platform {plat!r}. "
                "Aborting to prevent concurrent publishing conflicts.",
                exit_code=3,
            )

    atexit.register(_release_acquired_leases, store, acquired, pid)


def _maybe_emit_gate_banner(skip_flag: bool) -> None:
    from backlink_publisher._util.logger import publish_logger
    sentinel = _gate_banner_sentinel()
    if skip_flag or sentinel.exists():
        return
    publish_logger.warn(_GATE_BANNER_TEXT_TEMPLATE.format(sentinel=sentinel))
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch(exist_ok=True)
    except OSError:
        pass


def _check_token_drift(initial_revs: dict[str, int]) -> None:
    from backlink_publisher.config import snapshot_token_revs
    from backlink_publisher._util.errors import emit_error

    # Re-scan only the platforms present at run-start: the comparison below
    # only inspects keys in initial_revs, so reading the other (unbound) token
    # files every row was pure waste (10xN opens+parses on the publish path).
    # A credential file CREATED mid-run is intentionally not tracked — it was
    # never in initial_revs; only rotation/revocation of an already-bound
    # platform aborts the run.
    current = snapshot_token_revs(initial_revs.keys())
    for plat, init_rev in initial_revs.items():
        if current.get(plat, 0) != init_rev:
            emit_error(
                f"error: configuration for platform {plat!r} was updated mid-run. "
                "Aborting to prevent using revoked credentials.",
                exit_code=3,
            )