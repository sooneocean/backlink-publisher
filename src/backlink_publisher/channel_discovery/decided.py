"""Decided-store query API for channel discovery dedup (brainstorm 2026-06-01 R8).

Before probing a candidate URL, a discovery run must dedup against:
  1. The registry (registered_platforms() — always decided, verdict = registered)
  2. This store (non-registry platforms with recorded verdicts)

Usage::

    from backlink_publisher.channel_discovery.decided import is_decided, get_verdict

    if is_decided("bloglovin"):
        print("already ruled out, skip")

Verdicts: ``registered`` | ``no-go`` | ``removed`` | ``hold`` |
          ``conditional-deferred`` | ``deferred`` | ``needs-canary``

Data source: ``docs/notes/channel-decisions.json`` (relative to the repo root,
resolved via the package location so this works from any cwd).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Resolve the data file relative to this module's location so the module
# works regardless of the caller's cwd.
_HERE = Path(__file__).parent
_DATA_FILE = _HERE.parents[2] / "docs" / "notes" / "channel-decisions.json"

_DECIDED_VERDICTS = frozenset(
    {
        "no-go",
        "removed",
        "hold",
        "conditional-deferred",
        "deferred",
        "needs-canary",
    }
)


def _load() -> dict:
    """Load and return the raw JSON data (not cached — file may be updated)."""
    if not _DATA_FILE.exists():
        return {"version": 1, "entries": []}
    return json.loads(_DATA_FILE.read_text(encoding="utf-8"))


def _entries() -> list[dict]:
    return _load().get("entries", [])


def get_verdict(platform: str) -> Optional[dict]:
    """Return the verdict record for *platform*, or ``None`` if undecided.

    Also queries the publishing registry so callers need only one call.
    Registered platforms return a synthetic ``{"verdict": "registered"}`` record.
    """
    # Check registry first.
    try:
        from backlink_publisher.publishing.registry import registered_platforms

        if platform in registered_platforms():
            return {"platform": platform, "verdict": "registered", "source": "registry"}
    except Exception:  # noqa: BLE001 — registry unavailable, fall through
        pass

    for entry in _entries():
        if entry.get("platform") == platform:
            return entry
    return None


def is_decided(platform: str) -> bool:
    """True if *platform* has any recorded verdict (including registry registration)."""
    return get_verdict(platform) is not None


def all_decided_platforms() -> set[str]:
    """Return all platform slugs with a recorded verdict.

    Includes registry-registered platforms + non-registry store entries.
    """
    decided: set[str] = set()
    try:
        from backlink_publisher.publishing.registry import registered_platforms

        decided.update(registered_platforms())
    except Exception:  # noqa: BLE001
        pass
    decided.update(e["platform"] for e in _entries())
    return decided


def undecided_only(candidates: list[str]) -> list[str]:
    """Filter *candidates* to those not yet in the decided-store or registry.

    Use before probing a batch to skip already-decided platforms.
    """
    return [c for c in candidates if not is_decided(c)]
