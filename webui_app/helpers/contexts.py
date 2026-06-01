"""Template context builders and render helper — Plan 2026-05-21-007 Unit 5."""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from flask import render_template

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher.config import (
    _config_dir,
    load_blogger_token,
    load_config,
    merge_site_url_categories,
    save_config,
    upgrade_target_to_threeurl,
)
from backlink_publisher._util.logger import plan_logger

from webui_store import (
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    queue_store as _queue_store,
    schedule_store as _schedule_store,
)

from .security import _FLASK_PORT, _ensure_csrf_token, _oauth_callback_uri
from ._request_cache import _g_cache


from .channel_probes import (
    _get_blogger_token_status,
    _get_medium_browser_status,
    _get_velog_status,
    _image_gen_status,
)

def _llm_settings_file() -> Path:
    # Lazy so BACKLINK_PUBLISHER_CONFIG_DIR rebinds are honored per-call.
    return _config_dir() / 'llm-settings.json'




def _load_llm_settings() -> dict:
    defaults = {
        'api_key': '',
        'endpoint': '',
        'model': '',
        'temperature': 0.7,
        'system_prompt': '',
        'use_article_gen': False,
        'article_system_prompt': '',
        'image_gen_api_key': '',
        'image_gen_endpoint': '',
        'image_gen_model': '',
        'image_gen_banner_size': '1200x630',
        'use_image_gen': False
    }
    path = _llm_settings_file()
    if path.exists():
        # O8: pre-#140 code hand-rolled this write and shipped without a
        # chmod, leaving llm-settings.json (which stores the LLM api_key)
        # world-readable at 0o644. The write path now routes through
        # ``atomic_write`` (0o600), but files created by the old code never
        # get re-tightened until the operator happens to re-save. Auto-fix
        # loose perms on load — mirrors the frw-token.json reader in
        # ``_util/secrets.py`` (warn-don't-fail: a cp-induced 0o644 is far
        # more common than tampering).
        try:
            mode = os.stat(path).st_mode & 0o777
            if mode != 0o600:
                plan_logger.warn(
                    "llm-settings.json loose perms — auto-chmod to 0o600",
                    mode=oct(mode),
                )
                os.chmod(path, 0o600)
        except OSError:
            # Best-effort: never let a perms fix break the settings render.
            plan_logger.warn("failed to chmod llm-settings.json to 0o600")
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            defaults.update(data)
        except Exception:
            plan_logger.warn("failed to parse llm-settings.json, using defaults")
    return defaults






def _persist_three_tier_config(
    main_url: str, category_url: str, work_url: str,
) -> None:
    """Persist the homepage form's three-tier URL data via ThreeUrlConfig."""
    cfg = load_config()
    upgraded = upgrade_target_to_threeurl(
        cfg,
        main_url=main_url,
        category_url=category_url or None,
        work_url=work_url or None,
    )
    domain_key = main_url.rstrip("/")
    merged = dict(cfg.target_three_url)
    merged[domain_key] = upgraded
    save_config(cfg, target_anchor_keywords=None, target_three_url=merged)

    site_additions: dict[str, str] = {"home": main_url}
    if category_url:
        site_additions["category"] = category_url
    merge_site_url_categories(main_url, site_additions)

    plan_logger.recon(
        "homepage_form_persisted",
        main=main_url,
        list_url=upgraded.list_url,
        work_count=len(upgraded.work_urls),
    )


def _load_schedule_settings() -> dict:
    defaults = {'min_interval_hours': 4, 'jitter_minutes': 30}
    loaded = _g_cache('schedule', _schedule_store.load)
    if isinstance(loaded, dict):
        defaults.update(loaded)
    return defaults


def _save_schedule_settings(data: dict) -> None:
    _schedule_store.save(data)


def _calc_next_available(requested_dt: datetime) -> datetime:
    """Return the earliest publish time that respects min-interval + jitter."""
    settings = _load_schedule_settings()
    min_hours = settings.get('min_interval_hours', 4)
    jitter_mins = settings.get('jitter_minutes', 30)

    last_published = None
    for item in _g_cache('drafts', _drafts_store.load):
        if item.get('status') in ('published', 'scheduled'):
            ts = item.get('published_at') or item.get('scheduled_at')
            if ts:
                try:
                    dt = datetime.fromisoformat(ts) if 'T' in ts else \
                         datetime.strptime(ts, '%Y-%m-%d %H:%M')
                    if last_published is None or dt > last_published:
                        last_published = dt
                except ValueError:
                    plan_logger.warn("_calc_next_available: bad date in drafts_store", ts=ts)

    for item in _g_cache('history', _history_store.load):
        ts = item.get('created_at')
        if ts and item.get('status') in ('drafted', 'published'):
            try:
                dt = datetime.strptime(ts, '%Y-%m-%d %H:%M')
                if last_published is None or dt > last_published:
                    last_published = dt
            except ValueError:
                plan_logger.warn("_calc_next_available: bad date in history_store", ts=ts)

    if last_published is None:
        return requested_dt
    earliest = last_published + timedelta(hours=min_hours)
    if jitter_mins > 0:
        earliest += timedelta(minutes=random.randint(0, jitter_mins))
    return max(requested_dt, earliest)


