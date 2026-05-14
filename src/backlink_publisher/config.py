"""User config loader for backlink-publisher.

Config file: ~/.config/backlink-publisher/config.toml
Token file:  ~/.config/backlink-publisher/blogger-token.json
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import DependencyError, InputValidationError

_log = logging.getLogger(__name__)
_UNSAFE_IN_ANCHOR = re.compile(r'[\]\[()><"\'\n\r]')

# Anchor profile scheduler (zh-CN short-form) — type & proportion constants.
# These live module-level so other consumers (scheduler, resolver, validator)
# import a single source of truth.
ANCHOR_TYPES: tuple[str, ...] = ("branded", "partial", "exact", "lsi")
_SAFE_SEO_PROPORTIONS: dict[str, float] = {
    "branded": 0.55,
    "partial": 0.25,
    "exact": 0.10,
    "lsi": 0.10,
}
_LLM_API_KEY_ENV_VAR = "BACKLINK_LLM_API_KEY"
_PROPORTIONS_SUM_TOLERANCE = 1e-3

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
class MediumOAuthConfig:
    client_id: str
    client_secret: str


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

    anchor_alarm: AnchorAlarmConfig = field(default_factory=AnchorAlarmConfig)
    """Operator-tunable thresholds for ``report-anchors`` distribution alarm.

    Populated from ``[anchor_alarm]`` in config.toml. Globals + per-target
    overrides. Not round-tripped by ``save_config`` — manual edit only.
    See ``anchor_metrics.resolve_thresholds`` for precedence rules."""

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

    medium_oauth_section = medium_section.get("oauth", {})
    medium_oauth: MediumOAuthConfig | None = None
    if medium_oauth_section.get("client_id") and medium_oauth_section.get("client_secret"):
        medium_oauth = MediumOAuthConfig(
            client_id=medium_oauth_section["client_id"],
            client_secret=medium_oauth_section["client_secret"],
        )

    user_data_dir: Path | None = None
    if medium_browser_section.get("user_data_dir"):
        user_data_dir = Path(medium_browser_section["user_data_dir"])
    else:
        user_data_dir = _config_dir() / "chrome-profile-default"

    # blogger_section now contains only main_domain → blog_id mappings
    blog_ids = {k: str(v) for k, v in blogger_section.items() if isinstance(v, (str, int))}

    target_anchor_keywords = _parse_target_anchor_keywords(data.get("targets", {}))

    sites_section = data.get("sites", {})
    site_url_categories = _parse_site_url_categories(sites_section)
    target_anchor_pools_v2 = _parse_target_anchor_pools_v2(sites_section)

    anchor_proportions = _parse_anchor_proportions(data.get("anchor", {}))

    llm_anchor_provider = _parse_llm_anchor_provider(
        data.get("llm", {}).get("anchor_provider", {}),
        config_path=config_path,
    )

    anchor_alarm = _parse_anchor_alarm(data.get("anchor_alarm"))

    return Config(
        blogger_blog_ids=blog_ids,
        blogger_oauth=blogger_oauth,
        medium_oauth=medium_oauth,
        medium_integration_token=medium_section.get("integration_token") or None,
        medium_user_data_dir=user_data_dir,
        target_anchor_keywords=target_anchor_keywords,
        site_url_categories=site_url_categories,
        target_anchor_pools_v2=target_anchor_pools_v2,
        anchor_proportions=anchor_proportions,
        llm_anchor_provider=llm_anchor_provider,
        anchor_alarm=anchor_alarm,
    )


def _parse_target_anchor_keywords(targets_section: Any) -> dict[str, list[str]]:
    """Parse ``[targets."<main_domain>"].anchor_keywords`` entries.

    Tolerant of missing / malformed entries — invalid entries are skipped with a
    warning rather than aborting the whole config load. Keys are normalised by
    stripping trailing slashes so lookups work regardless of how the user wrote
    the domain.
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, list[str]] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            _log.warning(
                "[targets.%r] is not a table, skipping", raw_domain,
            )
            continue
        keywords = entry.get("anchor_keywords")
        if keywords is None:
            continue
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            _log.warning(
                "[targets.%r].anchor_keywords must be a list of strings, skipping",
                raw_domain,
            )
            continue
        # Strip characters that would break Markdown link syntax or inject HTML.
        # Brackets, parens, angle-brackets, newlines can corrupt [anchor](url) output.
        cleaned = [_UNSAFE_IN_ANCHOR.sub("", k).strip() for k in keywords]
        cleaned = [k for k in cleaned if k]  # drop any that became empty after cleaning
        key = raw_domain.rstrip("/")
        result[key] = cleaned
    return result


