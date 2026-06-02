"""Blogger / Medium token file I/O."""

from __future__ import annotations

import json
import logging
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

#: All token-FILE-backed credential platforms, in scan order. Single source of
#: truth for both the run-start baseline snapshot and the per-row drift re-check.
#: Deliberately a SUBSET of the adapter registry — platforms that authenticate by
#: browser session or paste-blob (e.g. livejournal, mastodon, rentry, substack,
#: telegraph, txtfyi, velog) keep no token_rev file, so there is nothing to
#: drift-check. Add an entry here whenever a NEW platform gains a save_*_token.
_TOKEN_FILES: list[tuple[str, str]] = [
    ("blogger", "blogger-token.json"),
    ("medium", "medium-token.json"),
    ("ghpages", "ghpages-token.json"),
    ("notion", "notion-token.json"),
    ("devto", "devto-token.json"),
    ("wordpresscom", "wordpresscom-token.json"),
    ("hashnode", "hashnode-token.json"),
    ("writeas", "writeas-token.json"),
    ("tumblr", "tumblr-credentials.json"),
    ("linkedin", "linkedin-token.json"),
    ("hackmd", "hackmd-token.json"),
    ("mataroa", "mataroa-token.json"),
    ("gitlabpages", "gitlabpages-token.json"),
]


def _resolve_config_dir() -> Path:
    """Indirect lookup of ``_config_dir`` via the package — restores
    monkeypatchability after the Unit 5 split (see ``writer.py``)."""
    from backlink_publisher import config as _cfg

    return _cfg._config_dir()


_log = logging.getLogger(__name__)


def snapshot_token_revs(platforms: Iterable[str] | None = None) -> dict[str, int]:
    """Capture the current token_rev for credential files.

    ``platforms`` limits the scan to the named platforms — pass it for the
    per-row drift re-check so it reads only the few files already bound at
    run-start instead of opening+parsing all ten every row (the publish
    hot-loop did 10xN reads otherwise). ``None`` (the default, used for the
    run-start baseline) scans every known file. An empty iterable scans none.
    """
    wanted = None if platforms is None else set(platforms)
    revs = {}
    for plat, filename in _TOKEN_FILES:
        if wanted is not None and plat not in wanted:
            continue
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
            return cast("dict[str, Any] | None", json.load(f))
    except Exception:
        return None


def _save_token(data: dict[str, Any], path: Path | None, default_filename: str) -> None:
    token_path = path or (_resolve_config_dir() / default_filename)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_token(token_path, default_filename)
    current_rev = existing.get("token_rev", 0) if existing else 0
    payload = {**data, "token_rev": current_rev + 1}

    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
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


def load_notion_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Notion integration token JSON ({integration_token: "...", database_id: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "notion-token.json")


def save_notion_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Notion integration token dict to JSON file with mode 0600.

    Expected keys: integration_token (str), database_id (str).
    """
    _save_token(data, path, "notion-token.json")


def load_wordpresscom_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load WordPress.com OAuth token JSON ({token: "...", site: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "wordpresscom-token.json")


def load_devto_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Dev.to API key JSON ({api_key: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "devto-token.json")


def save_devto_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Dev.to API key dict to JSON file with mode 0600.

    Expected keys: api_key (str).
    """
    _save_token(data, path, "devto-token.json")


def load_hackmd_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load HackMD API token JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "hackmd-token.json")


def save_hackmd_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save HackMD API token dict to JSON file with mode 0600.

    Expected keys: token (str). Generate at HackMD → Settings → API → Create token.
    """
    _save_token(data, path, "hackmd-token.json")


def load_mataroa_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Mataroa API key JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "mataroa-token.json")


def save_mataroa_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Mataroa API key dict to JSON file with mode 0600.

    Expected keys: token (str). Enable at mataroa.blog → account settings → API.
    """
    _save_token(data, path, "mataroa-token.json")


def load_gitlabpages_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load GitLab personal access token JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "gitlabpages-token.json")


def save_gitlabpages_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save GitLab PAT dict to JSON file with mode 0600.

    Expected keys: token (str). PAT needs ``api`` (or a project-scoped
    write_repository) scope on the target Pages project.
    """
    _save_token(data, path, "gitlabpages-token.json")


def load_zenn_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Zenn GitHub PAT JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "zenn-token.json")


def save_zenn_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Zenn GitHub PAT dict to JSON file with mode 0600.

    Expected keys: token (str). Generate at github.com → Settings →
    Developer settings → Personal access tokens → New token
    (repo or specific: contents:write on the Zenn-connected repo).
    """
    _save_token(data, path, "zenn-token.json")


def load_qiita_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Qiita personal access token JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "qiita-token.json")


def save_qiita_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Qiita PAT dict to JSON file with mode 0600.

    Expected keys: token (str). Generate at qiita.com → Settings → Applications.
    """
    _save_token(data, path, "qiita-token.json")


def save_wordpresscom_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save WordPress.com OAuth token dict to JSON file with mode 0600.

    Expected keys: token (str), site (str).
    """
    _save_token(data, path, "wordpresscom-token.json")


def load_hashnode_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Hashnode PAT JSON ({personal_access_token: "...", publication_id: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "hashnode-token.json")


def save_hashnode_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Hashnode PAT dict to JSON file with mode 0600.

    Expected keys: personal_access_token (str), publication_id (str).
    """
    _save_token(data, path, "hashnode-token.json")


def load_writeas_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Write.as API token JSON ({token: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "writeas-token.json")


def save_writeas_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Write.as API token dict to JSON file with mode 0600.

    Expected keys: token (str).
    """
    _save_token(data, path, "writeas-token.json")


def load_linkedin_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load LinkedIn OAuth token JSON ({token: "...", person_id: "..."}).

    Returns None if the file is absent — callers treat None as unbound.
    """
    return _load_token(path, "linkedin-token.json")


def save_linkedin_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save LinkedIn OAuth token dict to JSON file with mode 0600.

    Expected keys: token (str), person_id (str).
    """
    _save_token(data, path, "linkedin-token.json")


def load_medium_integration_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Medium integration token JSON ({integration_token: "..."}).

    SEC-3: integration token now lives in a dedicated 0600 file
    instead of config.toml. Returns None if the file is absent.
    """
    return _load_token(path, "medium-integration-token.json")


def save_medium_integration_token(
    data: dict[str, Any], path: Path | None = None
) -> None:
    """Save Medium integration token dict to JSON file with mode 0600.

    Expected keys: integration_token (str).
    """
    _save_token(data, path, "medium-integration-token.json")
