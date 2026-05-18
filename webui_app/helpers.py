"""Shared helpers extracted from legacy webui.py — Plan 2026-05-18-001 Unit 3.

Pure-Python utility functions (URL parsing, content-gate, OAuth callback
URI, CSRF, derivation pools, settings context, render shim). No Flask app
instance dependency — modules that need Flask context use ``flask.request``
/ ``flask.session`` via Flask's request context, not via app.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import render_template, request, session

# Ensure backlink_publisher package is importable when invoked from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher import content_fetch
from backlink_publisher.config import (
    load_blogger_token,
    load_config,
    merge_site_url_categories,
    save_config,
    upgrade_target_to_threeurl,
)
from backlink_publisher.logger import plan_logger

from webui_store import (
    drafts_store as _drafts_store,
    history_store as _history_store,
    profiles_store as _profiles_store,
    schedule_store as _schedule_store,
)


_FLASK_PORT = int(os.environ.get('PORT', 8888))
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


# ─────────────────────────────────────────────────────────────────────────────
# Content-fetch gate (plan 2026-05-14-007)
# ─────────────────────────────────────────────────────────────────────────────


def _content_gate_enabled() -> bool:
    val = os.environ.get("BACKLINK_NO_FETCH_VERIFY", "").strip().lower()
    return val not in {"1", "true", "yes"}


def _verify_urls_or_error(
    urls: list[str], field_label: str
) -> tuple[list[str], str | None]:
    if not urls:
        return [], None
    if not _content_gate_enabled():
        return list(urls), None
    results = content_fetch.verify_urls_batch(urls)
    survivors: list[str] = []
    failures: list[str] = []
    for u in urls:
        ok, reason, _title = results.get(u, (False, "missing_result", None))
        if ok:
            survivors.append(u)
        else:
            failures.append(f"{u} ({reason})")
    if failures:
        joined = ", ".join(failures)
        return survivors, f"{field_label} 无可访问内容: {joined}"
    return survivors, None


# ─────────────────────────────────────────────────────────────────────────────
# Token status (Blogger)
# ─────────────────────────────────────────────────────────────────────────────


def _get_blogger_token_status() -> dict:
    """Return token health status without making network calls."""
    try:
        from backlink_publisher.config import load_config as _load_cfg
        from backlink_publisher.config import load_blogger_token as _load_tok
        cfg = _load_cfg()
        token_data = _load_tok(cfg.blogger_token_path)
        if not token_data:
            return {'state': 'none', 'label': '未授权', 'days_left': None}
        if not cfg.blogger_oauth:
            return {'state': 'none', 'label': '未配置 OAuth', 'days_left': None}
        from google.oauth2.credentials import Credentials
        try:
            creds = Credentials.from_authorized_user_info(
                token_data, ['https://www.googleapis.com/auth/blogger']
            )
        except Exception:
            return {'state': 'expired', 'label': 'Token 无效', 'days_left': 0}
        if creds.expiry is None:
            return {'state': 'ok', 'label': 'Token 有效', 'days_left': None}
        now = datetime.now(timezone.utc)
        expiry = creds.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        days = (expiry - now).days
        if days < 0:
            if creds.refresh_token:
                return {'state': 'expiring', 'label': 'Token 已过期（将自动刷新）',
                        'days_left': days}
            return {'state': 'expired', 'label': 'Token 已过期，需重新授权',
                    'days_left': days}
        if days <= 3:
            return {'state': 'expiring', 'label': f'Token {days} 天后到期',
                    'days_left': days}
        return {'state': 'ok', 'label': f'Token 有效（{days} 天）', 'days_left': days}
    except Exception:
        return {'state': 'ok', 'label': 'Blogger 已连接', 'days_left': None}


# ─────────────────────────────────────────────────────────────────────────────
# URL metadata fetchers
# ─────────────────────────────────────────────────────────────────────────────


def fetch_url_metadata(url):
    try:
        headers = {'User-Agent':
                   'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        title = ''
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')
        if not title:
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else ''
        desc = ''
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            desc = og_desc.get('content', '')
        if not desc:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                desc = meta_desc.get('content', '')
        return {'url': url, 'title': title.strip() if title else '',
                'description': desc.strip() if desc else '', 'status': 'success'}
    except Exception as e:
        return {'url': url, 'title': '', 'description': '',
                'status': 'error', 'error': str(e)}


def fetch_full_tdk(url):
    try:
        headers = {'User-Agent':
                   'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        title = ''
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')
        if not title:
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else ''
        description = ''
        og_desc = soup.find('meta', property='og:description')
        if og_desc:
            description = og_desc.get('content', '')
        if not description:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                description = meta_desc.get('content', '')
        keywords = ''
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = meta_keywords.get('content', '')
        title = title.strip() if title else ''
        description = description.strip() if description else ''
        keywords = keywords.strip() if keywords else ''
        system_prompt = f"""你是一个专业的网站内容作家。请根据以下目标网站的SEO信息，创作一篇高质量的反向链接文章。

