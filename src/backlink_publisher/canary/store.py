"""Canary health store + ``[canary.<platform>]`` config reader (Plan 2026-05-27-001 Unit 1).

Persists per-platform canary health in ``<config_dir>/canary-health.json``,
keyed by registry platform name. Mirrors ``webui_store/channel_status.py``:
a ``_LazyStore`` proxy defers backing-file path resolution to first access,
so ``BACKLINK_PUBLISHER_CONFIG_DIR`` changes (tests / CI) re-resolve cleanly.

**Import-path decision:** ``JsonStore`` / ``_LazyStore`` live in the
repo-root ``webui_store/`` package, NOT under ``src/``. Verified that
``from webui_store.base import JsonStore, _LazyStore`` resolves from within
``src/backlink_publisher/`` (the package is on the path the same way it is
for ``webui_store/channel_status.py``), so this module imports the
root-level package directly rather than re-implementing the store. Writes
go through ``JsonStore.save`` → ``persistence.safe_write.atomic_write``
(0o600), and ``update(fn)`` serialises read-modify-write under a
per-instance ``threading.Lock``.

**Fields** (Unit 1 minimal + Unit 4 quarantine/re-arm):
``{status, consecutive_failures, last_ok_at, last_drift_at,
consecutive_oks, quarantined}``. The first four are the advisory-default
debounce set (Unit 1). ``consecutive_oks`` + ``quarantined`` and the
re-arm machinery landed in Unit 4 alongside their consumers (the
publish-backlinks hard-skip gate). Old minimal records written before
Unit 4 are forward-compatible: missing ``consecutive_oks`` defaults to 0
and missing ``quarantined`` defaults to ``False`` (treated as not
quarantined).

Status values are the verdicts Unit 3 will write:
``link-alive`` / ``drift-confirmed`` / ``advisory`` / ``not-configured``.

NOT ``channel_status_store`` — that store is bind-scoped to
{velog, medium, blogger} and raises ``UsageError`` for other platforms.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 fallback mirrors config/loader.py
    import tomli as tomllib  # type: ignore[no-redef]  # reason: conditional import for Python <3.11 compat

from backlink_publisher.config.loader import _config_dir
from webui_store.base import JsonStore, _LazyStore

# Health status values written by the canary verb (Unit 3).
STATUS_LINK_ALIVE = "link-alive"
STATUS_DRIFT_CONFIRMED = "drift-confirmed"
STATUS_ADVISORY = "advisory"
STATUS_NOT_CONFIGURED = "not-configured"

#: Quarantine threshold — consecutive confirmed drifts before a platform is
#: flagged ``quarantined`` (debounce against a single transient drift). Only an
#: opt-in (``hard_skip=true``) + quarantined platform is ever hard-skipped at
#: publish time; degraded platforms otherwise stay advisory-only.
QUARANTINE_AFTER_N = 2

#: Re-arm threshold — consecutive ``link-alive`` runs required to clear an
#: existing quarantine (anti-flap; a single green does not un-quarantine).
REARM_AFTER_M = 2

_HEALTH_DEFAULT: dict[str, Any] = {
    "status": STATUS_NOT_CONFIGURED,
    "consecutive_failures": 0,
    "last_ok_at": None,
    "last_drift_at": None,
    "consecutive_oks": 0,
    "quarantined": False,
}

#: Sibling top-level key for the forward-path (publish-time) drift stream
#: (Plan 2026-05-27-006 Unit 2). Disjoint from the evergreen per-platform
#: records that :func:`record_verdict` *replaces* wholesale, so neither stream
#: can ever overwrite the other. Registry platform names never collide with
#: this leading-underscore sentinel.
_PUBLISH_PATH_KEY = "_publish_path"

#: Forward-path default record. No ``quarantined`` flag — v1 is advisory-only
#: (no publishing gate); ``degraded`` is the debounced advisory analogue,
#: surfaced as a WARNING + ``/ce:health`` badge.
_PUBLISH_PATH_DEFAULT: dict[str, Any] = {
    "status": STATUS_NOT_CONFIGURED,
    "consecutive_failures": 0,
    "last_ok_at": None,
    "last_drift_at": None,
    "consecutive_oks": 0,
    "degraded": False,
}


canary_health_store: _LazyStore = _LazyStore(
    lambda: JsonStore(
        _config_dir() / "canary-health.json",
        default_factory=dict,
    )
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_health(platform: str) -> dict[str, Any]:
    """Read API. Unknown platforms return the minimal default record so
    callers don't have to branch on membership."""
    data = canary_health_store.load() or {}
    rec = data.get(platform)
    if rec is None:
        return dict(_HEALTH_DEFAULT)
    return rec


