"""Offline-readiness checks for publishing adapters.

Extracted from ``adapters/__init__.py`` so the dispatcher stays focused on
registry wiring. Holds the per-platform ``_check_*_setup`` probes, the
``_SETUP_CHECKS`` dispatch table, and ``_verify_offline_setup`` — the
credential/binding probe formerly inlined in ``verify_adapter_setup``'s
``mode == "offline"`` branch. Behaviour preserved verbatim.
"""

from __future__ import annotations

from typing import Callable

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config import Config

from ..registry import _REGISTRY, registered_platforms
from .devto_api import DevtoAPIAdapter
from .gitlabpages import GitLabPagesAPIAdapter
from .hackmd_api import HackmdAPIAdapter
from .hatena_atompub import HatenaAtomPubAdapter
from .mataroa_api import MataroaAPIAdapter
from .qiita_api import QiitaAPIAdapter
from .zenn_github import ZennGitHubAdapter
from .notion_api import NotionAPIAdapter
from .telegraph_api import verify_telegraph_setup


def _check_medium_setup(config: Config) -> str | None:
    from backlink_publisher.config import load_medium_token
    from backlink_publisher.config.tokens import load_medium_integration_token

    has_oauth = bool(load_medium_token())
    it_data = load_medium_integration_token()
    has_it = bool(it_data and it_data.get("integration_token", "").strip())
    has_toml_it = bool(config.medium_integration_token)
    from .medium_browser import sync_playwright as _spw

    has_playwright = _spw is not None
    if not (has_it or has_toml_it or has_oauth or has_playwright):
        return (
            "Medium adapter not ready: no integration_token, no OAuth token file, "
            "and Playwright is not installed. "
            "Run 'playwright install chromium' or configure a token in /settings."
        )
    return None


def _check_ghpages_setup(config: Config) -> str | None:
    if config.ghpages is None or not config.ghpages.repo:
        return (
            'GitHub Pages config missing. Add [ghpages] repo="owner/name" '
            "to ~/.config/backlink-publisher/config.toml"
        )
    if not config.ghpages_token_path.exists():
        return (
            "GitHub Pages PAT not stored. Write "
            f'{{"token": "<pat>"}} to {config.ghpages_token_path} '
            "(chmod 600). PAT needs Contents:Read+Write on the target repo."
        )
    return None


def _check_velog_setup(config: Config) -> str | None:
    velog_cfg = config.velog
    cookies_path = (
        velog_cfg.cookies_path
        if velog_cfg
        else config.config_dir / "velog-cookies.json"
    )
    if not cookies_path.exists():
        return f"velog cookies not found: {cookies_path}\nRun: velog-login"
    return None


