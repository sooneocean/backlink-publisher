"""PR opportunity store — flock-guarded JSONL, per-entry upsert.

Follows the same discipline as ``comment_outreach/store.py``:
- RMW wrapped in ``fcntl.flock`` (no lost updates under concurrent CLI calls)
- Full rewrite on every save (no append log retaining deleted-row content)
- ``0o600`` asserted-and-repaired on every open
- Config dir resolved at call time (never a frozen import-time Path)
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher._util.jsonl import atomic_write_jsonl

STORE_FILENAME = "pr-opportunities.jsonl"
LOCK_FILENAME = "pr-opportunities.lock"

#: Valid lifecycle states for an opportunity.
STATUS_ENUM = frozenset({"pending", "draft", "sent", "won", "lost", "skipped"})


def _config_dir() -> Path:
    from backlink_publisher.config.loader import _config_dir as resolve

    return resolve()


def _store_path() -> Path:
    return _config_dir() / STORE_FILENAME


def _lock_path() -> Path:
    return _config_dir() / LOCK_FILENAME


def _repair_perms(path: Path) -> None:
    try:
        if path.exists() and (path.stat().st_mode & 0o177) != 0:
            os.chmod(path, 0o600)
    except OSError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_opportunities() -> list[dict[str, Any]]:
    """Return all stored PR opportunities as a list of dicts."""
    path = _store_path()
    if not path.exists():
        return []
    _repair_perms(path)
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def upsert_opportunity(entry: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a PR opportunity by ``id``.

    ``entry`` must contain an ``id`` key.  Returns the saved entry.
    """
    opp_id = entry.get("id")
    if not opp_id:
        raise ValueError("PR opportunity entry must have an 'id' field")

    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            rows = load_opportunities()
            by_id = {r["id"]: r for r in rows if r.get("id")}
            existing = by_id.get(opp_id, {})
            merged = {**existing, **entry, "updated_at": _now_iso()}
            if "created_at" not in merged:
                merged["created_at"] = merged["updated_at"]
            by_id[opp_id] = merged
            _save_all(list(by_id.values()))
            return merged
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def update_status(opp_id: str, status: str, *, draft: str | None = None) -> dict[str, Any]:
    """Update the status (and optionally draft text) of an opportunity."""
    if status not in STATUS_ENUM:
        raise ValueError(f"status must be one of {sorted(STATUS_ENUM)}, got {status!r}")
    patch: dict[str, Any] = {"id": opp_id, "status": status}
    if draft is not None:
        patch["draft"] = draft
    return upsert_opportunity(patch)


def _save_all(rows: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_jsonl(rows, path)
    _repair_perms(path)
