from __future__ import annotations

import os
import stat
import logging
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write text to path atomically via a sibling .tmp/.new file and replace.

    Ensures readers see either the old file or the fully written new one,
    never a partially written or torn file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    tmp.replace(path)


def rotate_snapshots(
    path: Path,
    snapshot_dir: Path,
    file_suffix: str = ".toml",
    max_history: int = 20,
) -> None:
    """Best-effort: copy current file to snapshot_dir with UTC timestamp.

    Rotates oldest snapshots so that snapshot_dir does not grow unbounded.
    Failure to snapshot does not raise an exception, to ensure the main write path
    remains operational.
    """
    if not path.exists():
        return
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(snapshot_dir, stat.S_IRWXU)  # 0700
        except OSError:
            pass
    except OSError as exc:
        _log.warning(
            f"Failed to create snapshot directory {snapshot_dir}: {exc}"
        )
        return

    # UTC ISO timestamp with colons replaced (Windows-safe).
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    snap_path = snapshot_dir / f"{ts}{file_suffix}"
    try:
        snap_path.write_bytes(path.read_bytes())
        try:
            os.chmod(snap_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except OSError as exc:
        _log.warning(
            f"Failed to write snapshot {snap_path}: {exc}"
        )
        return

    # Rotate: keep the newest max_history files by mtime.
    try:
        snapshots = sorted(
            (p for p in snapshot_dir.glob(f"*{file_suffix}") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(snapshots) - max_history
        for old in snapshots[:max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass
