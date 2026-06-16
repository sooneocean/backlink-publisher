"""Process-scope statistics counters for the content-fetch gate.

Extracted from :mod:`backlink_publisher.content.fetch` (monolith-budget
headroom, 2026-06-01). The counters live here, but :mod:`~.fetch` imports the
``_STATS`` dict by reference and mutates it in place — it is never rebound,
only mutated, so ``content.fetch._STATS`` and ``_stats._STATS`` are the same
live object. Tests that read ``content.fetch._STATS`` or call the re-exported
:func:`reset_stats` / :func:`stats_snapshot` observe identical state.
"""

from __future__ import annotations

from typing import Any, Optional

#: Process-wide statistics counters. Updated on every
#: ``verify_url_has_content`` call. :func:`stats_snapshot` returns a shallow
#: copy; :func:`reset_stats` clears for tests / per-invocation resets.
_STATS: dict[str, Any] = {
    "cache_hits": 0,
    "cache_misses": 0,
    "fetches": 0,
    "total_latency_ms": 0,
    "reason_counts": {},
}


def reset_stats() -> None:
    """Reset the per-process stats counters.

    Called by plan-backlinks ``main()`` so each invocation reports its own
    cache hit rate / fetch count. Also by the autouse test fixture so
    cross-test bleed doesn't corrupt assertions.
    """
    _STATS["cache_hits"] = 0
    _STATS["cache_misses"] = 0
    _STATS["fetches"] = 0
    _STATS["total_latency_ms"] = 0
    _STATS["reason_counts"] = {}


def stats_snapshot() -> dict[str, Any]:
    """Return a snapshot of the stats counters.

    Shallow copy of the top-level dict, with ``reason_counts`` deep-copied
    so callers can mutate without affecting the live counters. Use
    :func:`reset_stats` to clear.
    """
    return {
        "cache_hits": _STATS["cache_hits"],
        "cache_misses": _STATS["cache_misses"],
        "fetches": _STATS["fetches"],
        "total_latency_ms": _STATS["total_latency_ms"],
        "reason_counts": dict(_STATS["reason_counts"]),
    }


def _record_reason(reason: Optional[str], ok: bool) -> None:
    """Increment the per-reason counter. ``ok=True`` records 'ok'."""
    key = "ok" if ok else (reason or "unknown")
    counts = _STATS["reason_counts"]
    counts[key] = counts.get(key, 0) + 1
