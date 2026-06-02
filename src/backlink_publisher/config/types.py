"""Config dataclasses and shared constants.

Pure data types. No I/O, no parsing — see ``loader.py``, ``writer.py``,
and ``parsers/`` for those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any  # noqa: F401  (preserved for downstream type hints)

# Anchor profile scheduler (zh-CN short-form) — type & proportion constants.
ANCHOR_TYPES: tuple[str, ...] = ("branded", "partial", "exact", "lsi")
_SAFE_SEO_PROPORTIONS: dict[str, float] = {
    "branded": 0.55,
    "partial": 0.25,
    "exact": 0.10,
    "lsi": 0.10,
}
_LLM_API_KEY_ENV_VAR = "BACKLINK_LLM_API_KEY"
_PROPORTIONS_SUM_TOLERANCE = 1e-3

# Publisher adapter constants
MEDIUM_API_BASE = "https://api.medium.com/v1"
MEDIUM_API_TIMEOUT = 30  # seconds
BLOGGER_LOCK_TIMEOUT_S = 30

DEFAULT_WORK_TEMPLATES: tuple[str, ...] = (
    "{title}",
    "{title} 详情",
    "{title} 推荐",
    "{title} 介绍",
)

_UNSAFE_IN_ANCHOR = re.compile(r'[\]\[()><"\'\n\r]')


@dataclass
class BloggerOAuthConfig:
    client_id: str
    client_secret: str


@dataclass
class MediumOAuthConfig:
    client_id: str
    client_secret: str


@dataclass
class ThreeUrlConfig:
    """Three-URL target config for the work-themed backlinks path.

    Required: ``main_url`` (https + host-root + trailing slash), ``list_url``,
    and three non-empty anchor pools (``branded_pool`` / ``partial_pool`` /
    ``exact_pool``).

    Optional: ``work_urls`` (when empty, Unit 2's ``work_scraper`` discovers
    them via sitemap / HTML fallback), ``work_anchor_templates`` (defaults to
    :data:`DEFAULT_WORK_TEMPLATES`), ``list_path_blocklist`` (overrides the
    scraper's default nav-path filter; ``None`` keeps the default),
    ``insecure_tls`` (opt-in TLS bypass for a target with broken certs).
    """

    main_url: str
    list_url: str
    branded_pool: list[str]
    partial_pool: list[str]
    exact_pool: list[str]
    work_urls: list[str] = field(default_factory=list)
    work_anchor_templates: list[str] = field(
        default_factory=lambda: list(DEFAULT_WORK_TEMPLATES)
    )
    list_path_blocklist: list[str] | None = None
    insecure_tls: bool = False


@dataclass
class LLMProviderConfig:
    """OpenAI-compatible LLM endpoint used to generate anchor-text candidates.

    The provider is optional — the anchor resolver falls back to config-pinned
    typed pools when this is unset. ``base_url`` MUST be ``https://`` (enforced
    at load time); ``api_key`` is preferentially loaded from the
    ``BACKLINK_LLM_API_KEY`` env var with the toml value as fallback.
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0
    temperature: float = 0.7
    system_prompt: str | None = None
    use_article_gen: bool = False
    article_system_prompt: str | None = None
    use_image_gen: bool = False
    image_gen_api_key: str | None = None

    # ── Deprecated image-gen fields (Plan 2026-05-20-001 Unit 1) ──────────
    #
    # ``use_image_gen`` was originally added to gate the cover-image
    # branch in ``plan_backlinks/core.py:531-547``.  That branch is
    # being migrated to ``Config.image_gen`` (the new ``[image_gen]``
    # section) in Unit 4; the field is retained here so the existing
    # call site short-circuits cleanly on the default value while the
    # migration is in flight.
    #
    # ``image_gen_api_key`` is fully deprecated — the key now lives
    # in ``frw-token.json`` (0600) per SEC-3.  Reading the field
    # raises ``DeprecationWarning`` at parse time; the field will be
    # removed once Unit 4 lands and no call sites remain.
    use_image_gen: bool = False  # type: ignore[no-redef]
    image_gen_api_key: str | None = None  # type: ignore[no-redef]


