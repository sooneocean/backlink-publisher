"""Checkpoint persistence for publish-backlinks batch runs.

Checkpoint files live at ~/.cache/backlink-publisher/checkpoints/<run_id>.json
File permissions: 0600 (owner read/write only).
Directory permissions: 0700.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import _cache_dir

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_lock = threading.Lock()


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id format: {run_id!r}")


def _checkpoint_dir() -> Path:
    return _cache_dir() / "checkpoints"


def _checkpoint_path(run_id: str) -> Path:
    return _checkpoint_dir() / f"{run_id}.json"


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    tmp.replace(path)


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(4).hex()


def create_checkpoint(
    rows: list[dict[str, Any]],
    platform: str | None,
    mode: str,
) -> tuple[str, Path]:
    """Create a new checkpoint file for a batch run. Returns (run_id, path)."""
    run_id = generate_run_id()
    ckpt_dir = _checkpoint_dir()
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ckpt_dir, stat.S_IRWXU)
    except OSError:
        pass

    items = []
    for row in rows:
        items.append({
            "id": row.get("id", ""),
            "status": "pending",
            "title": row.get("title", ""),
            "platform": platform or row.get("platform", ""),
            "adapter": None,
            "published_url": None,
            "error": None,
            "error_class": None,
            "completed_at": None,
            "payload": row,
        })

    data: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "mode": mode,
        "status": None,
        "items": items,
    }

    path = _checkpoint_path(run_id)
    _atomic_write(path, data)
    return run_id, path


def load_checkpoint(run_id: str) -> dict[str, Any]:
    """Load checkpoint by run_id. Raises FileNotFoundError if not found."""
    _validate_run_id(run_id)
    path = _checkpoint_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"checkpoint not found: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


_OPTIONAL_ITEM_FIELDS = ("adapter", "published_url", "error", "error_class", "completed_at")


def update_item(run_id: str, item_id: str, status: str, **fields: Any) -> None:
    """Atomically update a single checkpoint item's status and fields.

    Optional fields not present in **fields are reset to None so stale values
    from a previous status do not linger after a status transition.
    """
    _validate_run_id(run_id)
    with _lock:
        data = load_checkpoint(run_id)
        for item in data["items"]:
            if item["id"] == item_id:
                item["status"] = status
                for f in _OPTIONAL_ITEM_FIELDS:
                    item[f] = None
                item.update(fields)
                break
        _atomic_write(_checkpoint_path(run_id), data)


def mark_complete(run_id: str) -> None:
    """Mark a checkpoint run as complete."""
    _validate_run_id(run_id)
    with _lock:
        data = load_checkpoint(run_id)
        data["status"] = "complete"
        _atomic_write(_checkpoint_path(run_id), data)


def list_incomplete() -> list[dict[str, Any]]:
    """Return all incomplete runs sorted by started_at descending.

    Incomplete = has at least one pending/failed item AND root status != 'complete'.
    Skips .tmp orphan files from interrupted writes.
    """
    ckpt_dir = _checkpoint_dir()
    if not ckpt_dir.exists():
        return []

    # Bound scan to 20 most-recently-modified files
    candidates = sorted(
        ckpt_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:20]

    results = []
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("status") == "complete":
            continue
        statuses = {item["status"] for item in data.get("items", [])}
        if statuses & {"pending", "failed"}:
            results.append(data)

    results.sort(key=lambda d: d.get("started_at", ""), reverse=True)
    return results


def delete(run_id: str) -> None:
    """Delete a checkpoint file. Raises FileNotFoundError if not found."""
    _validate_run_id(run_id)
    path = _checkpoint_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"checkpoint not found: {run_id}")
    path.unlink()


def delete_complete() -> int:
    """Delete all checkpoints with status='complete'. Returns count deleted."""
    ckpt_dir = _checkpoint_dir()
    if not ckpt_dir.exists():
        return 0
    count = 0
    for path in ckpt_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "complete":
                path.unlink()
                count += 1
        except Exception:
            continue
    return count