def list_all() -> dict[str, dict[str, Any]]:
    """Read API. Returns the per-platform evergreen health records.

    Excludes the sibling :data:`_PUBLISH_PATH_KEY` forward-path stream — it is a
    nested ``{platform: record}`` mapping, not a platform record itself — so
    evergreen consumers that iterate this mapping (e.g. the ``/ce:health``
    canary card) never see the sentinel as a bogus platform."""
    data = canary_health_store.load() or {}
    return {k: v for k, v in data.items() if k != _PUBLISH_PATH_KEY}


def record_verdict(platform: str, status: str) -> dict[str, Any]:
    """Record a canary verdict for ``platform`` and return the new record.

    Debounce + quarantine/re-arm semantics:

    - ``link-alive``      → ``consecutive_failures`` resets to 0,
      ``consecutive_oks`` increments, ``last_ok_at`` stamped. On reaching
      :data:`REARM_AFTER_M` consecutive OKs while currently quarantined,
      ``quarantined`` clears (re-arm; anti-flap — one green is not enough).
    - ``drift-confirmed`` → ``consecutive_failures`` increments,
      ``consecutive_oks`` resets to 0, ``last_drift_at`` stamped. On
      reaching :data:`QUARANTINE_AFTER_N` consecutive failures,
      ``quarantined`` is set ``True``.
    - ``advisory`` / ``not-configured`` → counters preserved, timestamps
      and quarantine flag unchanged (a read failure is neither an OK nor a
      confirmed drift, and must never silently un-quarantine or quarantine).

    Backward compat: old records missing ``consecutive_oks`` / ``quarantined``
    default to 0 / ``False``.

    Read-modify-write runs through ``update(fn)`` under the store's
    per-instance lock; the write is atomic 0o600 via ``JsonStore.save``.
    """

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        existing = current.get(platform) or {}
        failures = int(existing.get("consecutive_failures", 0) or 0)
        oks = int(existing.get("consecutive_oks", 0) or 0)
        quarantined = bool(existing.get("quarantined", False))
        last_ok_at = existing.get("last_ok_at")
        last_drift_at = existing.get("last_drift_at")

        if status == STATUS_LINK_ALIVE:
            failures = 0
            oks += 1
            last_ok_at = _now_iso()
            if quarantined and oks >= REARM_AFTER_M:
                quarantined = False  # re-arm
        elif status == STATUS_DRIFT_CONFIRMED:
            failures += 1
            oks = 0
            last_drift_at = _now_iso()
            if failures >= QUARANTINE_AFTER_N:
                quarantined = True
        # advisory / not-configured: preserve counters, timestamps, quarantine.

        current[platform] = {
            "status": status,
            "consecutive_failures": failures,
            "last_ok_at": last_ok_at,
            "last_drift_at": last_drift_at,
            "consecutive_oks": oks,
            "quarantined": quarantined,
        }
        return current

    return canary_health_store.update(_apply)[platform]