@dataclass
class GeoProbeConfig:
    """OpenAI-compatible AI-engine endpoint used to probe AI-answer citations.

    The provider is optional — GEO citation probing is an operator-invoked
    capability and the tool is fully runnable without it. ``base_url`` MUST be
    ``https://`` (enforced at load time); ``api_key`` is preferentially loaded
    from the ``BACKLINK_GEO_API_KEY`` env var with the toml value as fallback.

    There is **no** fallback to the LLM provider credential
    (``BACKLINK_LLM_API_KEY``) — a populated LLM key with no
    ``[geo.probe_provider]`` section never enables GEO probing (D0).
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 30.0


@dataclass(frozen=True)
class ImageGenConfig:
    """FRW image-gen (OpenAI-compatible ``/images/generations``) settings.

    Populated from ``[image_gen]`` in config.toml.  ``None`` when the
    section is absent — image-gen is opt-in.

    The API key is NOT modeled here; it lives in
    ``~/.config/backlink-publisher/frw-token.json`` (0600) per SEC-3
    (see ``backlink_publisher._util.secrets``).  Operator writes it
    via ``frw-login`` and never via ``save_config``.

    ``banner_size`` is operator-tunable to handle endpoints that don't
    support the OG ``1200x630`` aspect (some OpenAI-compatible
    gateways only ship square sizes).  Format ``WIDTHxHEIGHT``,
    validated at parse time.
    """

    base_url: str
    model: str
    banner_size: str = "1200x630"
    daily_cap: int = 50
    per_run_cap: int = 10
    timeout_s: float = 30.0
    max_retries: int = 3
    strict: bool = False
    auto_disable_threshold: int = 5
    use_image_gen: bool = True
    """Operator toggle. When ``False`` the adapter is wired but never
    invoked — useful for staging endpoints where banner generation is
    not yet authorized."""


@dataclass(frozen=True)
class GhpagesConfig:
    """GitHub Pages adapter configuration.

    Token is stored in a separate 0600 JSON file (``ghpages-token.json``),
    NOT in ``config.toml`` (per Plan 2026-05-19-006 SEC-3 — PAT is too
    sensitive to live in TOML which gets backed up/screenshotted/shared).
    This dataclass holds only the non-secret routing fields.

    ``repo`` — ``"owner/name"`` of the Pages-enabled repository.
    ``branch`` — the branch Pages publishes from (Jekyll default ``gh-pages``;
                 modern repos often use ``main`` with /docs or ``main`` root).
    ``path_template`` — where to place each post, with ``{date}`` and
                        ``{slug}`` placeholders.
    """

    repo: str = ""
    branch: str = "gh-pages"
    path_template: str = "_posts/{date}-{slug}.md"


@dataclass(frozen=True)
class GitlabPagesConfig:
    """GitLab Pages adapter configuration (Plan 2026-06-01-007 Wave 1).

    The PAT is stored in a separate 0600 JSON file (``gitlabpages-token.json``),
    NOT in ``config.toml`` (same SEC-3 rationale as ghpages). This dataclass
    holds only the non-secret routing fields.

    ``project`` — ``"namespace/project"`` (or numeric id) of the Pages project;
                  URL-encoded (``%2F``) at request time.
    ``branch`` — the default branch commits land on (fires the ``pages`` pipeline).
    ``path_template`` — where to place each post under ``public/`` (GitLab Pages
                  serves the ``public/`` artifact verbatim — no auto-Jekyll), with
                  ``{date}`` and ``{slug}`` placeholders.
    ``pages_base_url`` — optional published-URL base. Empty = derive
                  ``https://<namespace>.gitlab.io/<project>``; set this for the
                  unique-domain case (the 6-char id is not derivable offline).
    """

    project: str = ""
    branch: str = "main"
    path_template: str = "public/{slug}/index.html"
    pages_base_url: str = ""


@dataclass(frozen=True)
class ZennConfig:
    """Zenn adapter configuration (wave-2 discovery, 2026-06-01).

    Zenn publishes via a GitHub repository connected to the operator's Zenn
    account. Articles are pushed as Markdown files to ``articles/<slug>.md``.
    The GitHub PAT is stored in ``zenn-token.json`` (0600), NOT in config.toml.

    ``github_repo`` — ``"owner/repo"`` of the GitHub repository connected to Zenn.
    ``username``    — the operator's Zenn username (used to construct the article URL).
    ``branch``      — the branch to commit to (default ``main``).
    """

    github_repo: str = ""
    username: str = ""
    branch: str = "main"


@dataclass(frozen=True)
class MastodonConfig:
    """Mastodon adapter configuration — single Fediverse instance.

    Mastodon is decentralized: there is no single canonical host like
    ``hashnode.com``. The operator picks one instance (e.g.,
    ``https://mastodon.social``) and posts go there. Multi-instance
    support is a follow-up plan (each instance keeps its own bind state
    + per-instance Chrome profile).

    ``instance_url`` — fully qualified ``https://<host>`` of the
    Mastodon instance. Trailing slash optional. The chrome publish
    recipe appends ``/publish`` to drive the compose window.
    """

    instance_url: str = ""


def _velog_default_cookies_path() -> Path:
    """Lazy resolver for the default Velog cookies path.

    Uses the env-aware ``_config_dir()`` so tests and CI land in the
    sandbox, not the operator's real ``~/.config``.  The lambda in
    ``VelogConfig.cookies_path``'s ``default_factory`` calls this function
    at instance-creation time, never at import time.
    """
    from backlink_publisher import config as _cfg

    return _cfg._config_dir() / "velog-cookies.json"


@dataclass(frozen=True)
class VelogConfig:
    """Velog adapter configuration.

    ``cookies_path`` points to the JSON file produced by ``velog-login``.
    Default: ``~/.config/backlink-publisher/velog-cookies.json``.

    The file must be 0600 — the adapter enforces this at load time.
    """

    cookies_path: Path = field(default_factory=lambda: _velog_default_cookies_path())


@dataclass(frozen=True)
class AnchorAlarmOverride:
    """One override row in ``[[anchor_alarm.override]]``.

    Matches a target by ``scope``: ``"url"`` matches the entry's full
    ``target_url``; ``"domain"`` matches its ``main_domain``. ``match`` is the
    exact string compared (no glob/regex — keep config behavior obvious).

    Each threshold field is optional; ``None`` means "fall through to the next
    precedence layer for this field". A row with all three fields ``None`` is
    rejected as a config error (it would have no effect — almost certainly a
    typo).
    """

    match: str
    scope: str  # "url" | "domain"
    entropy_floor: float | None = None
    exact_ratio_ceiling: float | None = None
    top3_concentration_ceiling: float | None = None


@dataclass
class AnchorAlarmConfig:
    """Operator-tunable thresholds for the anchor distribution alarm.

    Three global defaults plus an ordered list of overrides. Resolution
    precedence per target (highest wins): per-target-URL > per-`main_domain` >
    these globals > hardcoded constants in ``anchor_metrics``. Partial-field
    overrides fall through layer-by-layer.

    Defaults of ``None`` mean "use the hardcoded constants from
    ``anchor_metrics``". Setting any value here overrides only that field.
    """

    entropy_floor: float | None = None
    exact_ratio_ceiling: float | None = None
    top3_concentration_ceiling: float | None = None
    overrides: list[AnchorAlarmOverride] = field(default_factory=list)


@dataclass
class Config:
    blogger_blog_ids: dict[str, str] = field(default_factory=dict)
    blogger_oauth: BloggerOAuthConfig | None = None
    medium_oauth: MediumOAuthConfig | None = None
    medium_integration_token: str | None = None
    medium_user_data_dir: Path | None = None
    target_anchor_keywords: dict[str, list[str]] = field(default_factory=dict)
    """Per-target SEO anchor keyword pool, keyed by main_domain (trailing slash
    stripped). Populated from ``[targets."<main_domain>"].anchor_keywords`` in
    config.toml. Empty pool / missing entry triggers fallback to bare domain
    label at link-rendering time. Used by the en/ru long-form path.
    Round-tripped by ``save_config(target_anchor_keywords=...)``."""

    site_url_categories: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per-site URL category → URL mapping for the zh-CN short-form path.

    Schema: ``[main_domain][category_name] → URL``. ``category_name`` is one of
    ``home`` / ``hot`` / ``animate`` / ``category`` / ``topic`` (the scheduler
    treats this set as opaque — any string is accepted, but the scheduler
    requires at least ``home`` plus one non-``home`` category to engage).

    Populated from ``[sites."<main_domain>".url_categories]`` in config.toml.
    Operator-edit-only; not modeled in ``Config`` for rewrite. Preserved
    verbatim by ``save_config`` (unmanaged root). See also
    :func:`merge_site_url_categories` for in-place key updates."""

    target_anchor_pools_v2: dict[str, dict[str, dict[str, list[str]]]] = field(
        default_factory=dict,
    )
    """Per-site, per-(url_category, anchor_type) anchor candidate pool.

    Schema: ``[main_domain][url_category][anchor_type] → list[anchor_text]``.
    ``anchor_type`` is one of ``branded`` / ``partial`` / ``exact`` / ``lsi``.

    Empty inner pools are valid and signal the anchor resolver to fall back to
    LLM-generated candidates. Populated from
    ``[sites."<main_domain>".anchor_pools.<url_category>.<anchor_type>]`` in
    config.toml. Operator-edit-only; not modeled in ``Config`` for rewrite.
    Preserved verbatim by ``save_config`` (unmanaged root)."""

    anchor_proportions: dict[str, float] = field(
        default_factory=lambda: dict(_SAFE_SEO_PROPORTIONS),
    )
    """Target distribution for the anchor profile scheduler.

    Defaults to Safe SEO (Branded 55% / Partial 25% / Exact 10% / LSI 10%).
    Sum must equal 1.0 ± 0.001 — validated at load time. Override by setting
    ``[anchor.proportions]`` in config.toml. Operator-edit-only; not modeled
    in ``Config`` for rewrite. Preserved verbatim by ``save_config``
    (unmanaged root)."""

    llm_anchor_provider: LLMProviderConfig | None = None
    """Optional OpenAI-compatible LLM provider used to generate anchor candidates
    when typed pools are empty for a given (url_category, anchor_type).

    Populated from ``[llm.anchor_provider]`` in config.toml. ``api_key`` is
    loaded with priority ``BACKLINK_LLM_API_KEY`` env var > toml value.
    ``base_url`` is required to use ``https://`` — ``http://`` raises
    ``InputValidationError`` at load time. Operator-edit-only; not modeled
    in ``Config`` for rewrite. Preserved verbatim by ``save_config``
    (unmanaged root)."""

    target_three_url: dict[str, ThreeUrlConfig] = field(default_factory=dict)
    """Three-URL target config for the work-themed backlinks path (Plan
    2026-05-13-004 Unit 3). Keyed by main_domain with trailing slash stripped.
    Populated from ``[targets."<main_domain>"]`` entries that carry the
    required three-URL schema (``main_url`` + ``list_url`` + three non-empty
    pools). Round-tripped by ``save_config(target_three_url=...)``."""

    geo_probe_provider: "GeoProbeConfig | None" = None
    """Optional OpenAI-compatible AI-engine provider used by the GEO citation
    probe (Plan 2026-05-29-006 Unit 1). ``None`` when the section is absent —
    GEO probing is operator-invoked and the tool is fully runnable without it.

    Populated from ``[geo.probe_provider]`` in config.toml. ``api_key`` is
    loaded with priority ``BACKLINK_GEO_API_KEY`` env var > toml value (there is
    **no** fallback to the LLM key — D0). ``base_url`` is required to use
    ``https://`` — ``http://`` raises ``InputValidationError`` at load time.
    Operator-edit-only; not modeled in ``Config`` for rewrite. Preserved
    verbatim by ``save_config`` (unmanaged root)."""

    target_probe_queries: dict[str, list[str]] = field(default_factory=dict)
    """Per-target GEO probe-query overrides, keyed by main_domain (trailing
    slash stripped). Populated from ``[targets."<main_domain>"].probe_queries``
    in config.toml. Empty / missing entry means the probe derives queries from
    ``seed_keywords`` / ``topic`` instead (R3). Round-tripped by
    ``save_config(target_probe_queries=...)`` — emitted inside the writer's
    ``[targets.*]`` regeneration loop."""

    target_brand_aliases: dict[str, list[str]] = field(default_factory=dict)
    """Per-target brand aliases for the ``brand_mentioned`` GEO signal tier,
    keyed by main_domain (trailing slash stripped). Populated from
    ``[targets."<main_domain>"].brand_aliases`` in config.toml. Empty / missing
    entry renders the brand-mention tier inert for that target. Round-tripped by
    ``save_config(target_brand_aliases=...)`` — emitted inside the writer's
    ``[targets.*]`` regeneration loop."""

    anchor_alarm: AnchorAlarmConfig = field(default_factory=AnchorAlarmConfig)
    """Operator-tunable thresholds for ``report-anchors`` distribution alarm.

    Populated from ``[anchor_alarm]`` in config.toml. Globals + per-target
    overrides. Operator-edit-only; not modeled in ``Config`` for rewrite.
    Preserved verbatim by ``save_config`` (unmanaged root).
    See ``anchor_metrics.resolve_thresholds`` for precedence rules."""

    velog: VelogConfig | None = None
    """Velog adapter config (cookies path).

    Populated from ``[velog]`` in config.toml. ``None`` when section is
    absent — the adapter will use its default path
    ``~/.config/backlink-publisher/velog-cookies.json``."""

    ghpages: GhpagesConfig | None = None
    """GitHub Pages adapter config (repo / branch / path_template).

    Populated from ``[ghpages]`` in config.toml. ``None`` when section is
    absent. The PAT lives in a separate 0600 file at
    ``~/.config/backlink-publisher/ghpages-token.json`` (per SEC-3)."""

    gitlabpages: GitlabPagesConfig | None = None
    """GitLab Pages adapter config (project / branch / path_template / pages_base_url).

    Populated from ``[gitlabpages]`` in config.toml. ``None`` when section is
    absent. The PAT lives in a separate 0600 file at
    ``~/.config/backlink-publisher/gitlabpages-token.json`` (per SEC-3)."""

    mastodon: "MastodonConfig | None" = None
    """Mastodon adapter config (single Fediverse instance URL).

    Populated from ``[mastodon]`` in config.toml. ``None`` when section
    is absent — the chrome publish recipe raises ``DependencyError`` if
    asked to compose without an ``instance_url``. Single-instance only
    in Plan 2026-05-21-001 Unit 4c; multi-instance is a follow-up
    (per-instance worktree with per-instance bind state)."""

    zenn: "ZennConfig | None" = None
    """Zenn adapter config (github_repo / username / branch).

    Populated from ``[zenn]`` in config.toml. ``None`` when section is
    absent. The GitHub PAT lives in a separate 0600 file at
    ``~/.config/backlink-publisher/zenn-token.json`` (per SEC-3)."""

    image_gen: ImageGenConfig | None = None
    """AI banner image-gen settings (Plan 2026-05-20-001).

    Populated from ``[image_gen]`` in config.toml. ``None`` when the
    section is absent — image-gen is opt-in. The API key lives in a
    separate 0600 file at ``~/.config/backlink-publisher/frw-token.json``
    (per SEC-3); use ``frw-login`` to write it."""

    cell_assignments: dict[str, list[str]] = field(default_factory=dict)
    """Money-site → allowed channel subset for blast-radius containment (R7).

    Populated from ``[cells."<main_domain>"]`` blocks in config.toml::

        [cells."https://example.com"]
        channels = ["telegraph", "rentry"]

    Keyed by ``main_domain`` (trailing slash stripped, consistent with
    other per-domain config keys). Empty dict means "no cells configured;
    all sites are unrestricted" (opt-in semantics).

    Operator-edit-only; not managed by ``save_config`` (unmanaged root —
    preserved verbatim by ``_preserve_unknown_sections``).

    Validated at load time: unknown channel names and cross-cell overlap
    both raise ``InputValidationError`` (fail-loud, not skip-with-warning).
    See ``config/parsers/cells.py`` and Blast-radius Phase 1 R7-minimal."""

    @property
    def frw_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "frw-token.json"

    @property
    def config_dir(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir()

    @property
    def cache_dir(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._cache_dir()

    @property
    def blogger_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "blogger-token.json"

    @property
    def ghpages_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "ghpages-token.json"

    @property
    def notion_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "notion-token.json"

    @property
    def wordpresscom_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "wordpresscom-token.json"

    @property
    def hashnode_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "hashnode-token.json"

    @property
    def writeas_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "writeas-token.json"

    @property
    def tumblr_credentials_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "tumblr-credentials.json"

    @property
    def devto_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "devto-token.json"

    @property
    def hackmd_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "hackmd-token.json"

    @property
    def mataroa_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "mataroa-token.json"

    @property
    def gitlabpages_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "gitlabpages-token.json"

    @property
    def qiita_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "qiita-token.json"

    @property
    def zenn_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "zenn-token.json"

    @property
    def linkedin_token_path(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._config_dir() / "linkedin-token.json"

    @property
    def screenshot_dir(self) -> Path:
        from backlink_publisher import config as _cfg

        return _cfg._cache_dir() / "screenshots"
