"""User config loader for backlink-publisher.

Config file: ~/.config/backlink-publisher/config.toml
Token file:  ~/.config/backlink-publisher/blogger-token.json
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import DependencyError

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


def _config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / "backlink-publisher"


def _cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".cache"
    return base / "backlink-publisher"


@dataclass
class BloggerOAuthConfig:
    client_id: str
    client_secret: str


@dataclass
class Config:
    blogger_blog_ids: dict[str, str] = field(default_factory=dict)
    blogger_oauth: BloggerOAuthConfig | None = None
    medium_integration_token: str | None = None
    medium_user_data_dir: Path | None = None

    @property
    def config_dir(self) -> Path:
        return _config_dir()

    @property
    def cache_dir(self) -> Path:
        return _cache_dir()

    @property
    def blogger_token_path(self) -> Path:
        return _config_dir() / "blogger-token.json"

    @property
    def screenshot_dir(self) -> Path:
        return _cache_dir() / "screenshots"


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file. Missing file → empty Config (not an error)."""
    config_path = path or (_config_dir() / "config.toml")
    if not config_path.exists():
        return Config()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise DependencyError(
            f"Failed to parse config file {config_path}: {exc}"
        ) from exc

    blogger_section = data.get("blogger", {})
    oauth_section = blogger_section.pop("oauth", {})
    medium_section = data.get("medium", {})
    medium_browser_section = medium_section.get("browser", {})

    blogger_oauth: BloggerOAuthConfig | None = None
    if oauth_section.get("client_id") and oauth_section.get("client_secret"):
        blogger_oauth = BloggerOAuthConfig(
            client_id=oauth_section["client_id"],
            client_secret=oauth_section["client_secret"],
        )

    user_data_dir: Path | None = None
    if medium_browser_section.get("user_data_dir"):
        user_data_dir = Path(medium_browser_section["user_data_dir"])
    else:
        user_data_dir = _config_dir() / "chrome-profile-default"

    # blogger_section now contains only main_domain → blog_id mappings
    blog_ids = {k: str(v) for k, v in blogger_section.items() if isinstance(v, (str, int))}

    return Config(
        blogger_blog_ids=blog_ids,
        blogger_oauth=blogger_oauth,
        medium_integration_token=medium_section.get("integration_token") or None,
        medium_user_data_dir=user_data_dir,
    )


def resolve_blog_id(config: Config, main_domain: str) -> str:
    """Return Blogger blog_id for main_domain. Raises DependencyError if not mapped."""
    # Normalise: strip trailing slash for lookup
    key = main_domain.rstrip("/")
    # Try exact match, then with/without trailing slash
    for candidate in (key, key + "/"):
        if candidate in config.blogger_blog_ids:
            return config.blogger_blog_ids[candidate]
    raise DependencyError(
        f"No Blogger blog_id configured for domain '{main_domain}'. "
        f"Add it to ~/.config/backlink-publisher/config.toml under [blogger]:\n"
        f'  "{main_domain}" = "<your-blog-id>"'
    )


def load_blogger_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_config_dir() / "blogger-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_config(
    config: "Config",
    path: Path | None = None,
    extra_blogger_ids: dict[str, str] | None = None,
    medium_token: str | None = None,
    blogger_client_id: str | None = None,
    blogger_client_secret: str | None = None,
) -> None:
    """Write (or update) config.toml with the supplied values.

    Merges new values with any existing config so that calling this
    function never silently drops keys that were already there.
    """
    config_path = path or (_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config first so we don't clobber it
    existing = load_config(config_path)

    # Build blog_ids: start from config.blogger_blog_ids (may already be pre-set by caller),
    # then overlay extra_blogger_ids on top. If extra_blogger_ids is None, merge from existing.
    blog_ids: dict[str, str] = dict(config.blogger_blog_ids)
    if extra_blogger_ids is None:
        # No override supplied — merge from disk
        for k, v in existing.blogger_blog_ids.items():
            if k not in blog_ids:
                blog_ids[k] = v
    elif extra_blogger_ids:
        blog_ids.update(extra_blogger_ids)

    # OAuth credentials
    client_id = blogger_client_id or (
        existing.blogger_oauth.client_id if existing.blogger_oauth else ""
    )
    client_secret = blogger_client_secret or (
        existing.blogger_oauth.client_secret if existing.blogger_oauth else ""
    )

    # Medium token
    token = medium_token if medium_token is not None else (
        existing.medium_integration_token or ""
    )

    lines: list[str] = []

    # [blogger] section — domain → blog_id pairs first
    lines.append("[blogger]")
    for domain, blog_id in blog_ids.items():
        lines.append(f'"{domain}" = "{blog_id}"')
    lines.append("")

    # [blogger.oauth]
    if client_id or client_secret:
        lines.append("[blogger.oauth]")
        lines.append(f'client_id     = "{client_id}"')
        lines.append(f'client_secret = "{client_secret}"')
        lines.append("")

    # [medium]
    lines.append("[medium]")
    if token:
        lines.append(f'integration_token = "{token}"')
    else:
        lines.append("# integration_token = \"your-medium-integration-token\"")
    lines.append("")

    config_path.write_text("\n".join(lines), encoding="utf-8")


def save_blogger_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_config_dir() / "blogger-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # Restrict permissions (no-op on Windows)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
