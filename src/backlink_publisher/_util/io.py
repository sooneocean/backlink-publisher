"""Shared I/O helpers for the backlink pipeline.

Currently exposes a single primitive — ``atomic_write_json`` — used by checkpoint
and anchor-profile persistence. Both consumers need the same guarantees: write
the new bytes to a sibling temp file, chmod 0600, and ``replace`` onto the final
path so a partial write never replaces the prior contents.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any, mode: int = 0o600) -> None:
    """Write ``data`` as JSON to ``path`` atomically.

    Steps: serialize → write to a per-process ``<path>.<pid>.tmp`` sibling →
    chmod → ``Path.replace`` onto the destination. ``Path.replace`` is atomic on
    POSIX, so readers either see the old file or the fully written new one —
    never a torn write.

    The temp name carries the writer's PID so two processes writing the same
    ``path`` concurrently cannot collide on a shared ``.tmp`` and crash one
    another's ``replace`` with ``FileNotFoundError`` (matches the per-PID temp
    in ``events.persona`` and the unique temp in ``persistence.safe_write``). A
    failed write unlinks its own temp so a crash mid-write leaves no orphan.

    ``mode`` defaults to 0o600 (owner read/write). Failures to chmod the temp
    file are swallowed because the rename is the load-bearing step; we still
    raise on the upstream write / ``replace`` failures.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        tmp.replace(path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
