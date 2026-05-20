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


def snapshot_token_revs() -> dict[str, int]:
    """Capture the current token_rev for all known credential files."""
    revs = {}
    for plat, filename in [
        ("blogger", "blogger-token.json"),
        ("medium", "medium-token.json"),
        ("ghpages", "ghpages-token.json"),
        ("hashnode", "hashnode-token.json"),
        ("writeas", "writeas-token.json"),
    ]:
        token = _load_token(None, filename)
        if token:
            revs[plat] = token.get("token_rev", 0)
    return revs

def _load_token(path: Path | None, default_filename: str) -> dict[str, Any] | None:
    token_path = path or (_resolve_config_dir() / default_filename)
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_token(data: dict[str, Any], path: Path | None, default_filename: str) -> None:
    token_path = path or (_resolve_config_dir() / default_filename)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    
    existing = _load_token(token_path, default_filename)
    current_rev = existing.get("token_rev", 0) if existing else 0
    data["token_rev"] = current_rev + 1

    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def load_blogger_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load OAuth token dict from JSON file. Returns None if file missing."""
    return _load_token(path, "blogger-token.json")


def save_blogger_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save OAuth token dict to JSON file with mode 0600."""
    _save_token(data, path, "blogger-token.json")


def load_medium_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Medium OAuth token dict from JSON file. Returns None if file missing."""
    return _load_token(path, "medium-token.json")


def save_medium_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Medium OAuth token dict to JSON file with mode 0600."""
    _save_token(data, path, "medium-token.json")


def load_ghpages_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load GitHub Pages PAT JSON ({token: "..."}). Returns None if missing."""
    return _load_token(path, "ghpages-token.json")


def save_ghpages_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save GitHub Pages PAT dict to JSON file with mode 0600."""
    _save_token(data, path, "ghpages-token.json")


def load_hashnode_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Hashnode PAT JSON ({token: "..."}). Returns None if missing."""
    return _load_token(path, "hashnode-token.json")


def save_hashnode_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Hashnode PAT dict to JSON file with mode 0600."""
    _save_token(data, path, "hashnode-token.json")


def load_writeas_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Write.as login-issued token JSON ({token: "..."})."""
    return _load_token(path, "writeas-token.json")


def save_writeas_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Write.as token dict to JSON file with mode 0600."""
    _save_token(data, path, "writeas-token.json")