def _parse_site_url_categories(sites_section: Any) -> dict[str, dict[str, str]]:
    """Parse ``[sites."<main_domain>".url_categories]`` entries.

    Each URL must be a string starting with ``http://`` or ``https://``;
    malformed entries are skipped with a warning rather than raising.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        categories = entry.get("url_categories")
        if categories is None:
            continue
        if not isinstance(categories, dict):
            _log.warning(
                "[sites.%r].url_categories must be a table, skipping", raw_domain,
            )
            continue
        cleaned: dict[str, str] = {}
        for cat_name, cat_url in categories.items():
            if not isinstance(cat_name, str) or not isinstance(cat_url, str):
                continue
            if not re.match(r"^https?://", cat_url):
                _log.warning(
                    "[sites.%r].url_categories.%r is not a valid URL, skipping",
                    raw_domain, cat_name,
                )
                continue
            cleaned[cat_name] = cat_url
        if cleaned:
            result[raw_domain.rstrip("/")] = cleaned
    return result


def _parse_target_anchor_pools_v2(
    sites_section: Any,
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Parse ``[sites."<main_domain>".anchor_pools.<url_cat>.<anchor_type>]``.

    Schema-strict: ``anchor_type`` must be one of ``ANCHOR_TYPES``; lists must
    be ``list[str]``. Pool entries are run through ``_UNSAFE_IN_ANCHOR`` to
    strip characters that would break Markdown/HTML link syntax — same hygiene
    contract as the legacy ``target_anchor_keywords`` parser.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        pools = entry.get("anchor_pools")
        if pools is None:
            continue
        if not isinstance(pools, dict):
            _log.warning(
                "[sites.%r].anchor_pools must be a table, skipping", raw_domain,
            )
            continue
        site_pools: dict[str, dict[str, list[str]]] = {}
        for url_cat, type_table in pools.items():
            if not isinstance(type_table, dict):
                continue
            cat_pools: dict[str, list[str]] = {}
            for anchor_type, words in type_table.items():
                if anchor_type not in ANCHOR_TYPES:
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s is not a known anchor "
                        "type (expected one of %s), skipping",
                        raw_domain, url_cat, anchor_type, ANCHOR_TYPES,
                    )
                    continue
                if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s must be a list of strings, skipping",
                        raw_domain, url_cat, anchor_type,
                    )
                    continue
                cleaned = [_UNSAFE_IN_ANCHOR.sub("", w).strip() for w in words]
                cleaned = [w for w in cleaned if w]
                cat_pools[anchor_type] = cleaned
            if cat_pools:
                site_pools[url_cat] = cat_pools
        if site_pools:
            result[raw_domain.rstrip("/")] = site_pools
    return result


def _parse_anchor_proportions(anchor_section: Any) -> dict[str, float]:
    """Parse ``[anchor.proportions]``; default to Safe SEO if absent.

    Validates that the four anchor types are covered and their sum is ~1.0.
    Raises ``InputValidationError`` on schema or sum violations — anchor
    distribution is load-bearing for the scheduler, silent fall-through would
    mask configuration bugs.
    """
    if not isinstance(anchor_section, dict):
        return dict(_SAFE_SEO_PROPORTIONS)
    proportions_section = anchor_section.get("proportions")
    if proportions_section is None:
        return dict(_SAFE_SEO_PROPORTIONS)
    if not isinstance(proportions_section, dict):
        raise InputValidationError(
            "[anchor.proportions] must be a table mapping anchor type → float"
        )
    # Start from Safe SEO and let toml keys override individual values; that
    # lets users tweak one slot without restating the whole map.
    result: dict[str, float] = dict(_SAFE_SEO_PROPORTIONS)
    for key, value in proportions_section.items():
        if key == "preset":
            # Only "safe_seo" is implemented; reject unknown presets explicitly.
            if value != "safe_seo":
                raise InputValidationError(
                    f"[anchor.proportions].preset = {value!r} is unknown "
                    f'(supported: "safe_seo")'
                )
            continue
        if key not in ANCHOR_TYPES:
            raise InputValidationError(
                f"[anchor.proportions].{key} is not a known anchor type "
                f"(expected one of {ANCHOR_TYPES})"
            )
        if not isinstance(value, (int, float)):
            raise InputValidationError(
                f"[anchor.proportions].{key} must be a number, got {type(value).__name__}"
            )
        result[key] = float(value)
    total = sum(result.values())
    if abs(total - 1.0) > _PROPORTIONS_SUM_TOLERANCE:
        raise InputValidationError(
            f"[anchor.proportions] values must sum to 1.0 ± {_PROPORTIONS_SUM_TOLERANCE} "
            f"(got {total:.4f}). Values: {result!r}"
        )
    return result


_ANCHOR_ALARM_THRESHOLD_FIELDS: tuple[str, ...] = (
    "entropy_floor",
    "exact_ratio_ceiling",
    "top3_concentration_ceiling",
)


def _coerce_threshold(section_label: str, key: str, value: Any) -> float:
    """Coerce a threshold scalar; raise ``InputValidationError`` on bad input.

    Anchor-alarm thresholds are non-load-bearing for publish-flow correctness,
    but silent fall-through still masks operator typos — we mirror
    ``_parse_anchor_proportions``'s raise-loud posture. Better to surface a
    config bug at load time than to ship with the operator's intent silently
    ignored.
    """
    if isinstance(value, bool):
        # bool is a subclass of int — reject explicitly to catch obvious typos.
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got bool ({value!r})"
        )
    if not isinstance(value, (int, float)):
        raise InputValidationError(
            f"[{section_label}].{key} must be a number, got {type(value).__name__}"
        )
    f = float(value)
    if not math.isfinite(f):
        raise InputValidationError(
            f"[{section_label}].{key} must be finite, got {value!r}"
        )
    if key == "entropy_floor":
        if f < 0:
            raise InputValidationError(
                f"[{section_label}].entropy_floor must be ≥ 0, got {f!r}"
            )
    else:
        # Ratio / concentration fields are bounded to [0, 1].
        if not (0.0 <= f <= 1.0):
            raise InputValidationError(
                f"[{section_label}].{key} must be in [0.0, 1.0], got {f!r}"
            )
    return f


def _parse_anchor_alarm(section: Any) -> AnchorAlarmConfig:
    """Parse ``[anchor_alarm]`` section. Missing → defaults (no overrides).

    Raises ``InputValidationError`` on malformed input — typos in a threshold
    key, non-numeric values, unknown scope, or an override row whose every
    threshold field is absent (a row with no effect is almost certainly a
    config mistake).
    """
    if section is None:
        return AnchorAlarmConfig()
    if not isinstance(section, dict):
        raise InputValidationError(
            f"[anchor_alarm] must be a table, got {type(section).__name__}"
        )

    cfg = AnchorAlarmConfig()
    overrides_raw = section.get("override")

    # Pull globals out of the section.
    for key, value in section.items():
        if key == "override":
            continue
        if key not in _ANCHOR_ALARM_THRESHOLD_FIELDS:
            raise InputValidationError(
                f"[anchor_alarm].{key} is not a known threshold "
                f"(expected one of {_ANCHOR_ALARM_THRESHOLD_FIELDS} or 'override')"
            )
        coerced = _coerce_threshold("anchor_alarm", key, value)
        setattr(cfg, key, coerced)

    # Parse overrides. TOML maps [[anchor_alarm.override]] to a list of dicts.
    if overrides_raw is not None:
        if not isinstance(overrides_raw, list):
            raise InputValidationError(
                "[[anchor_alarm.override]] must be an array of tables"
            )
        parsed: list[AnchorAlarmOverride] = []
        for i, row in enumerate(overrides_raw):
            if not isinstance(row, dict):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} must be a table"
                )
            match = row.get("match")
            scope = row.get("scope")
            if not isinstance(match, str) or not match:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'match' is required (non-empty string)"
                )
            if scope not in ("url", "domain"):
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i}: 'scope' must be 'url' or 'domain', got {scope!r}"
                )
            kwargs: dict[str, float | None] = {}
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                if f_name in row:
                    kwargs[f_name] = _coerce_threshold(
                        f"anchor_alarm.override[{i}]", f_name, row[f_name]
                    )
            if not kwargs:
                raise InputValidationError(
                    f"[[anchor_alarm.override]] row {i} sets no threshold fields — "
                    f"row would have no effect. Add at least one of "
                    f"{_ANCHOR_ALARM_THRESHOLD_FIELDS}, or delete the row."
                )
            for f_name in _ANCHOR_ALARM_THRESHOLD_FIELDS:
                kwargs.setdefault(f_name, None)
            parsed.append(
                AnchorAlarmOverride(match=match, scope=scope, **kwargs)
            )
        cfg.overrides = parsed

    return cfg


def _parse_llm_anchor_provider(
    section: Any,
    *,
    config_path: Path | None = None,
) -> LLMProviderConfig | None:
    """Parse ``[llm.anchor_provider]`` and resolve ``api_key`` from env.

    Returns ``None`` when the section is empty or missing required fields —
    LLM is optional; absence simply means the anchor resolver will only use
    config-pinned typed pools.

    Enforces ``https://`` on ``base_url`` and warns if config.toml contains
    ``api_key`` but its file permissions are not 0600.
    """
    if not isinstance(section, dict):
        return None

    env_api_key = os.environ.get(_LLM_API_KEY_ENV_VAR)
    toml_api_key_raw = section.get("api_key")
    toml_has_api_key = isinstance(toml_api_key_raw, str) and bool(toml_api_key_raw)

    if toml_has_api_key and config_path is not None and config_path.exists():
        _warn_if_loose_config_permissions(config_path)

    base_url = section.get("base_url")
    model = section.get("model")
    timeout_s = section.get("timeout_s", 30.0)

    api_key = env_api_key or (toml_api_key_raw if toml_has_api_key else None)

    if not base_url and not model and not api_key:
        # Section absent or fully empty — silent no-op.
        return None

    # Beyond this point we treat a section with ANY content as an explicit
    # intent to configure the provider, so missing fields become errors.
    if not isinstance(base_url, str) or not base_url:
        raise InputValidationError(
            "[llm.anchor_provider].base_url is required when the section is present"
        )
    if not base_url.startswith("https://"):
        raise InputValidationError(
            f"[llm.anchor_provider].base_url must use https:// "
            f"(got {base_url!r}). Insecure endpoints are rejected to prevent "
            f"prompt-injection and credential exfiltration via a hostile host."
        )
    if not isinstance(model, str) or not model:
        raise InputValidationError(
            "[llm.anchor_provider].model is required when the section is present"
        )
    if not api_key:
        raise InputValidationError(
            f"LLM provider is configured but no api_key is available — set "
            f"the {_LLM_API_KEY_ENV_VAR} env var or [llm.anchor_provider].api_key"
        )
    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise InputValidationError(
            f"[llm.anchor_provider].timeout_s must be a positive number, got {timeout_s!r}"
        )

    return LLMProviderConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=float(timeout_s),
    )


def _warn_if_loose_config_permissions(config_path: Path) -> None:
    """Emit a warning if config.toml contains api_key but isn't 0600.

    No-op on Windows where POSIX permission bits aren't meaningful.
    """
    if os.name == "nt":
        return
    try:
        mode = stat.S_IMODE(config_path.stat().st_mode)
    except OSError:
        return
    if mode != 0o600:
        _log.warning(
            "config file %s contains an LLM api_key but has mode %s; "
            "set permissions to 0600 (chmod 600) to prevent credential leakage",
            config_path, oct(mode),
        )


def _normalize_domain_key(domain: str) -> str:
    """Strip scheme and trailing slashes for config key comparison."""
    return domain.rstrip("/").removeprefix("https://").removeprefix("http://")


def get_anchor_pool_v2(
    config: Config,
    main_domain: str,
    url_category: str,
    anchor_type: str,
) -> list[str]:
    """Return the configured typed-pool anchor candidates for one slot.

    Returns ``[]`` when any layer of the (main_domain, url_category,
    anchor_type) lookup is missing — callers should interpret an empty pool
    as the cue to fall back to LLM-generated candidates.

    Like ``get_anchor_keywords``, tolerates trailing-slash variants in the
    main_domain key.
    """
    for candidate in (
        main_domain.rstrip("/"),
        main_domain.rstrip("/") + "/",
    ):
        if candidate in config.target_anchor_pools_v2:
            return (
                config.target_anchor_pools_v2[candidate]
                .get(url_category, {})
                .get(anchor_type, [])
            )
    return []


def get_anchor_keywords(config: Config, main_domain: str) -> list[str]:
    """Return the configured anchor keyword pool for ``main_domain``.

    Tolerates scheme mismatches between config keys and seed rows — both
    ``https://example.com`` and ``http://example.com`` will match a config
    entry for either form, as well as a bare ``example.com`` key.

    Returns an empty list when no pool is configured — callers are expected to
    detect that condition and fall back to bare-domain anchor text.
    """
    bare = _normalize_domain_key(main_domain)
    for candidate in (
        main_domain.rstrip("/"),          # exact match first (most common)
        "https://" + bare,
        "http://" + bare,
        bare,                              # bare domain (no scheme)
    ):
        if candidate in config.target_anchor_keywords:
            return config.target_anchor_keywords[candidate]
    return []


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
    target_anchor_keywords: dict[str, list[str]] | None = None,
) -> None:
    """Write (or update) config.toml with the supplied values.

    Merges new values with any existing config so that calling this
    function never silently drops keys that were already there.

    ``target_anchor_keywords`` follows the same three-state semantics as
    ``extra_blogger_ids``:
    - ``None`` (default) — preserve whatever is already on disk
    - ``{}`` — explicitly clear the ``[targets]`` section
    - non-empty dict — write exactly the provided pools (overrides disk)
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

    # [targets] — three-state merge matching extra_blogger_ids semantics:
    #   None → preserve existing disk contents
    #   {}   → explicitly clear (write no [targets] section)
    #   {...} → write exactly the provided pools
    if target_anchor_keywords is None:
        targets = dict(existing.target_anchor_keywords)
    else:
        targets = dict(target_anchor_keywords)
    if targets:
        for domain, keywords in targets.items():
            quoted_kws = ", ".join(f'"{k}"' for k in keywords)
            lines.append(f'[targets."{domain}"]')
            lines.append(f"anchor_keywords = [{quoted_kws}]")
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


def load_medium_token(path: Path | None = None) -> dict[str, Any] | None:
    """Load Medium OAuth token dict from JSON file. Returns None if file missing."""
    token_path = path or (_config_dir() / "medium-token.json")
    if not token_path.exists():
        return None
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_medium_token(data: dict[str, Any], path: Path | None = None) -> None:
    """Save Medium OAuth token dict to JSON file with mode 0600."""
    token_path = path or (_config_dir() / "medium-token.json")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    try:
        os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
