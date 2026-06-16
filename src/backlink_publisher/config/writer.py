"""Atomic config writer + section preservation."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from backlink_publisher._util.logger import plan_logger
from .types import (
    Config,
    GhpagesConfig,
    GitlabPagesConfig,
    ImageGenConfig,
    MastodonConfig,
    ThreeUrlConfig,
)

from .loader import load_config
from ._config_io import _resolve_config_dir, _snapshot_config, _atomic_write_text
from .tokens import save_medium_integration_token
from ._toml_utils import (
    _SAVE_CONFIG_KNOWN_ROOTS,
    _emit_target_section,
    _preserve_unknown_sections,
    _toml_str,
    _toml_list,
)

_log = logging.getLogger(__name__)


def _emit_ghpages_section(lines: list[str], cfg: "GhpagesConfig | None") -> None:
    if cfg is None:
        return
    lines.append("[ghpages]")
    lines.append(f"repo          = {_toml_str(cfg.repo)}")
    lines.append(f"branch        = {_toml_str(cfg.branch)}")
    lines.append(f"path_template = {_toml_str(cfg.path_template)}")
    lines.append("")


def _emit_gitlabpages_section(
    lines: list[str], cfg: "GitlabPagesConfig | None"
) -> None:
    if cfg is None:
        return
    lines.append("[gitlabpages]")
    lines.append(f"project        = {_toml_str(cfg.project)}")
    lines.append(f"branch         = {_toml_str(cfg.branch)}")
    lines.append(f"path_template  = {_toml_str(cfg.path_template)}")
    lines.append(f"pages_base_url = {_toml_str(cfg.pages_base_url)}")
    lines.append("")


def _emit_mastodon_section(lines: list[str], cfg: "MastodonConfig | None") -> None:
    if cfg is None:
        return
    lines.append("[mastodon]")
    lines.append(f"instance_url = {_toml_str(cfg.instance_url)}")
    lines.append("")


def _emit_image_gen_section(lines: list[str], cfg: "ImageGenConfig | None") -> None:
    if cfg is None:
        return
    lines.append("[image_gen]")
    lines.append(f"base_url = {_toml_str(cfg.base_url)}")
    lines.append(f"model = {_toml_str(cfg.model)}")
    lines.append(f"banner_size = {_toml_str(cfg.banner_size)}")
    lines.append(f"daily_cap = {cfg.daily_cap}")
    lines.append(f"per_run_cap = {cfg.per_run_cap}")
    lines.append(f"timeout_s = {cfg.timeout_s}")
    lines.append(f"max_retries = {cfg.max_retries}")
    lines.append(f"strict = {'true' if cfg.strict else 'false'}")
    lines.append(f"auto_disable_threshold = {cfg.auto_disable_threshold}")
    lines.append(f"use_image_gen = {'true' if cfg.use_image_gen else 'false'}")
    lines.append("")


def _sync_medium_integration_token(token: str) -> None:
    """Persist Medium integration token to 0600 JSON file (SEC-3).
    Writes only when the new token differs from the stored value.
    Empty tokens silently preserve any existing file.
    """
    stripped = token.strip()
    if not stripped:
        return
    from .tokens import load_medium_integration_token
    current = load_medium_integration_token()
    if current and current.get("integration_token", "").strip() == stripped:
        return
    save_medium_integration_token({"integration_token": stripped})


def save_config(
    config: "Config",
    path: Path | None = None,
    extra_blogger_ids: dict[str, str] | None = None,
    medium_token: str | None = None,
    blogger_client_id: str | None = None,
    blogger_client_secret: str | None = None,
    target_anchor_keywords: dict[str, list[str]] | None = None,
    target_three_url: dict[str, ThreeUrlConfig] | None = None,
    ghpages_config: GhpagesConfig | None = None,
    gitlabpages_config: GitlabPagesConfig | None = None,
    mastodon_config: MastodonConfig | None = None,
    target_probe_queries: dict[str, list[str]] | None = None,
    target_brand_aliases: dict[str, list[str]] | None = None,
    image_gen_config: ImageGenConfig | None = None,
) -> None:
    config_path = path or (_resolve_config_dir() / "config.toml")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config_path.parent, 0o700)
    except OSError:
        pass

    existing = load_config(config_path)

    blog_ids: dict[str, str] = dict(config.blogger_blog_ids)
    if extra_blogger_ids is None:
        for k, v in existing.blogger_blog_ids.items():
            if k not in blog_ids:
                blog_ids[k] = v
    elif extra_blogger_ids:
        blog_ids.update(extra_blogger_ids)

    client_id = blogger_client_id or (
        existing.blogger_oauth.client_id if existing.blogger_oauth else ""
    )
    client_secret = blogger_client_secret or (
        existing.blogger_oauth.client_secret if existing.blogger_oauth else ""
    )

    token = medium_token if medium_token is not None else (
        existing.medium_integration_token or ""
    )
    # SEC-3: persist integration token to 0600 file instead of TOML
    _sync_medium_integration_token(token)

    if target_anchor_keywords is None:
        kws_by_domain = dict(existing.target_anchor_keywords)
    else:
        kws_by_domain = dict(target_anchor_keywords)

    if target_three_url is None:
        three_url_by_domain = dict(existing.target_three_url)
    else:
        three_url_by_domain = dict(target_three_url)

    if target_probe_queries is None:
        probe_queries_by_domain = dict(existing.target_probe_queries)
    else:
        probe_queries_by_domain = dict(target_probe_queries)

    if target_brand_aliases is None:
        brand_aliases_by_domain = dict(existing.target_brand_aliases)
    else:
        brand_aliases_by_domain = dict(target_brand_aliases)

    ghpages_cfg = ghpages_config if ghpages_config is not None else existing.ghpages
    gitlabpages_cfg = (
        gitlabpages_config if gitlabpages_config is not None
        else existing.gitlabpages
    )
    mastodon_cfg = mastodon_config if mastodon_config is not None else existing.mastodon
    image_gen_cfg = (
        image_gen_config if image_gen_config is not None else existing.image_gen
    )

    lines: list[str] = []

    lines.append("[blogger]")
    for domain, blog_id in blog_ids.items():
        lines.append(f"{_toml_str(domain)} = {_toml_str(blog_id)}")
    lines.append("")

    if client_id or client_secret:
        lines.append("[blogger.oauth]")
        lines.append(f"client_id     = {_toml_str(client_id)}")
        lines.append(f"client_secret = {_toml_str(client_secret)}")
        lines.append("")

    lines.append("[medium]")
    # SEC-3: integration token is now written to medium-integration-token.json (0600).
    # The TOML field is kept as a commented placeholder for backward compat discovery.
    lines.append('# integration_token = "your-medium-integration-token"')
    lines.append("# Token persisted via save_medium_integration_token() to 0600 JSON file")
    lines.append("")

    all_target_domains = sorted(
        set(kws_by_domain)
        | set(three_url_by_domain)
        | set(probe_queries_by_domain)
        | set(brand_aliases_by_domain)
    )
    for domain in all_target_domains:
        lines.extend(
            _emit_target_section(
                domain,
                kws_by_domain,
                probe_queries_by_domain,
                brand_aliases_by_domain,
                three_url_by_domain,
            )
        )
        lines.append("")

    _emit_ghpages_section(lines, ghpages_cfg)
    _emit_gitlabpages_section(lines, gitlabpages_cfg)
    _emit_mastodon_section(lines, mastodon_cfg)
    _emit_image_gen_section(lines, image_gen_cfg)

    known_subsections: set[tuple[str, str]] = set()
    if client_id or client_secret:
        known_subsections.add(("blogger", "oauth"))
    for domain in all_target_domains:
        known_subsections.add(("targets", _toml_str(domain)))
    on_disk_target_domains = (
        set(existing.target_anchor_keywords)
        | set(existing.target_three_url)
        | set(existing.target_probe_queries)
        | set(existing.target_brand_aliases)
    )
    for domain in on_disk_target_domains - set(all_target_domains):
        known_subsections.add(("targets", _toml_str(domain)))

    preserved = ""
    if config_path.exists():
        try:
            existing_raw = config_path.read_text(encoding="utf-8")
            preserved = _preserve_unknown_sections(
                existing_raw,
                _SAVE_CONFIG_KNOWN_ROOTS,
                frozenset(known_subsections),
            )
        except OSError as exc:
            plan_logger.warn(
                "config_preserve_read_failed",
                path=str(config_path),
                reason=type(exc).__name__,
            )

    payload = "\n".join(lines)
    if preserved:
        if not payload.endswith("\n"):
            payload += "\n"
        payload += "\n" + preserved

    _snapshot_config(config_path)
    _atomic_write_text(config_path, payload)
