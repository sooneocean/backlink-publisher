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
# flake8: noqa: F401
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
    ImageGenConfig,
    LLMProviderConfig,
    MediumOAuthConfig,
    ThreeUrlConfig,
)
from .loader import (
    _cache_dir,
    _config_dir,
    _warn_if_loose_config_permissions,  # noqa: F401
    get_three_url_config,
    load_config,
    resolve_blog_id,
)
from ._config_io import (
    _atomic_write_text,
    _CONFIG_HISTORY_MAX,
    _snapshot_config,  # noqa: F401
)
from ._toml_utils import (
    _preserve_unknown_sections,
    _SAVE_CONFIG_KNOWN_ROOTS,
    _TOML_HEADING_RE,
    _toml_heading_root,
    _toml_list,
    _toml_str,  # noqa: F401
)
from ._merge_categories import merge_site_url_categories
from .writer import save_config
from .tokens import (
    load_blogger_token,
    load_devto_token,
    load_ghpages_token,
    load_gitlabpages_token,
    load_hackmd_token,
    load_hashnode_token,
    load_mataroa_token,
    load_linkedin_token,
    load_medium_token,
    load_notion_token,
    load_wordpresscom_token,
    load_writeas_token,
    save_blogger_token,
    save_devto_token,
    save_ghpages_token,
    save_gitlabpages_token,
    save_hackmd_token,
    save_hashnode_token,
    save_linkedin_token,
    save_mataroa_token,
    save_medium_token,
    save_notion_token,
    save_wordpresscom_token,
    save_writeas_token,
    snapshot_token_revs,
)
from .parsers.anchor import (
    _parse_anchor_proportions,  # noqa: F401
    get_anchor_keywords,
    get_anchor_pool_v2,
)
from .parsers.alarm import _coerce_threshold, _parse_anchor_alarm  # noqa: F401
from .parsers.image_gen import _parse_image_gen  # noqa: F401
from .parsers.llm import _parse_llm_anchor_provider  # noqa: F401
from .parsers.target import (
    _clean_pool,  # noqa: F401
    _parse_target_anchor_keywords,  # noqa: F401
    _parse_target_anchor_pools_v2,  # noqa: F401
)
from .parsers.three_url import (
    _domain_label,  # noqa: F401
    _normalize_domain_key,  # noqa: F401
    _parse_site_url_categories,  # noqa: F401
    _parse_target_three_url,  # noqa: F401
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
    "ImageGenConfig",
    "load_blogger_token",
    "load_config",
    "load_devto_token",
    "load_ghpages_token",
    "load_gitlabpages_token",
    "load_hackmd_token",
    "load_mataroa_token",
    "load_medium_token",
    "load_notion_token",
    "merge_site_url_categories",
    "resolve_blog_id",
    "save_blogger_token",
    "save_config",
    "save_devto_token",
    "save_ghpages_token",
    "save_gitlabpages_token",
    "save_hackmd_token",
    "save_mataroa_token",
    "save_medium_token",
    "save_notion_token",
    "snapshot_token_revs",
    "upgrade_target_to_threeurl",
]
