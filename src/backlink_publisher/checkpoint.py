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
from backlink_publisher._util.io import atomic_write_json

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_lock = threading.Lock()


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id format: {run_id!r}")


def _checkpoint_dir() -> Path:
    return _cache_dir() / "checkpoints"


def _checkpoint_path(run_id: str) -> Path:
    return _checkpoint_dir() / f"{run_id}.json"


def checkpoint_path(run_id: str) -> Path:
    """Public accessor for a run's checkpoint file path.

    Lets the events projector resolve a run_id without importing the private
    ``_checkpoint_path``. Does not validate or require the file to exist.
    """
    return _checkpoint_path(run_id)


def _atomic_write(path: Path, data: dict) -> None:
    # Preserved as a module-private alias so existing call sites and tests that
    # patch ``checkpoint._atomic_write`` keep working. New code should import
    # ``atomic_write_json`` from ``io_utils`` directly.
    atomic_write_json(path, data, mode=stat.S_IRUSR | stat.S_IWUSR)


def generate_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(4).hex()


#: error_class string constants used by Unit 7 (R13 retro-revalidation) so
#: producers, the to_process filter, and --list-runs display all agree.
RETRO_LANGUAGE_FAILED = "retro_language_failed"
RETRO_ANCHOR_FAILED = "retro_anchor_failed"
#: In-band policy-gate decision (skipped_policy / skipped_circuit_open).
#: Distinct from adapter failures — the adapter was never called.
#: --resume excludes these items (the gate decision must be re-evaluated at
#: dispatch time, not retried blindly).
POLICY_SKIP = "policy_skip"


def create_checkpoint(
    rows: list[dict[str, Any]],
    platform: str | None,
    mode: str,
    flags: dict[str, Any] | None = None,
) -> tuple[str, Path]:
    """Create a new checkpoint file for a batch run. Returns (run_id, path).

    ``flags`` (added in plan 2026-05-14-001 Unit 6) is a top-level dict that
    persists CLI-level posture across ``--resume``. Today's known keys:
    ``skip_publish_time_check: bool``. Older checkpoints lacking the key are
    safe — ``_run_resume`` reads via ``.get("flags", {})``.
    """
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
        "flags": dict(flags or {}),
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


_OPTIONAL_ITEM_FIELDS = (
    "adapter",
    "published_url",
    "article_urls",
    "error",
    "error_class",
    "completed_at",
)


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


def list_all_runs() -> list[dict[str, Any]]:
    """Return ALL checkpoint runs (regardless of status), newest-first.

    Unlike ``list_incomplete``, this includes completed runs. Skips
    ``.tmp`` orphan files from interrupted writes. Bound scan to 100
    most-recently-modified files to keep I/O bounded.
    """
    ckpt_dir = _checkpoint_dir()
    if not ckpt_dir.exists():
        return []
    candidates = sorted(
        ckpt_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:100]
    results = []
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        results.append(data)
    results.sort(key=lambda d: d.get("started_at", ""), reverse=True)
    return results


def list_failed_items() -> list[dict[str, Any]]:
    """Return all checkpoint items with status 'pending' or 'failed',
    across all runs. Each item is tagged with its parent run's ``run_id``.

    Used by the reconciler to find items that may need auto-fixing or
    quarantine.
    """
    items: list[dict[str, Any]] = []
    for run in list_all_runs():
        run_id = run.get("run_id", "")
        for item in run.get("items", []):
            status = item.get("status")
            if status in ("pending", "failed"):
                tagged = dict(item)
                tagged["_run_id"] = run_id
                items.append(tagged)
    return items


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