def _load_incomplete_run():
    """Return the most recent incomplete checkpoint run (with pending_count), or None."""
    try:
        runs = _checkpoint_mod.list_incomplete()
    except Exception:
        return None
    if not runs:
        return None
    run = runs[0]
    pending_count = sum(
        1 for i in run.get("items", []) if i.get("status") in ("pending", "failed")
    )
    return {**run, "pending_count": pending_count}




def _token_paste_status(cfg, channel: str, load_fn, *, token_field: str = "token") -> dict:
    """Status dict consumed by _settings_channel_token_paste.html.

    Reads the platform's token file via the load function injected by
    the caller. Returns {bound, masked, dofollow}. Defensive against any
    load failure — broken token files surface as "unbound" rather than
    crashing the settings page render.

    ``token_field`` is the JSON key to read from the token file (default
    "token"). Dev.to uses "api_key" instead.
    """
    from backlink_publisher.publishing.registry import dofollow_status
    try:
        token_path_attr = f"{channel}_token_path"
        token_path = getattr(cfg, token_path_attr, None)
        data = load_fn(token_path) if token_path else load_fn()
    except Exception:
        data = None
    token = (data or {}).get(token_field, "") if isinstance(data, dict) else ""
    bound = bool(token)
    if bound and len(token) > 6:
        masked = token[:3] + "*" * (len(token) - 6) + token[-3:]
    elif bound:
        masked = "*" * len(token)
    else:
        masked = ""
    return {
        "bound": bound,
        "masked": masked,
        "dofollow": dofollow_status(channel),
    }


def _token_paste_status_notion(cfg, load_fn) -> dict:
    """Status dict for the Notion token-paste card.

    Notion's token file has two fields (integration_token + database_id)
    rather than the single 'token' field used by ghpages/devto.
    """
    from backlink_publisher.publishing.registry import dofollow_status
    try:
        token_path = getattr(cfg, "notion_token_path", None)
        data = load_fn(token_path) if token_path else load_fn()
    except Exception:
        data = None
    integration_token = (data or {}).get("integration_token", "") if isinstance(data, dict) else ""
    database_id = (data or {}).get("database_id", "") if isinstance(data, dict) else ""
    bound = bool(integration_token and database_id)
    if bound and len(integration_token) > 6:
        masked = integration_token[:3] + "*" * (len(integration_token) - 6) + integration_token[-3:]
    elif bound:
        masked = "*" * len(integration_token)
    else:
        masked = ""
    return {
        "bound": bound,
        "masked": masked,
        "dofollow": dofollow_status("notion"),
        "database_id_set": bool(database_id),
    }


def _settings_context(flash=None):
    """Build template context for the settings page."""
    from flask import session as _flask_session

    from backlink_publisher.config import (
        load_devto_token,
        load_ghpages_token,
        load_medium_token,
        load_notion_token,
    )
    from backlink_publisher.cli._bind.channels import CHANNELS
    from webui_store.channel_status import list_all as _channel_list_all
    from ..services.bind_job import BIND_ERROR_MESSAGES

    cfg = _g_cache('config', load_config)
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    ghpages_status = _token_paste_status(cfg, "ghpages", load_ghpages_token)
    ghpages_config_summary = [
        ("repo", cfg.ghpages.repo if cfg.ghpages else ""),
        ("branch", cfg.ghpages.branch if cfg.ghpages else "gh-pages"),
        ("path_template", cfg.ghpages.path_template if cfg.ghpages else "_posts/{date}-{slug}.md"),
    ]

    notion_status = _token_paste_status_notion(cfg, load_notion_token)
    devto_status = _token_paste_status(cfg, "devto", load_devto_token, token_field="api_key")
    notion_config_summary: list[tuple[str, str]] = []
    devto_config_summary: list[tuple[str, str]] = []

    from backlink_publisher.config.tokens import load_medium_integration_token as _load_it
    _it_data = _load_it()
    _it_val = (_it_data or {}).get("integration_token", "").strip()
    token = _it_val or cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    try:
        from ..medium_liveness import medium_liveness_check
        medium_liveness_check()
    except Exception:  # noqa: BLE001 — Settings render must not depend on probe
        pass

    try:
        channel_statuses = _channel_list_all()
    except Exception:
        channel_statuses = {}

    try:
        csrf_token = _ensure_csrf_token()
    except Exception:
        csrf_token = ""

    velog_status = _get_velog_status()

    try:
        # Plan 2026-05-25-002 Unit 4a — use ``active_platforms()`` from
        # the registry; this composes ``registered_platforms()`` with the
        # manifest ``visibility`` filter, so the dashboard card list now
        # automatically excludes any future ``visibility='hidden'`` or
        # ``visibility='retired'`` channel without needing to touch this
        # helper. ``HIDDEN_FROM_UI`` (Unit 2a PEP 562 alias) is still the
        # legacy fallback path but redundant once we read from
        # ``active_platforms()`` directly.
        from backlink_publisher.publishing.registry import active_platforms
        from ..binding_status import get_channel_status
        dashboard_channels = [
            (name, get_channel_status(name, cfg))
            for name in active_platforms()
        ]
    except Exception:
        dashboard_channels = []

    # Plan 2026-05-29-003 Unit 2 — pre-group the overview channels into
    # automation tiers for the settings overview panel. Rendering must never
    # fail because grouping failed: fall back to [] (template renders no groups).
    try:
        from .channel_tiers import group_channels_by_tier
        dashboard_channel_tiers = group_channels_by_tier(dashboard_channels)
    except Exception:
        dashboard_channel_tiers = []

    return dict(
        flash=flash,
        csrf_token=csrf_token,
        dashboard_channels=dashboard_channels,
        dashboard_channel_tiers=dashboard_channel_tiers,
        medium_browser_status=_get_medium_browser_status(cfg, session=_flask_session),
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        blogger_client_secret_set=bool(cfg.blogger_oauth and cfg.blogger_oauth.client_secret),
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        medium_token_file_exists=bool(medium_token_data),
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_g_cache('profiles', _profiles_store.load),
        plans_list=[],
        schedule_settings=_load_schedule_settings(),
        llm_settings=_load_llm_settings(),
        image_gen_status=_image_gen_status(cfg),
        all_targets=all_targets,
        target_anchor_keywords=cfg.target_anchor_keywords,
        binding_channels=sorted(CHANNELS),
        channel_statuses=channel_statuses,
        bind_error_messages=BIND_ERROR_MESSAGES,
        velog_status=velog_status,
        velog_cookies_path=velog_status.get('cookies_path', ''),
        ghpages_status=ghpages_status,
        ghpages_config_summary=ghpages_config_summary,
        notion_status=notion_status,
        notion_config_summary=notion_config_summary,
        devto_status=devto_status,
        devto_config_summary=devto_config_summary,
    )