def is_quarantined(platform: str) -> bool:
    """True iff ``platform`` is currently quarantined (confirmed-drift debounce
    crossed :data:`QUARANTINE_AFTER_N` and not yet re-armed). Backward-compat:
    records missing the ``quarantined`` key are treated as not quarantined.
    Fail-open: unknown platforms return ``False``."""
    return bool(get_health(platform).get("quarantined", False))


def is_degraded(platform: str) -> bool:
    """True iff ``platform`` warrants an advisory WARNING at publish/plan time:
    either its last verdict is ``drift-confirmed`` or it is quarantined.
    Fail-open: unknown / never-run platforms return ``False`` (no spurious
    warning). ``advisory`` / ``link-alive`` / ``not-configured`` are NOT
    degraded — an unreadable canary page is not a confirmed contract drift."""
    rec = get_health(platform)
    return bool(rec.get("quarantined", False)) or (
        rec.get("status") == STATUS_DRIFT_CONFIRMED
    )


def get_publish_path_health(platform: str) -> dict[str, Any]:
    """Read API for the forward-path (publish-time) drift stream (Unit 2).

    Lives under the sibling :data:`_PUBLISH_PATH_KEY`, never nested under the
    evergreen platform record. Unknown platforms return the minimal default so
    callers need not branch on membership."""
    data = canary_health_store.load() or {}
    raw_stream = data.get(_PUBLISH_PATH_KEY)
    stream = raw_stream if isinstance(raw_stream, dict) else {}
    rec = stream.get(platform)
    if rec is None or not isinstance(rec, dict):
        return dict(_PUBLISH_PATH_DEFAULT)
    return rec


def record_publish_path_verdict(platform: str, status: str) -> dict[str, Any]:
    """Record a forward-path (publish-time) drift verdict for ``platform``.

    Mirrors :func:`record_verdict`'s debounce/re-arm arithmetic (set degraded at
    :data:`QUARANTINE_AFTER_N` consecutive drifts, re-arm after
    :data:`REARM_AFTER_M` consecutive OKs) but writes to the **sibling
    top-level** :data:`_PUBLISH_PATH_KEY` stream so the evergreen writer — which
    *replaces* ``data[platform]`` wholesale — can never wipe it, and vice versa.

    Advisory only: the ``degraded`` flag drives a WARNING + ``/ce:health``
    badge, never a publishing gate (v1 has no forward-path hard-skip). Only
    ``link-alive`` / ``drift-confirmed`` mutate the record; any other status is
    a no-op (preserves the record), matching :func:`record_verdict`.

    Read-modify-write runs through ``update(fn)`` under the store's per-instance
    lock; the write is atomic 0o600 via ``JsonStore.save``.
    """

    def _apply(current: dict[str, Any]) -> dict[str, Any]:
        current = dict(current)
        stream = dict(current.get(_PUBLISH_PATH_KEY) or {})
        existing = stream.get(platform) or {}
        failures = int(existing.get("consecutive_failures", 0) or 0)
        oks = int(existing.get("consecutive_oks", 0) or 0)
        degraded = bool(existing.get("degraded", False))
        last_ok_at = existing.get("last_ok_at")
        last_drift_at = existing.get("last_drift_at")

        if status == STATUS_LINK_ALIVE:
            failures = 0
            oks += 1
            last_ok_at = _now_iso()
            if degraded and oks >= REARM_AFTER_M:
                degraded = False  # re-arm (anti-flap: one green is not enough)
        elif status == STATUS_DRIFT_CONFIRMED:
            failures += 1
            oks = 0
            last_drift_at = _now_iso()
            if failures >= QUARANTINE_AFTER_N:
                degraded = True
        else:
            # advisory / not-configured / unknown: never an OK nor a confirmed
            # drift — preserve the record untouched (no-op).
            return current

        stream[platform] = {
            "status": status,
            "consecutive_failures": failures,
            "last_ok_at": last_ok_at,
            "last_drift_at": last_drift_at,
            "consecutive_oks": oks,
            "degraded": degraded,
        }
        current[_PUBLISH_PATH_KEY] = stream
        return current

    new = canary_health_store.update(_apply)
    rec = (new.get(_PUBLISH_PATH_KEY) or {}).get(platform)
    return rec if rec is not None else dict(_PUBLISH_PATH_DEFAULT)


