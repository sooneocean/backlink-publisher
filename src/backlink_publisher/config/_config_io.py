"""Config directory resolution, atomic file I/O, and pre-save snapshots.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
"""

from __future__ import annotations

from pathlib import Path


_CONFIG_HISTORY_MAX: int = 20


def _resolve_config_dir():
    from backlink_publisher import config as _cfg
    return _cfg._config_dir()


def _resolve_cache_dir():
    from backlink_publisher import config as _cfg
    return _cfg._cache_dir()


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    from backlink_publisher.persistence.safe_write import atomic_write
    atomic_write(path, text, mode)


def _snapshot_config(path: Path, max_history: int = _CONFIG_HISTORY_MAX) -> None:
    from backlink_publisher.persistence.safe_write import rotate_snapshots
    rotate_snapshots(
        path,
        snapshot_dir=path.parent / ".config-history",
        file_suffix=".toml",
        max_history=max_history,
    )
