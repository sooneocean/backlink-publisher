"""``comment status`` persistence — a locked, operator-private ReviewStatus store.

``status`` is the only mutable state in the module and it is **operator-set only** — the
tool never derives ``posted`` (or any other status) automatically. The store is a single
JSONL file under the existing ``~/.config/backlink-publisher/`` tree (operator-private,
not a fresh world-readable subdir), keyed by ``target_id``.

Three properties this module guarantees:

1. **No lost updates.** An upsert is a read-modify-write; ``atomic_write_jsonl`` makes the
   *write* atomic but not the *update*, so two racing ``status`` calls would clobber each
   other. The whole RMW is wrapped in an advisory ``fcntl.flock`` on a sibling lock file.
2. **No retained secrets.** The file is **fully rewritten** every time, never appended —
   an append log would keep a deleted row's ``final_comment_text`` in an earlier line.
   ``removed`` / ``rejected`` physically drop the row.
3. **Tight permissions, always.** ``0o600`` is asserted-and-repaired on every open (not
   just first write), mirroring the ``llm-settings.json`` pre-#140 ``0o644`` bug.

The store path is resolved through :func:`config.loader._config_dir` **at call time**, so
a test (or container) that re-points ``BACKLINK_PUBLISHER_CONFIG_DIR`` between calls is
honored — never a frozen ``Path.home()``.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backlink_publisher._util.errors import InputValidationError, PipelineError
from backlink_publisher._util.jsonl import atomic_write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.comment_outreach import schema

status_logger = PipelineLogger("comment-status")

STORE_FILENAME = "comment-outreach-status.jsonl"
LOCK_FILENAME = "comment-outreach-status.lock"

#: Terminal statuses whose row is physically removed (purging any stored comment text).
_DELETE_STATUSES = {"removed", "rejected"}


def _config_dir() -> Path:
    """Re-resolve CONFIG_DIR at call time (never a frozen import-time Path)."""
    from backlink_publisher.config.loader import _config_dir as resolve

    return resolve()


def _store_path() -> Path:
    return _config_dir() / STORE_FILENAME


def _repair_perms(path: Path) -> None:
    """Tighten *path* to ``0o600`` if it exists and is looser (assert-and-repair)."""
    try:
        if path.exists() and (path.stat().st_mode & 0o777) != 0o600:
            os.chmod(path, 0o600)
    except OSError:  # best-effort hardening; never crash the verb on a chmod failure
        pass


def _load_all(path: Path) -> list[dict[str, Any]]:
    """Read every JSONL object row from *path* (repairing perms first). Missing file or
    malformed lines yield no rows rather than raising."""
    if not path.exists():
        return []
    _repair_perms(path)
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def upsert_status(record: dict[str, Any]) -> dict[str, Any]:
    """Insert/replace (or, for terminal statuses, delete) *record* by ``target_id``.

    The entire read-modify-write runs under an exclusive ``flock`` so concurrent calls
    serialize and no update is lost. The file is always fully rewritten at ``0o600``.
    """
    target_id = record["target_id"]
    delete = record.get("status") in _DELETE_STATUSES

    cfg_dir = _config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    store_path = cfg_dir / STORE_FILENAME
    lock_path = cfg_dir / LOCK_FILENAME

    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        # Read-only / unwritable CONFIG_DIR, or a race that removed the dir. Surface as a
        # clean PipelineError (documented exit code) instead of an uncaught traceback —
        # the status verb's exit-0/handled-error contract must hold.
        raise PipelineError(f"cannot open status store lock at {lock_path}: {exc}") from exc
    try:
        _repair_perms(lock_path)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        rows = [r for r in _load_all(store_path) if r.get("target_id") != target_id]
        if not delete:
            rows.append(record)
        atomic_write_jsonl(rows, store_path, mode=0o600)
        _repair_perms(store_path)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    return record


def set_status(
    target_id: str,
    status: str,
    *,
    reviewer: Optional[str] = None,
    comment_url: Optional[str] = None,
    final_comment_text: Optional[str] = None,
    result_notes: Optional[str] = None,
    updated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build, validate, and persist a ``ReviewStatus``. Raises ``InputValidationError`` if
    the assembled record fails schema validation (the CLI validates the status *enum*
    earlier so a bad enum surfaces as ``UsageError``/exit-1, not exit-2)."""
    record: dict[str, Any] = {"target_id": target_id, "status": status}
    for key, value in (
        ("reviewer", reviewer),
        ("comment_url", comment_url),
        ("final_comment_text", final_comment_text),
        ("result_notes", result_notes),
    ):
        if value is not None:
            record[key] = value
    record["updated_at"] = updated_at or datetime.now(timezone.utc).isoformat()

    errors = schema.validate_review_status(record)
    if errors:
        raise InputValidationError("; ".join(errors))

    upsert_status(record)
    status_logger.recon(
        "comment_status_set",
        target_id=target_id,
        status=status,
        deleted=status in _DELETE_STATUSES,
    )
    return record


def load_status(target_id: str) -> Optional[dict[str, Any]]:
    """Return the stored ReviewStatus for *target_id*, or ``None``."""
    for row in _load_all(_store_path()):
        if row.get("target_id") == target_id:
            return row
    return None