def is_publish_path_degraded(platform: str) -> bool:
    """True iff ``platform``'s forward-path stream is in the debounced degraded
    state (>= :data:`QUARANTINE_AFTER_N` consecutive confirmed drifts, not yet
    re-armed). Advisory — never gates publishing. Fail-open: unknown / never-run
    platforms return ``False`` (no spurious warning)."""
    return bool(get_publish_path_health(platform).get("degraded", False))


def list_publish_path_all() -> dict[str, dict[str, Any]]:
    """Return all per-platform forward-path records for ``/ce:health``.

    Returns a ``{platform: health_dict}`` mapping of every platform that has
    *ever* had a forward-path verdict recorded (i.e., the inner
    ``_publish_path`` mapping). Platforms with no data yet are absent (the
    caller renders them as neutral/n-a). Fills each record with
    :data:`_PUBLISH_PATH_DEFAULT` back-compat defaults so the template can
    read all keys without guards.

    Used by :py:mod:`webui_app.routes.health` — never raises (fail-open)."""
    data = canary_health_store.load() or {}
    ppath = data.get(_PUBLISH_PATH_KEY)
    if not isinstance(ppath, dict):
        return {}
    return {
        platform: {**_PUBLISH_PATH_DEFAULT, **rec}
        for platform, rec in ppath.items()
        if isinstance(rec, dict)
    }


def _load_canary_section(config_path: Path | None = None) -> dict[str, Any]:
    """Read the raw ``[canary]`` table straight off the parsed TOML.

    Mirrors ``loader.py``'s ``data.get("targets", {})`` style rather than
    threading a new field through the ``Config`` dataclass (which
    ``save_config`` does not round-trip). Missing file / missing section →
    empty dict (never raises)."""
    path = config_path or (_config_dir() / "config.toml")
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    section = data.get("canary", {})
    return section if isinstance(section, dict) else {}


def read_canary_config(
    platform: str, *, config_path: Path | None = None
) -> dict[str, Any] | None:
    """Read the ``[canary.<platform>]`` post config for ``platform``.

    Returns ``{"post_url", "expected_target", "marker", "hard_skip"}`` when
    the section exists, else ``None`` (platform not configured → Unit 3
    surfaces it as ``not-configured``).

    ``marker`` is the private, per-seeded-post sentinel the canary asserts is
    present before it will ever classify ``drift-confirmed`` (a missing marker
    means the page is not proven to be the canary post, so a missing anchor
    stays ``advisory`` rather than a false drift). ``None`` when unset.
    ``hard_skip`` defaults to ``False`` when absent. The opt-in hard-skip
    machinery that consumes it lands in Unit 4.
    """
    entry = _load_canary_section(config_path).get(platform)
    if not isinstance(entry, dict):
        return None
    marker = entry.get("marker")
    return {
        "post_url": str(entry.get("post_url", "")),
        "expected_target": str(entry.get("expected_target", "")),
        "marker": str(marker) if marker else None,
        "hard_skip": bool(entry.get("hard_skip", False)),
    }


__all__ = [
    "canary_health_store",
    "STATUS_LINK_ALIVE",
    "STATUS_DRIFT_CONFIRMED",
    "STATUS_ADVISORY",
    "STATUS_NOT_CONFIGURED",
    "QUARANTINE_AFTER_N",
    "REARM_AFTER_M",
    "get_health",
    "list_all",
    "record_verdict",
    "read_canary_config",
    "is_quarantined",
    "is_degraded",
    "get_publish_path_health",
    "record_publish_path_verdict",
    "is_publish_path_degraded",
    "list_publish_path_all",
    "_load_canary_section",
]