def _draft_tab_extra() -> dict:
    """Extra template context for the draft tab."""
    return {
        'schedule_settings': _load_schedule_settings(),
    }


def _group_history(items: list[dict]) -> list[dict]:
    """Group consecutive history items by ``run_id`` into run-groups.

    Items sharing a ``run_id`` (from one ``_push_history_per_row`` call) fold
    into one group so the UI renders them as a collapsible card instead of N
    flat rows. Items without ``run_id`` each form a group of size 1.

    Each group dict: run_id, rows, created_at, platform, language,
    n_published, n_drafted, n_failed, n_unverified, n_total, is_multi.
    """
    groups: list[dict] = []
    current: dict | None = None
    for item in items:
        rid = item.get("run_id")
        if rid and current and current["run_id"] == rid:
            current["rows"].append(item)
        else:
            current = {
                "run_id": rid,
                "rows": [item],
                "created_at": item.get("created_at", ""),
                "platform": item.get("platform", ""),
                "language": item.get("language", ""),
            }
            groups.append(current)
    for g in groups:
        statuses = [i.get("status", "") for i in g["rows"]]
        g["n_published"] = sum(1 for s in statuses if s in ("published", "success"))
        g["n_drafted"] = sum(1 for s in statuses if s == "drafted")
        g["n_failed"] = sum(1 for s in statuses if s == "failed")
        g["n_unverified"] = sum(1 for s in statuses if "unverified" in s)
        g["n_total"] = len(g["rows"])
        g["is_multi"] = g["n_total"] > 1
    return groups


def _render(template_name: str, **kwargs):
    """Render a Jinja2 template, auto-injecting common context.

    Auto-injected context (when not provided by caller):
      - history, blogger_token_status, profiles, draft_queue, tasks,
        now_iso, suggested_next, incomplete_run
    """
    if 'history' not in kwargs:
        kwargs['history'] = _g_cache('history', _history_store.load)
    if 'grouped_history' not in kwargs:
        kwargs['grouped_history'] = _group_history(kwargs['history'])
    if 'blogger_token_status' not in kwargs:
        kwargs['blogger_token_status'] = _get_blogger_token_status()
    if 'profiles' not in kwargs:
        kwargs['profiles'] = _g_cache('profiles', _profiles_store.load)
    if 'draft_queue' not in kwargs:
        kwargs['draft_queue'] = _g_cache('drafts', _drafts_store.load)
    if 'tasks' not in kwargs:
        try:
            kwargs['tasks'] = _g_cache('tasks', _queue_store.load)
        except Exception:
            kwargs['tasks'] = []
    if 'now_iso' not in kwargs:
        now = datetime.now()
        kwargs['now_iso'] = now.strftime('%Y-%m-%dT%H:%M')
        kwargs.setdefault(
            'suggested_next',
            _calc_next_available(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
        )
    if 'incomplete_run' not in kwargs:
        kwargs['incomplete_run'] = _load_incomplete_run()
    return render_template(template_name, **kwargs)
