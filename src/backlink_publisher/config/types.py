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
class HashnodeConfig:
    """Hashnode adapter configuration.

    Token is stored in a separate 0600 JSON file (``hashnode-token.json``),
    NOT in ``config.toml`` (same SEC-3 reasoning as ghpages). This dataclass
    holds only the non-secret routing fields.

    ``publication_id`` — the operator's Hashnode publication ID (UUID-like
                         string). Required for the publishPost mutation —
                         posts always belong to a publication, never a user
                         directly. Operators look this up via the Hashnode
                         dashboard URL or the ``me { publications }`` query.
    ``host`` — optional custom domain. When empty, posts publish under the
               default ``<subdomain>.hashnode.dev`` URL.
    """

    publication_id: str = ""
    host: str = ""


@dataclass(frozen=True)
class VelogConfig:
    """Velog adapter configuration.

    ``cookies_path`` points to the JSON file produced by ``velog-login``.
    Default: ``~/.config/backlink-publisher/velog-cookies.json``.

    The file must be 0600 — the adapter enforces this at load time.
    """

    cookies_path: Path = field(
        default_factory=lambda: Path.home()
        / ".config"
        / "backlink-publisher"
        / "velog-cookies.json"
    )


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
    label at link-rendering time. Used by the en/ru long-form path. Must be
    edited by hand — ``save_config`` does not write this section back."""

    site_url_categories: dict[str, dict[str, str]] = field(default_factory=dict)
    """Per-site URL category → URL mapping for the zh-CN short-form path.

    Schema: ``[main_domain][category_name] → URL``. ``category_name`` is one of
    ``home`` / ``hot`` / ``animate`` / ``category`` / ``topic`` (the scheduler
    treats this set as opaque — any string is accepted, but the scheduler
    requires at least ``home`` plus one non-``home`` category to engage).

    Populated from ``[sites."<main_domain>".url_categories]`` in config.toml.
    Not round-tripped by ``save_config`` — manual edit only."""

    target_anchor_pools_v2: dict[str, dict[str, dict[str, list[str]]]] = field(
        default_factory=dict,
    )
    """Per-site, per-(url_category, anchor_type) anchor candidate pool.

    Schema: ``[main_domain][url_category][anchor_type] → list[anchor_text]``.
    ``anchor_type`` is one of ``branded`` / ``partial`` / ``exact`` / ``lsi``.

    Empty inner pools are valid and signal the anchor resolver to fall back to
    LLM-generated candidates. Populated from
    ``[sites."<main_domain>".anchor_pools.<url_category>.<anchor_type>]`` in
    config.toml. Not round-tripped by ``save_config``."""

    anchor_proportions: dict[str, float] = field(
        default_factory=lambda: dict(_SAFE_SEO_PROPORTIONS),
    )
    """Target distribution for the anchor profile scheduler.

    Defaults to Safe SEO (Branded 55% / Partial 25% / Exact 10% / LSI 10%).
    Sum must equal 1.0 ± 0.001 — validated at load time. Override by setting
    ``[anchor.proportions]`` in config.toml. Not round-tripped by
    ``save_config``."""

    llm_anchor_provider: LLMProviderConfig | None = None
    """Optional OpenAI-compatible LLM provider used to generate anchor candidates
    when typed pools are empty for a given (url_category, anchor_type).

    Populated from ``[llm.anchor_provider]`` in config.toml. ``api_key`` is
    loaded with priority ``BACKLINK_LLM_API_KEY`` env var > toml value.
    ``base_url`` is required to use ``https://`` — ``http://`` raises
    ``InputValidationError`` at load time. Not round-tripped by ``save_config``."""

    target_three_url: dict[str, ThreeUrlConfig] = field(default_factory=dict)
    """Three-URL target config for the work-themed backlinks path (Plan
    2026-05-13-004 Unit 3). Keyed by main_domain with trailing slash stripped.
    Populated from ``[targets."<main_domain>"]`` entries that carry the
    required three-URL schema (``main_url`` + ``list_url`` + three non-empty
    pools). Round-tripped by ``save_config(target_three_url=...)``."""

    anchor_alarm: AnchorAlarmConfig = field(default_factory=AnchorAlarmConfig)
    """Operator-tunable thresholds for ``report-anchors`` distribution alarm.

    Populated from ``[anchor_alarm]`` in config.toml. Globals + per-target
    overrides. Not round-tripped by ``save_config`` — manual edit only.
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

    hashnode: HashnodeConfig | None = None
    """Hashnode adapter config (publication_id / host).

    Populated from ``[hashnode]`` in config.toml. ``None`` when section is
    absent. The PAT lives in a separate 0600 file at
    ``~/.config/backlink-publisher/hashnode-token.json`` (per SEC-3)."""

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
    def hashnode_token_path(self) -> Path:
        from backlink_publisher import config as _cfg
        return _cfg._config_dir() / "hashnode-token.json"

    @property
    def screenshot_dir(self) -> Path:
        from backlink_publisher import config as _cfg
        return _cfg._cache_dir() / "screenshots"
