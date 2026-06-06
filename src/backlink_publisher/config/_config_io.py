"""Config directory resolution, atomic file I/O, and pre-save snapshots.

Extracted from ``writer.py`` in the Unit 2 monolith decomposition.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path


_log = logging.getLogger(__name__)

_CONFIG_HISTORY_MAX: int = 20

# Keys whose values must be redacted in config_history snapshots (SEC-1).
_REDACT_KEYS: frozenset[str] = frozenset({
    "client_id",
    "client_secret",
    "integration_token",
    "api_key",
})

# Compiled regex: matches ``key = "value"`` (possibly with trailing comment).
_REDACT_RE = re.compile(
    r"^(\s*(?:" + "|".join(re.escape(k) for k in _REDACT_KEYS) + r")\s*=\s*)"
    r'"[^"]*"(\s*(?:#.*)?)$',
    re.MULTILINE,
)


def _redact_toml_credential_values(toml_text: str) -> str:
    """Return *toml_text* with known credential values replaced by ``"****"``.

    Preserves all structure — section headers, non-credential keys, comments,
    and indentation — only the value portion of matched keys is replaced.
    No-op when no credential keys are present.
    """
    return _REDACT_RE.sub(r'\1"****"\2', toml_text)


def _resolve_config_dir() -> Path:
    from backlink_publisher import config as _cfg
    return _cfg._config_dir()


def _resolve_cache_dir() -> Path:
    from backlink_publisher import config as _cfg
    return _cfg._cache_dir()


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    from backlink_publisher.persistence.safe_write import atomic_write
    atomic_write(path, text, mode)


def _snapshot_config(path: Path, max_history: int = _CONFIG_HISTORY_MAX) -> None:
    from backlink_publisher.persistence.safe_write import rotate_snapshots

    # SEC-1: redact credential values before writing snapshot.
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("Failed to read config for snapshot: %s", exc)
        return

    redacted = _redact_toml_credential_values(raw)

    rotate_snapshots(
        path,
        snapshot_dir=path.parent / ".config-history",
        file_suffix=".toml",
        max_history=max_history,
        content=redacted,
    )