目标网站信息:
- 标题: {title}
- 描述: {description}
- 关键词: {keywords}

文章要求:
1. 内容要与目标网站主题相关
2. 自然地嵌入目标网站链接
3. 保持专业、流畅的写作风格
4. 字数控制在100-200字之间

请生成一篇有价值的文章内容。"""
        return {'title': title, 'description': description, 'keywords': keywords,
                'system_prompt': system_prompt, 'status': 'success'}
    except Exception as e:
        return {'title': '', 'description': '', 'keywords': '',
                'system_prompt': '', 'status': 'error', 'error': str(e)}


def detect_platform(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if 'medium.com' in domain:
        return 'medium'
    if 'blogspot.com' in domain or 'blogger.com' in domain:
        return 'blogger'
    if 'wordpress.com' in domain:
        return 'wordpress'
    return 'medium'


def detect_language(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    if '.cn' in domain or 'cn' in path:
        return 'zh-CN'
    if '.tw' in domain or 'tw' in path or 'hk' in path:
        return 'zh-TW'
    if '.jp' in domain or 'jp' in path or 'ja' in path:
        return 'ja'
    if '.ru' in domain or 'ru' in path:
        return 'ru'
    if '.es' in domain or 'es' in path:
        return 'es'
    if '.de' in domain or 'de' in path:
        return 'de'
    if '.fr' in domain or 'fr' in path:
        return 'fr'
    return 'en'


def get_main_domain(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_publish_results(jsonl_str):
    results = []
    for line in (jsonl_str or '').strip().split('\n'):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def _normalize_url(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Three-URL persistence
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# Schedule
# ─────────────────────────────────────────────────────────────────────────────


def _load_schedule_settings() -> dict:
    defaults = {'min_interval_hours': 4, 'jitter_minutes': 30}
    loaded = _schedule_store.load()
    if isinstance(loaded, dict):
        defaults.update(loaded)
    return defaults


def _save_schedule_settings(data: dict) -> None:
    _schedule_store.save(data)


def _calc_next_available(requested_dt: datetime) -> datetime:
    """Return the earliest publish time that respects min-interval + jitter."""
    import random
    settings = _load_schedule_settings()
    min_hours = settings.get('min_interval_hours', 4)
    jitter_mins = settings.get('jitter_minutes', 30)

    last_published = None
    for item in _drafts_store.load():
        if item.get('status') in ('published', 'scheduled'):
            ts = item.get('published_at') or item.get('scheduled_at')
            if ts:
                try:
                    dt = datetime.fromisoformat(ts) if 'T' in ts else \
                         datetime.strptime(ts, '%Y-%m-%d %H:%M')
                    if last_published is None or dt > last_published:
                        last_published = dt
                except ValueError:
                    pass

    for item in _history_store.load():
        ts = item.get('created_at')
        if ts and item.get('status') in ('drafted', 'published'):
            try:
                dt = datetime.strptime(ts, '%Y-%m-%d %H:%M')
                if last_published is None or dt > last_published:
                    last_published = dt
            except ValueError:
                pass

    if last_published is None:
        return requested_dt
    earliest = last_published + timedelta(hours=min_hours)
    if jitter_mins > 0:
        earliest += timedelta(minutes=random.randint(0, jitter_mins))
    return max(requested_dt, earliest)


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint helpers
# ─────────────────────────────────────────────────────────────────────────────


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


def _check_localhost():
    if request.remote_addr not in ("127.0.0.1", "::1", "localhost"):
        from flask import abort
        abort(403)


def _validate_webui_run_id(run_id):
    if not run_id or not _RUN_ID_RE.match(run_id):
        from flask import abort
        abort(400)


# ─────────────────────────────────────────────────────────────────────────────
# OAuth / bind helpers
# ─────────────────────────────────────────────────────────────────────────────


def _oauth_callback_uri():
    return f'http://localhost:{_FLASK_PORT}/settings/blogger/oauth-callback'


def _resolve_bind_host() -> str:
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    if host in _LOOPBACK_HOSTS:
        return host
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        return host
    raise RuntimeError(
        f"refusing to bind to non-loopback host {host!r}: this WebUI has "
        "minimal auth. Set BACKLINK_PUBLISHER_ALLOW_NETWORK=1 to opt in to "
        "network exposure (only do this on a trusted network)."
    )


def _ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _check_csrf_or_abort() -> None:
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not token or not expected or not secrets.compare_digest(token, expected):
        from flask import abort
        abort(403)


def _parse_lines(raw: str) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _wire_content_fetch_ttl_from_env() -> None:
    bypass = os.environ.get("BACKLINK_NO_FETCH_VERIFY", "").strip().lower()
    if bypass in {"1", "true", "yes"}:
        return
    raw = os.environ.get("BACKLINK_GATE_CACHE_TTL_SECONDS", "900").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    if seconds <= 0:
        return
    content_fetch.set_default_max_age(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Derived pools (plan 006)
# ─────────────────────────────────────────────────────────────────────────────


_DERIVED_BRANDED_MAX: int = 30
_DERIVED_PARTIAL_MAX: int = 60
_DERIVED_PARTIAL_KEEP: int = 3
_DERIVED_PARTIAL_SPLIT_RE = re.compile(r"[。.；;，,、]+")


def _derive_branded_pool(main_url: str, tdk: dict | None) -> list[str]:
    from backlink_publisher.config import _domain_label
    if tdk and tdk.get("title"):
        title = str(tdk["title"]).strip()
        if title:
            return [title[:_DERIVED_BRANDED_MAX]]
    return [_domain_label(main_url)]


def _derive_partial_pool(main_url: str, tdk: dict | None) -> list[str]:
    from backlink_publisher.config import _domain_label
    if tdk and tdk.get("description"):
        desc = str(tdk["description"]).strip()
        if desc:
            phrases = [
                p.strip()[:_DERIVED_PARTIAL_MAX]
                for p in _DERIVED_PARTIAL_SPLIT_RE.split(desc)
                if p and p.strip()
            ]
            if phrases:
                return phrases[:_DERIVED_PARTIAL_KEEP]
    return [_domain_label(main_url)]


def _derive_exact_pool(main_url: str) -> list[str]:
    from backlink_publisher.config import _domain_label
    return [_domain_label(main_url)]


# ─────────────────────────────────────────────────────────────────────────────
# Settings context
# ─────────────────────────────────────────────────────────────────────────────


def _settings_context(flash=None):
    """Build template context for the settings page."""
    from backlink_publisher.config import load_medium_token

    cfg = load_config()
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    token = cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    return dict(
        flash=flash,
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        blogger_client_secret=cfg.blogger_oauth.client_secret if cfg.blogger_oauth else "",
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_profiles_store.load(),
        plans_list=[],
        schedule_settings=_load_schedule_settings(),
        all_targets=all_targets,
        target_anchor_keywords=cfg.target_anchor_keywords,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Draft tab extra context
# ─────────────────────────────────────────────────────────────────────────────


def _draft_tab_extra() -> dict:
    """Extra template context for the draft tab."""
    return {
        'schedule_settings': _load_schedule_settings(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# run_pipe — subprocess wrapper for plan/validate/publish
# ─────────────────────────────────────────────────────────────────────────────


_CLI_MODULES = {
    'publish-backlinks': 'backlink_publisher.cli.publish_backlinks',
    'plan-backlinks': 'backlink_publisher.cli.plan_backlinks',
    'validate-backlinks': 'backlink_publisher.cli.validate_backlinks',
    'footprint': 'backlink_publisher.cli.footprint',
    'report-anchors': 'backlink_publisher.cli.report_anchors',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, 'src')


def _rewrite_cli_cmd(cmd):
    """Rewrite bare CLI command (publish-backlinks, plan-backlinks, ...) to
    ``sys.executable -m <module>`` and inject ``PYTHONPATH=./src``.

    Why: the installed entry-point shims (pyenv shim, .venv/bin/*) can point
    at a stale editable-install path that no longer exists. Running via the
    current interpreter + repo src/ bypasses that and is hermetic.
    """
    if not cmd:
        return cmd, None
    module = _CLI_MODULES.get(cmd[0])
    if module is None:
        return cmd, None
    new_cmd = [sys.executable, '-m', module, *cmd[1:]]
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC_DIR + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else ''
    )
    return new_cmd, env


def run_pipe(cmd, stdin):
    """Run a pipeline command."""
    import subprocess
    new_cmd, env = _rewrite_cli_cmd(cmd)
    result = subprocess.run(
        new_cmd,
        input=stdin,
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT or os.getcwd(),
        env=env,
    )
    if result.returncode != 0:
        raise Exception(result.stderr or f"Exit code: {result.returncode}")
    return {'stdout': result.stdout, 'stderr': result.stderr}


# ─────────────────────────────────────────────────────────────────────────────
# Render shim — replaces legacy render_template_string(HTML, ...) calls
# ─────────────────────────────────────────────────────────────────────────────


def _render(template_name: str, **kwargs):
    """Render a Jinja2 template, auto-injecting common context.

    Unit 4: replaces the legacy ``_render(HTML, ...)`` which passed the
    HTML string directly to ``render_template_string``. Now takes a
    template *file* name (e.g., ``"index.html"``) and Flask's
    ``render_template`` finds it under ``webui_app/templates/``.

    Auto-injected context (when not provided by caller):
      - history, blogger_token_status, profiles, draft_queue,
        now_iso, suggested_next, incomplete_run
    """
    if 'history' not in kwargs:
        kwargs['history'] = _history_store.load()
    if 'blogger_token_status' not in kwargs:
        kwargs['blogger_token_status'] = _get_blogger_token_status()
    if 'profiles' not in kwargs:
        kwargs['profiles'] = _profiles_store.load()
    if 'draft_queue' not in kwargs:
        kwargs['draft_queue'] = _drafts_store.load()
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


# ─────────────────────────────────────────────────────────────────────────────
# In-memory work-themed run store (shared between /sites routes)
# ─────────────────────────────────────────────────────────────────────────────


_WORK_THEMED_RUNS: dict[str, dict] = {}
_WORK_THEMED_RUNS_MAX = 50


def _parse_run_result(stdout: str, entry) -> list[dict]:
    """Parse plan-backlinks JSONL stdout into per-work-url status rows."""
    rows = []
    work_urls = list(entry.work_urls or [])
    by_url: dict[str, dict] = {}
    for line in (stdout or '').strip().split('\n'):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        wurl = obj.get('work_url') or obj.get('target_url', '')
        if wurl:
            by_url[wurl] = obj
    for wurl in work_urls:
        obj = by_url.get(wurl)
        if obj is None:
            rows.append({'work_url': wurl, 'status': 'missing'})
        elif obj.get('error'):
            rows.append({'work_url': wurl, 'status': 'scrape_failed',
                         'error': obj.get('error', '')})
        else:
            rows.append({'work_url': wurl, 'status': 'success'})
    return rows


__all__ = [
    # Constants
    '_FLASK_PORT', '_RUN_ID_RE', '_LOOPBACK_HOSTS',
    '_DERIVED_BRANDED_MAX', '_DERIVED_PARTIAL_MAX', '_DERIVED_PARTIAL_KEEP',
    '_DERIVED_PARTIAL_SPLIT_RE',
    '_WORK_THEMED_RUNS', '_WORK_THEMED_RUNS_MAX',
    # Functions
    '_content_gate_enabled', '_verify_urls_or_error',
    '_get_blogger_token_status',
    'fetch_url_metadata', 'fetch_full_tdk',
    'detect_platform', 'detect_language', 'get_main_domain',
    '_parse_publish_results', '_normalize_url',
    '_persist_three_tier_config',
    '_load_schedule_settings', '_save_schedule_settings',
    '_calc_next_available',
    '_load_incomplete_run',
    '_check_localhost', '_validate_webui_run_id',
    '_oauth_callback_uri', '_resolve_bind_host',
    '_ensure_csrf_token', '_check_csrf_or_abort',
    '_parse_lines',
    '_wire_content_fetch_ttl_from_env',
    '_derive_branded_pool', '_derive_partial_pool', '_derive_exact_pool',
    '_settings_context', '_draft_tab_extra',
    'run_pipe', '_render', '_parse_run_result',
]
