"""TOML loader + parser dispatcher."""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

from backlink_publisher._util.errors import DependencyError

from .tokens import load_medium_integration_token
from .types import (
    BloggerOAuthConfig,
    Config,
    GhpagesConfig,
    GitlabPagesConfig,
    MastodonConfig,
    ZennConfig,
    MediumOAuthConfig,
    ThreeUrlConfig,
    VelogConfig,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from .parsers.alarm import _parse_anchor_alarm
from .parsers.anchor import _parse_anchor_proportions
from .parsers.cells import _parse_cell_assignments
from .parsers.geo import _parse_geo_probe_provider
from .parsers.image_gen import _parse_image_gen
from .parsers.llm import _llm_provider_from_sidecar, _parse_llm_anchor_provider
from .parsers.target import (
    _parse_target_anchor_keywords,
    _parse_target_anchor_pools_v2,
    _parse_target_string_list_field,
)
from .parsers.three_url import (
    _normalize_domain_key,
    _parse_site_url_categories,
    _parse_target_three_url,
)


def _resolve_config_dir() -> Path:
    """Indirect lookup so test monkeypatch on
    ``backlink_publisher.config._config_dir`` intercepts even when called
    from inside loader.py (where the local ``_config_dir`` would otherwise
    be a module-internal globals lookup, missed by the package-level patch)."""
    from backlink_publisher import config as _cfg

    return _cfg._config_dir()


_log = logging.getLogger(__name__)


_SANDBOX_SENTINEL = "BACKLINK_PUBLISHER_TEST_SANDBOX"
_FAIL_CLOSED_MSG = (
    "{override_key} is unset but {sentinel} is set — the test harness "
    "is active without a sandboxed {desc} directory. "
    "This usually means a subprocess was spawned without propagating the "
    "override env var. Fix: pass {override_key} to the child process, or "
    "unset {sentinel} if you are not running the test suite."
)


def _config_dir() -> Path:
    """Resolve the config directory.

    Honors ``BACKLINK_PUBLISHER_CONFIG_DIR`` when set so tests, CI, and
    containers can point at an isolated directory without touching the
    operator's real ``~/.config/backlink-publisher/``. Falls back to
    platform defaults otherwise.

    **Test-only fail-closed branch:** if the sentinel
    ``BACKLINK_PUBLISHER_TEST_SANDBOX`` is set but no override is configured,
    the call raises ``RuntimeError`` rather than silently resolving to the
    operator's real home. This catches subprocess spawns inside the test
    suite that forgot to propagate ``BACKLINK_PUBLISHER_CONFIG_DIR``.
    Production code is unaffected (the sentinel is never set outside tests).
    """
    override = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    if override:
        return Path(override)
    # Fail-closed in test-sandbox mode: no override + sentinel set → raise.
    if os.environ.get(_SANDBOX_SENTINEL):
        raise RuntimeError(
            _FAIL_CLOSED_MSG.format(
                override_key="BACKLINK_PUBLISHER_CONFIG_DIR",
                sentinel=_SANDBOX_SENTINEL,
                desc="config",
            )
        )
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".config"
    return base / "backlink-publisher"


def _cache_dir() -> Path:
    """Resolve the cache directory.

    Honors ``BACKLINK_PUBLISHER_CACHE_DIR`` for the same reasons as
    ``_config_dir`` — keeps ``~/.cache/backlink-publisher/`` (checkpoints,
    anchor profiles) untouched during tests.

    **Test-only fail-closed branch:** mirrors ``_config_dir()`` — raises when
    the sentinel is set but no cache override is configured.
    """
    override = os.environ.get("BACKLINK_PUBLISHER_CACHE_DIR")
    if override:
        return Path(override)
    # Fail-closed in test-sandbox mode.
    if os.environ.get(_SANDBOX_SENTINEL):
        raise RuntimeError(
            _FAIL_CLOSED_MSG.format(
                override_key="BACKLINK_PUBLISHER_CACHE_DIR",
                sentinel=_SANDBOX_SENTINEL,
                desc="cache",
            )
        )
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".cache"
    return base / "backlink-publisher"


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML file. Missing file → empty Config (not an error)."""
    config_path = path or (_resolve_config_dir() / "config.toml")
    if not config_path.exists():
        # No config.toml, but a WebUI-saved llm-settings.json sidecar (or
        # BACKLINK_LLM_* env) can still configure the provider. Same precedence
        # as the full path: env > (no TOML here) > sidecar.
        llm = _parse_llm_anchor_provider({}, config_path=config_path)
        if llm is None:
            llm = _llm_provider_from_sidecar(config_path.parent)
        return Config(llm_anchor_provider=llm)

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise DependencyError(
            f"Failed to parse config file {config_path}: {exc}"
        ) from exc

    _warn_if_loose_config_permissions(config_path, data)

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

    medium_oauth_section = medium_section.get("oauth", {})
    medium_oauth: MediumOAuthConfig | None = None
    if medium_oauth_section.get("client_id") and medium_oauth_section.get(
        "client_secret"
    ):
        medium_oauth = MediumOAuthConfig(
            client_id=medium_oauth_section["client_id"],
            client_secret=medium_oauth_section["client_secret"],
        )

    user_data_dir: Path | None = None
    if medium_browser_section.get("user_data_dir"):
        user_data_dir = Path(medium_browser_section["user_data_dir"])
    else:
        user_data_dir = _resolve_config_dir() / "chrome-profile-default"

    # blogger_section now contains only main_domain → blog_id mappings
    blog_ids = {
        k: str(v) for k, v in blogger_section.items() if isinstance(v, (str, int))
    }

    targets_section = data.get("targets", {})
    target_anchor_keywords = _parse_target_anchor_keywords(targets_section)
    target_three_url = _parse_target_three_url(targets_section)
    target_probe_queries = _parse_target_string_list_field(
        targets_section, "probe_queries"
    )
    target_brand_aliases = _parse_target_string_list_field(
        targets_section, "brand_aliases"
    )

    sites_section = data.get("sites", {})
    site_url_categories = _parse_site_url_categories(sites_section)
    target_anchor_pools_v2 = _parse_target_anchor_pools_v2(sites_section)

    # Maintenance-mode INFO: same domain has both legacy [sites."x"] and the
    # new three-URL [targets."x"] schema. Inform (not alarm) — both paths
    # continue to work; the dispatcher will prefer the work-themed flow.
    for domain_key in target_three_url:
        if domain_key in site_url_categories or domain_key in target_anchor_pools_v2:
            _log.info(
                "[sites.%r] is in maintenance mode; consider migrating to "
                "[targets.%r] three-URL form",
                domain_key,
                domain_key,
            )

    anchor_proportions = _parse_anchor_proportions(data.get("anchor", {}))

    llm_anchor_provider = _parse_llm_anchor_provider(
        data.get("llm", {}).get("anchor_provider", {}),
        config_path=config_path,
    )
    # Fallback: when neither env vars nor the TOML section configured a provider
    # (so the parser returned None), use the WebUI's ``llm-settings.json`` sidecar
    # next to config.toml. This is what makes the WebUI "Pro Mode" toggle drive
    # real publish runs. Precedence: env > TOML > sidecar (the parser already
    # consumed env/TOML, so reaching here means both were empty).
    if llm_anchor_provider is None:
        llm_anchor_provider = _llm_provider_from_sidecar(config_path.parent)

    geo_probe_provider = _parse_geo_probe_provider(
        data.get("geo", {}).get("probe_provider", {}),
        config_path=config_path,
    )

    anchor_alarm = _parse_anchor_alarm(data.get("anchor_alarm"))

    velog_section = data.get("velog")  # None when section absent
    velog: VelogConfig | None = None
    if velog_section is not None:
        raw_path = velog_section.get("cookies_path", "")
        if raw_path == "":
            # [velog] present but cookies_path not set → use default
            velog = VelogConfig()
        else:
            velog = VelogConfig(cookies_path=Path(raw_path).expanduser())

    ghpages_section = data.get("ghpages")
    ghpages: GhpagesConfig | None = None
    if ghpages_section is not None:
        # PAT lives in ghpages-token.json (SEC-3) — only routing fields here.
        ghpages = GhpagesConfig(
            repo=str(ghpages_section.get("repo", "")),
            branch=str(ghpages_section.get("branch", "gh-pages")),
            path_template=str(
                ghpages_section.get("path_template", "_posts/{date}-{slug}.md")
            ),
        )

    gitlabpages_section = data.get("gitlabpages")
    gitlabpages: GitlabPagesConfig | None = None
    if gitlabpages_section is not None:
        # PAT lives in gitlabpages-token.json (SEC-3) — only routing fields here.
        gitlabpages = GitlabPagesConfig(
            project=str(gitlabpages_section.get("project", "")),
            branch=str(gitlabpages_section.get("branch", "main")),
            path_template=str(
                gitlabpages_section.get("path_template", "public/{slug}/index.html")
            ),
            pages_base_url=str(gitlabpages_section.get("pages_base_url", "")),
        )

    mastodon_section = data.get("mastodon")
    mastodon: MastodonConfig | None = None
    if mastodon_section is not None:
        mastodon = MastodonConfig(
            instance_url=str(mastodon_section.get("instance_url", "")),
        )

    zenn_section = data.get("zenn")
    zenn: ZennConfig | None = None
    if zenn_section is not None:
        zenn = ZennConfig(
            github_repo=str(zenn_section.get("github_repo", "")),
            username=str(zenn_section.get("username", "")),
            branch=str(zenn_section.get("branch", "main")),
        )

    image_gen = _parse_image_gen(data.get("image_gen"))

    cell_assignments = _parse_cell_assignments(data.get("cells"))

    return Config(
        blogger_blog_ids=blog_ids,
        blogger_oauth=blogger_oauth,
        medium_oauth=medium_oauth,
        medium_integration_token=_resolve_medium_integration_token(
            medium_section.get("integration_token")
        ),
        medium_user_data_dir=user_data_dir,
        target_anchor_keywords=target_anchor_keywords,
        site_url_categories=site_url_categories,
        target_anchor_pools_v2=target_anchor_pools_v2,
        anchor_proportions=anchor_proportions,
        llm_anchor_provider=llm_anchor_provider,
        target_three_url=target_three_url,
        geo_probe_provider=geo_probe_provider,
        target_probe_queries=target_probe_queries,
        target_brand_aliases=target_brand_aliases,
        anchor_alarm=anchor_alarm,
        velog=velog,
        ghpages=ghpages,
        gitlabpages=gitlabpages,
        mastodon=mastodon,
        zenn=zenn,
        image_gen=image_gen,
        cell_assignments=cell_assignments,
    )


def _resolve_medium_integration_token(toml_value: str | None) -> str | None:
    """Resolve Medium integration token: token file (0600) wins over TOML.

    SEC-3 migration: the integration token is being moved out of config.toml
    into ``medium-integration-token.json`` (0600). For backward compat, the
    TOML value still works but the token file takes precedence when present.
    """
    token_data = load_medium_integration_token()
    if token_data:
        token = token_data.get("integration_token", "").strip()
        if token:
            return token  # type: ignore[no-any-return]
    return toml_value


def _warn_if_loose_config_permissions(
    config_path: Path, raw_data: dict | None = None
) -> None:
    """Emit a warning if config.toml contains credentials but isn't 0600.

    Checks all credential-bearing sections: ``[llm].anchor_provider.api_key``,
    ``[geo.probe_provider].api_key``, ``[blogger.oauth]``, ``[medium.oauth]``,
    and ``[medium].integration_token``.
    No-op on Windows where POSIX permission bits aren't meaningful.
    """
    if os.name == "nt":
        return

    # Detect credential-bearing sections in the raw TOML data.
    sections: list[str] = []
    if raw_data:
        _llm = raw_data.get("llm", {})
        if _llm.get("anchor_provider", {}).get("api_key"):
            sections.append("[llm].api_key")
        if raw_data.get("geo", {}).get("probe_provider", {}).get("api_key"):
            sections.append("[geo.probe_provider].api_key")
        if raw_data.get("blogger", {}).get("oauth", {}).get("client_id"):
            sections.append("[blogger.oauth]")
        if raw_data.get("medium", {}).get("oauth", {}).get("client_id"):
            sections.append("[medium.oauth]")
        if raw_data.get("medium", {}).get("integration_token"):
            sections.append("[medium].integration_token")

    if not sections:
        return  # no credential sections → nothing to warn about

    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError:
        return
    if mode != 0o600:
        _log.warning(
            "config file %s has mode %s and contains credential sections %s; "
            "set permissions to 0600 (chmod 600) to prevent credential leakage",
            config_path,
            oct(mode),
            sections,
        )


def get_three_url_config(config: Config, main_domain: str) -> ThreeUrlConfig | None:
    """Return the work-themed ``ThreeUrlConfig`` for ``main_domain`` if any.

    Tolerates trailing-slash variants in the lookup key — matches
    ``get_anchor_keywords``'s scheme-tolerance contract.
    """
    bare = _normalize_domain_key(main_domain)
    for candidate in (
        main_domain.rstrip("/"),
        "https://" + bare,
        "http://" + bare,
        bare,
    ):
        if candidate in config.target_three_url:
            return config.target_three_url[candidate]
    return None


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
