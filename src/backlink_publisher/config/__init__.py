"""User config loader for backlink-publisher.

This package was split out of a single ``config.py`` module in Plan
2026-05-18-001 Unit 5. All public names remain importable from
``backlink_publisher.config`` for backwards compatibility (R6).

Layout:
  - ``types``    — dataclasses + shared constants
  - ``loader``   — ``load_config`` + parser dispatcher
  - ``writer``   — ``save_config`` + atomic write + section preservation
  - ``tokens``   — Blogger / Medium token file I/O
  - ``parsers``  — per-section TOML parsers (one file per section)
"""
from __future__ import annotations

# Surfaced at package level so legacy tests that monkeypatch
# `backlink_publisher.config.os.replace` (and similar) keep working
# after the Plan Unit 5 split. The real call site lives in
# ``backlink_publisher.config.writer``; this re-export preserves the
# patchable attribute path.
import os  # noqa: F401

from .types import (
    ANCHOR_TYPES,
    AnchorAlarmConfig,
    AnchorAlarmOverride,
    BloggerOAuthConfig,
    Config,
    DEFAULT_WORK_TEMPLATES,
    GhpagesConfig,
    HashnodeConfig,
    ImageGenConfig,
    LLMProviderConfig,
    MediumOAuthConfig,
    ThreeUrlConfig,
    WriteAsConfig,
)
from .loader import (
    _cache_dir,
    _config_dir,
    _warn_if_loose_config_permissions,
    get_three_url_config,
    load_config,
    resolve_blog_id,
)
from .writer import (
    _atomic_write_text,
    _CONFIG_HISTORY_MAX,
    _preserve_unknown_sections,
    _SAVE_CONFIG_KNOWN_ROOTS,
    _snapshot_config,
    _TOML_HEADING_RE,
    _toml_heading_root,
    _toml_list,
    _toml_str,
    merge_site_url_categories,
    save_config,
)
from .tokens import (
    load_blogger_token,
    load_ghpages_token,
    load_hashnode_token,
    load_medium_token,
    load_writeas_token,
    save_blogger_token,
    save_ghpages_token,
    save_hashnode_token,
    save_medium_token,
    save_writeas_token,
)
from .parsers.anchor import (
    _parse_anchor_proportions,
    get_anchor_keywords,
    get_anchor_pool_v2,
)
from .parsers.alarm import _coerce_threshold, _parse_anchor_alarm
from .parsers.image_gen import _parse_image_gen
from .parsers.llm import _parse_llm_anchor_provider
from .parsers.target import (
    _clean_pool,
    _parse_target_anchor_keywords,
    _parse_target_anchor_pools_v2,
)
from .parsers.three_url import (
    _domain_label,
    _normalize_domain_key,
    _parse_site_url_categories,
    _parse_target_three_url,
    upgrade_target_to_threeurl,
)

__all__ = [
    "ANCHOR_TYPES",
    "AnchorAlarmConfig",
    "AnchorAlarmOverride",
    "BloggerOAuthConfig",
    "Config",
    "DEFAULT_WORK_TEMPLATES",
    "LLMProviderConfig",
    "MediumOAuthConfig",
    "ThreeUrlConfig",
    "get_anchor_keywords",
    "get_anchor_pool_v2",
    "get_three_url_config",
    "GhpagesConfig",
    "HashnodeConfig",
    "ImageGenConfig",
    "load_blogger_token",
    "load_config",
    "load_ghpages_token",
    "load_hashnode_token",
    "load_medium_token",
    "load_writeas_token",
    "merge_site_url_categories",
    "resolve_blog_id",
    "save_blogger_token",
    "save_config",
    "save_ghpages_token",
    "save_hashnode_token",
    "save_medium_token",
    "save_writeas_token",
    "WriteAsConfig",
    "upgrade_target_to_threeurl",
]