_SETUP_CHECKS: dict[str, Callable[[Config], str | None]] = {
    "blogger": lambda c: (
        None
        if c.blogger_oauth
        else "Blogger OAuth not configured. "
        "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
    ),
    "medium": _check_medium_setup,
    "telegraph": lambda c: _check_telegraph_setup(c),
    "velog": _check_velog_setup,
    "ghpages": _check_ghpages_setup,
    "notion": lambda c: (
        None
        if NotionAPIAdapter.available(c)
        else (
            "Notion integration token or database_id not configured. "
            f'Write {{"integration_token": "secret_...", "database_id": "..."}} '
            f"to {c.notion_token_path} (chmod 600). "
            "Create an Integration at https://www.notion.so/my-integrations."
        )
    ),
    "devto": lambda c: (
        None
        if DevtoAPIAdapter.available(c)
        else (
            "Dev.to API key not configured. "
            f'Write {{"api_key": "<key>"}} to {c.devto_token_path} '
            "(chmod 600). Generate at https://dev.to/settings/extensions."
        )
    ),
    "hackmd": lambda c: (
        None
        if HackmdAPIAdapter.available(c)
        else (
            "HackMD API token not configured. "
            f'Write {{"token": "<token>"}} to {c.hackmd_token_path} '
            "(chmod 600). Generate at HackMD → Settings → API → Create token."
        )
    ),
    "mataroa": lambda c: (
        None
        if MataroaAPIAdapter.available(c)
        else (
            "Mataroa API token not configured. "
            f'Write {{"token": "<token>"}} to {c.mataroa_token_path} '
            "(chmod 600). Enable at mataroa.blog → account settings → API."
        )
    ),
    "qiita": lambda c: (
        None
        if QiitaAPIAdapter.available(c)
        else (
            "Qiita personal access token not configured. "
            f'Write {{"token": "<token>"}} to {c.qiita_token_path} '
            "(chmod 600). Generate at qiita.com → Settings → Applications "
            "→ New token (read_qiita + write_qiita scopes)."
        )
    ),
    "zenn": lambda c: (
        None
        if ZennGitHubAdapter.available(c)
        else (
            "Zenn not configured. Requires: (1) [zenn] section in config.toml "
            "with github_repo = \"owner/repo\" and username = \"your-zenn-username\"; "
            f'(2) {{"token": "<github-pat>"}} at {c.zenn_token_path} '
            "(chmod 600, contents:write scope on your Zenn-connected repo)."
        )
    ),
    "gitlabpages": lambda c: (
        None
        if GitLabPagesAPIAdapter.available(c)
        else (
            'GitLab Pages not configured. Add [gitlabpages] project="namespace/name" '
            f'to config.toml and write {{"token": "<pat>"}} to {c.gitlabpages_token_path} '
            "(chmod 600, `api` scope). PRECONDITION: the target project must already "
            "have a `pages` CI job emitting public/ — committing a file does not "
            "publish without it."
        )
    ),
    "hatena": lambda c: (
        None
        if HatenaAtomPubAdapter.available(c)
        else (
            "Hatena credentials not configured. Write "
            '{"hatena_id": "...", "blog_id": "...", "api_key": "..."} to '
            f"{c.config_dir / 'hatena-credentials.json'} (chmod 600). "
            "API key: Hatena Blog → Settings → Advanced → AtomPub."
        )
    ),
}


def _check_telegraph_setup(config: Config) -> str | None:
    try:
        verify_telegraph_setup(config)
        return None
    except DependencyError as e:
        return str(e)


def _verify_offline_setup(platform: str, config: Config) -> None:
    # dispatch table first, then registry-driven fallback
    check = _SETUP_CHECKS.get(platform)
    if check is not None:
        error = check(config)
        if error:
            raise DependencyError(error)
        return None

    # ── Plan 2026-05-26-002 Unit 1: registry-driven fallback ──────────────
    # Platforms not in _SETUP_CHECKS delegate to their adapter chain's
    # ``available(config)`` — EXCEPT two whose ``available()`` does not reflect
    # per-account binding and would false-positive as "bound":
    #   • livejournal — USERPASS adapter inherits base ``available()`` (always
    #     True); probe its stored credential file instead.
    #   • mastodon    — chrome dispatcher gates on environment, not login;
    #     probe its per-channel Chrome profile instead.
    # Delegating ``available()`` is correct for the rest: the credential
    # adapters return False when unconfigured, and the ANON adapters
    # (txtfyi/rentry) return True ("免绑定·就绪"). Replaces the old terminal
    # raise that misreported 20 registered channels as "No adapter configured".
    if platform not in registered_platforms():
        raise DependencyError(f"No adapter configured for platform: {platform}")

    if platform == "livejournal":
        cred = config.config_dir / "livejournal-credentials.json"
        if cred.exists():
            return
        raise DependencyError(
            "LiveJournal not bound: no stored credentials. Save "
            f'{{"username": "...", "hpassword": "..."}} to {cred} '
            "(use a throwaway account — the secret is password-equivalent)."
        )

    if platform == "mastodon":
        profile = config.config_dir / "real-chrome-profile" / "mastodon"
        if profile.exists() and any(profile.iterdir()):
            return
        raise DependencyError(
            f"Mastodon not bound: no Chrome login profile at {profile}. "
            "Bind via browser login (set [mastodon] instance_url first)."
        )

    _entry = _REGISTRY.get(platform)
    chain = _entry.publishers if _entry else []
    for entry in chain:
        publisher_cls = entry if isinstance(entry, type) else type(entry)
        if publisher_cls.available(config):
            return
    raise DependencyError(f"{platform} not bound: credentials not configured.")
