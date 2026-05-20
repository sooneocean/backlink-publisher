"""Blogger / Medium token file I/O."""
from __future__ import annotations

import json
import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any


if sys.version_info >= (3, 11):
    pass
else:
    pass  # type: ignore[no-redef]



def _resolve_config_dir():
    """Indirect lookup of ``_config_dir`` via the package — restores
    monkeypatchability after the Unit 5 split (see ``writer.py``)."""
    from backlink_publisher import config as _cfg
    return _cfg._config_dir()


_log = logging.getLogger(__name__)

def load_blogger_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_resolve_config_dir() / "blogger-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_blogger_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "blogger-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # Restrict permissions (no-op on Windows)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_medium_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Medium OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_resolve_config_dir() / "medium-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_medium_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Medium OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "medium-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_ghpages_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load GitHub Pages PAT JSON ({token: "..."}). Returns None if missing.

    Per Plan 2026-05-19-006 SEC-3: PAT lives in a 0600 file, never in
    ``config.toml``. Operator pastes the token via /settings (or by
    writing the file directly during initial setup).
    """
    token_path = path or (_resolve_config_dir() / "ghpages-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_ghpages_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save GitHub Pages PAT dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "ghpages-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_hashnode_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Hashnode PAT JSON ({token: "..."}). Returns None if missing.

    Same SEC-3 contract as ghpages: PAT lives in a 0600 file, never in
    ``config.toml``. Operator generates the token from
    hashnode.com/settings/developer.
    """
    token_path = path or (_resolve_config_dir() / "hashnode-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_hashnode_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Hashnode PAT dict to JSON file with mode 0600."""
    token_path = path or (_resolve_config_dir() / "hashnode-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
