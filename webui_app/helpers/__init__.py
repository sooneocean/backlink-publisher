"""Shared helpers extracted from legacy webui.py — Plan 2026-05-18-001 Unit 3."""

from __future__ import annotations

import json
import os
import random
import re
import secrets
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import abort, render_template, request, session
from google.oauth2.credentials import Credentials

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher.content import fetch as content_fetch
from backlink_publisher.config import (
    _config_dir,
    _domain_label,
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

# url_meta functions used by remaining __init__ code (moved in Unit 1).
from .url_meta import _is_fetch_verify_disabled  # noqa: E402


def _llm_settings_file() -> Path:
    # Lazy so BACKLINK_PUBLISHER_CONFIG_DIR rebinds are honored per-call.
    return _config_dir() / 'llm-settings.json'


_FLASK_PORT = int(os.environ.get('PORT', 8888))
_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_TRUTHY_BYPASS = {"1", "true", "yes"}


def _image_gen_status(cfg) -> dict:
    """Snapshot of image-gen state for the Settings template.

    Reads ``Config.image_gen`` (config.toml ``[image_gen]`` section) plus
    the on-disk presence + mtime of ``frw-token.json``.  The api_key
    itself is NEVER returned — even shape/length information leaks
    timing-attack surface.
    """
    import datetime as _dt
    cfg_dict: dict | None = None
    if cfg.image_gen is not None:
        cfg_dict = {
            "base_url": cfg.image_gen.base_url,
            "model": cfg.image_gen.model,
            "banner_size": cfg.image_gen.banner_size,
            "daily_cap": cfg.image_gen.daily_cap,
            "per_run_cap": cfg.image_gen.per_run_cap,
            "strict": cfg.image_gen.strict,
            "use_image_gen": cfg.image_gen.use_image_gen,
            "auto_disable_threshold": cfg.image_gen.auto_disable_threshold,
        }

    token_path = cfg.frw_token_path
    token_present = token_path.exists()
    token_mtime: str | None = None
    if token_present:
        try:
            token_mtime = _dt.datetime.fromtimestamp(
                token_path.stat().st_mtime, tz=_dt.timezone.utc
            ).strftime("%Y-%m-%d %H:%M UTC")
        except OSError:
            token_mtime = None

    return {
        "configured": cfg_dict is not None,
        "config": cfg_dict,
        "token_path": str(token_path),
        "token_present": token_present,
        "token_mtime": token_mtime,
    }


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
        'use_image_gen': False
    }
    path = _llm_settings_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            defaults.update(data)
        except Exception:
            plan_logger.warning("failed to parse llm-settings.json, using defaults")
    return defaults


# _is_fetch_verify_disabled, _content_gate_enabled, _verify_urls_or_error
# → moved to helpers/url_meta.py (Plan 2026-05-21-007 Unit 1)


# ─────────────────────────────────────────────────────────────────────────────
# Token status (Blogger)
# ─────────────────────────────────────────────────────────────────────────────


def _get_blogger_token_status() -> dict:
    """Return token health status without making network calls."""
    try:
        cfg = load_config()
        token_data = load_blogger_token(cfg.blogger_token_path)
        if not token_data:
            return {'state': 'none', 'label': '未授权', 'days_left': None}
        if not cfg.blogger_oauth:
            return {'state': 'none', 'label': '未配置 OAuth', 'days_left': None}
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


def _get_velog_status() -> dict:
    """Return velog channel status for the WebUI badge (6 states).

    States:
      fresh            — file just written (mtime < 60 s)
      ok               — file exists, 0600, parseable, cap not reached
      warn             — file exists but JSON broken / cookies empty
      err              — file missing (needs velog-login)
      cap_reached      — daily cap exhausted
      permission_denied — file exists but WebUI uid cannot read (not 0600 or EPERM)
    """
    try:
        cfg = load_config()
        from backlink_publisher.publishing.adapters.velog_graphql import (
            _effective_cap,
            _read_count,
        )
        velog_cfg = cfg.velog
        cookies_path = (
            velog_cfg.cookies_path if velog_cfg else
            cfg.config_dir / "velog-cookies.json"
        )
        count_path = cfg.config_dir / "velog-rate-limit.json"
        cap = _effective_cap()

        # file absent → err
        if not cookies_path.exists():
            return {
                'state': 'err',
                'label': '未绑定',
                'guide': '运行: velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        # permission check
        try:
            mode = os.stat(cookies_path).st_mode & 0o777
            if mode != 0o600:
                return {
                    'state': 'permission_denied',
                    'label': f'权限错误 ({oct(mode)})',
                    'guide': f'chmod 600 {cookies_path}',
                    'cookies_path': str(cookies_path),
                    'count': 0,
                    'cap': cap,
                }
        except PermissionError:
            return {
                'state': 'permission_denied',
                'label': '无法读取 cookie 文件（uid 不匹配）',
                'guide': f'chmod 640 {cookies_path}  # 或确认 WebUI 与 CLI 使用同一 uid',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        # parse cookies
        try:
            raw = json.loads(cookies_path.read_text())
            cookie_list = raw.get('cookies', [])
            if not cookie_list:
                return {
                    'state': 'warn',
                    'label': 'Cookie 文件为空',
                    'guide': 'velog-login',
                    'cookies_path': str(cookies_path),
                    'count': 0,
                    'cap': cap,
                }
        except Exception:
            return {
                'state': 'warn',
                'label': 'Cookie 文件解析失败',
                'guide': 'velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

        # daily count
        count, _ = _read_count(count_path)
        if count >= cap:
            return {
                'state': 'cap_reached',
                'label': f'今日上限已达 ({count}/{cap})',
                'guide': '重置时间：UTC 午夜',
                'cookies_path': str(cookies_path),
                'count': count,
                'cap': cap,
            }

        # fresh: mtime < 60s
        mtime = cookies_path.stat().st_mtime
        if (datetime.now().timestamp() - mtime) < 60:
            return {
                'state': 'fresh',
                'label': '刚刚绑定',
                'guide': '',
                'cookies_path': str(cookies_path),
                'count': count,
                'cap': cap,
            }

        # ok
        return {
            'state': 'ok',
            'label': f'已绑定（今日 {count}/{cap}）',
            'guide': '',
            'cookies_path': str(cookies_path),
            'count': count,
            'cap': cap,
        }

    except Exception as exc:
        return {
            'state': 'err',
            'label': f'状态检查失败: {exc}',
            'guide': 'velog-login',
            'cookies_path': '',
            'count': 0,
            'cap': 5,
        }


# _fetch_page, _extract_title, _extract_description, fetch_url_metadata,
# fetch_full_tdk, detect_platform, detect_language, get_main_domain,
# _normalize_url → moved to helpers/url_meta.py (Plan 2026-05-21-007 Unit 1)


def _parse_publish_results(jsonl_str):
    results = []
    for line in (jsonl_str or '').strip().split('\n'):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


# ─────────────────────────────────────────────────────────────────────────────
# History truth-propagation (Plan 2026-05-19-006 Unit 1)
# ─────────────────────────────────────────────────────────────────────────────

_HISTORY_MAX_ITEMS = 100


def _push_history_per_row(
    rows: list[dict],
    *,
    target_url_fallback: str = "unknown",
    platform_fallback: str = "",
    language_fallback: str = "",
) -> list[dict]:
    """Append one history entry per CLI publish-result row, preserving the
    per-row ``status`` field (including ``*_unverified`` suffixes).

    Plan 2026-05-19-006 Unit 1 root-cause fix: previously the three WebUI
    callsites (``_publish_draft_job`` / batch / publish-real) collapsed a
    multi-row publish-backlinks stdout into one history entry whose status
    was hard-coded to ``'drafted'`` or ``'published'`` regardless of the
    real per-row outcome. The ``*_unverified`` rows therefore showed up
    as solid green ✓ even though the outside site never received the
    article.

    This helper writes one history item per row, transparently carrying
    the row's real ``status`` and ``error``, and synthesises a ``failed``
    status when both ``published_url`` and ``draft_url`` are empty (which
    means the adapter returned no usable URL).
    """
    if not rows:
        return _history_store.load()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    new_items: list[dict] = []
    for row in rows:
        published_url = (row.get("published_url") or "").strip()
        draft_url = (row.get("draft_url") or "").strip()
        article_urls = [u for u in (published_url, draft_url) if u]
        raw_error = row.get("error")
        status = row.get("status") or ""
        # Coerce "no URL returned but no error" to failed — adapter silently
        # gave us nothing usable.
        if not article_urls and not raw_error and not status.endswith("_unverified"):
            status = "failed"
            raw_error = "no URL returned by adapter"
        elif not status:
            status = "failed" if raw_error else "published"
        item = {
            "id": str(uuid.uuid4())[:8],
            "target_url": row.get("target_url") or target_url_fallback,
            "platform": row.get("platform") or platform_fallback,
            "language": row.get("language") or language_fallback,
            "status": status,
            "created_at": row.get("created_at") or now_str,
            "article_urls": article_urls,
            "title": row.get("title", ""),
            "adapter": row.get("adapter", ""),
        }
        if raw_error:
            item["error"] = raw_error
        new_items.append(item)
    return _history_store.update(
        lambda hist: [*new_items, *hist][:_HISTORY_MAX_ITEMS]
    )


def _push_history_single_failure(
    *,
    target_url: str,
    platform: str,
    language: str,
    error: str,
) -> list[dict]:
    """Append one synthetic ``failed`` history entry — used when the publish
    CLI itself blew up (subprocess returncode!=0 or exception) and there
    are no per-row outputs to forward."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    item = {
        "id": str(uuid.uuid4())[:8],
        "target_url": target_url or "unknown",
        "platform": platform,
        "language": language,
        "status": "failed",
        "created_at": now_str,
        "article_urls": [],
        "title": "",
        "adapter": "",
        "error": error or "publish failed",
    }
    return _history_store.update(
        lambda hist: [item, *hist][:_HISTORY_MAX_ITEMS]
    )


def _apply_history_cap(hist: list[dict]) -> list[dict]:
    """Trim history to the configured maximum, newest-first order preserved."""
    return hist[:_HISTORY_MAX_ITEMS]


# Statuses that require at least one article URL — operator-visible "success"
# states must be backed by a real URL or the publish-history invariant is broken.
_REQUIRES_URL_STATUSES: frozenset[str] = frozenset({"published", "drafted"})


def _push_history_aggregate(entry: dict) -> list[dict]:
    """Append a single caller-built aggregate entry to publish history.

    Unlike ``_push_history_per_row`` (which writes one entry per CLI row),
    this helper is for callers that have already collapsed N rows into one
    entry — e.g. ``checkpoint.py`` which writes a per-resume summary rather
    than per-row details.

    Invariant: if ``entry['status']`` is in ``_REQUIRES_URL_STATUSES`` then
    ``entry['article_urls']`` must be non-empty.  Callers whose status-collapse
    logic (e.g. exit-code 4 = failed_partial) produces statuses outside this
    set are always accepted.

    Raises:
        ValueError: if the invariant is violated.
    """
    if (entry.get("status") in _REQUIRES_URL_STATUSES
            and not entry.get("article_urls")):
        raise ValueError(
            f"_push_history_aggregate: entry status={entry.get('status')!r} "
            f"requires non-empty article_urls; got {entry.get('article_urls')!r}"
        )
    return _history_store.update(
        lambda hist: _apply_history_cap([entry, *hist])
    )


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
                    plan_logger.warn("_calc_next_available: bad date in drafts_store", ts=ts)

    for item in _history_store.load():
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
    if request.remote_addr not in _LOOPBACK_HOSTS:
        abort(403)


# ─────────────────────────────────────────────────────────────────────────────
# Safe redirect helpers (Plan 2026-05-21-006 Unit 3)
# ─────────────────────────────────────────────────────────────────────────────

# Max length for any operator-visible flash message embedded in a redirect URL.
# Long messages get truncated rather than rejected because the operator still
# needs *some* hint of what went wrong. 200 chars is enough for one short
# Chinese sentence plus a stack-trace summary.
_FLASH_MSG_MAX_LEN = 200


def _safe_flash_redirect(path: str, *, flash_type: str = "", msg: str = "",
                         fragment: str = ""):
    """Return a Flask redirect Response with a sanitized ``flash_msg`` query.

    Plan 2026-05-21-006 Unit 3.4 / F26: 11+ routes interpolated raw
    exception text into ``redirect(f"/x?flash_msg={e}")``. Werkzeug
    sanitises the Location header, but bare exception text can carry
    CRLF, ANSI escapes, or quote characters that confuse log forwarders
    and naive proxy middlewares. This helper centralises the sanitisation
    so any flash path gets the same treatment.

    Sanitisation:
      * Strip CR/LF (header-injection defence)
      * Replace tabs with spaces
      * Cap to ``_FLASH_MSG_MAX_LEN`` characters
      * URL-quote so query semantics survive
      * Strip leading/trailing whitespace

    The Jinja autoescape on the receiving end (settings.html etc.) already
    protects against XSS in the rendered message; this helper guards the
    transport layer (the URL) only.
    """
    from urllib.parse import quote
    from flask import redirect as _flask_redirect

    safe_msg = msg.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    safe_msg = safe_msg.strip()[:_FLASH_MSG_MAX_LEN]
    parts = []
    if flash_type:
        # flash_type is a short controlled vocab (success/danger/warning/info);
        # quote defensively but it should already be safe.
        parts.append(f"flash_type={quote(flash_type, safe='')}")
    if safe_msg:
        parts.append(f"flash_msg={quote(safe_msg, safe='')}")
    qs = ('?' + '&'.join(parts)) if parts else ''
    frag = ('#' + fragment) if fragment else ''
    return _flask_redirect(path + qs + frag)


def _safe_referrer_redirect(default: str = '/'):
    """Same-origin guard for ``redirect(request.referrer or '/')``.

    Plan 2026-05-21-006 Unit 3.3 / F8: ``request.referrer`` is attacker-
    controllable (browser sends whatever the previous page was), so naive
    use is an open-redirect vector when combined with CSRF bypass. This
    helper checks that the referrer's scheme + host match ``request.host_url``,
    falling back to ``default`` otherwise.
    """
    from flask import redirect as _flask_redirect
    referrer = request.referrer or ''
    if not referrer:
        return _flask_redirect(default)
    try:
        ref = urlparse(referrer)
        host = urlparse(request.host_url)
    except Exception:
        return _flask_redirect(default)
    if (ref.scheme, ref.netloc) != (host.scheme, host.netloc):
        return _flask_redirect(default)
    return _flask_redirect(referrer)


def _validate_webui_run_id(run_id):
    if not run_id or not _RUN_ID_RE.match(run_id):
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
    """Accept CSRF token from ``request.form['csrf_token']`` (form POST) OR
    ``X-CSRFToken`` header (JS fetch with JSON body). The dashboard JS in
    Unit 5 calls /api/<ch>/* via fetch(), which can't easily multipart-encode
    a CSRF field — header threading is the canonical JSON-fetch pattern.

    Plan 2026-05-19-006 Unit 4 — SEC-4 review recommendation.
    """
    token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken", "")
    expected = session.get("csrf_token", "")
    if not token or not expected or not secrets.compare_digest(token, expected):
        abort(403)


# ─────────────────────────────────────────────────────────────────────────────
# Plan 2026-05-19-003 Unit 3 — bind-route security helpers
#
# These stack with the existing _check_localhost (off-host network attacker)
# and _check_csrf_or_abort (same-origin XSS) defenses. Each helper defends
# a distinct attack class:
#
#   _check_localhost              — network-level (RemoteAddr filter)
#   _check_bind_origin_or_abort   — browser-level (cross-origin + DNS rebinding)
#   _check_csrf_or_abort          — same-origin XSS (token check)
#   _refuse_when_allow_network    — operator-mode (hard-disable under
#                                     BACKLINK_PUBLISHER_ALLOW_NETWORK=1)
#
# Plan 001 Unit 4's bind routes are expected to call all four. There is no
# CI gate enforcing this (deferred per Plan 003 scope-guardian); coordinate
# via PR review.
# ─────────────────────────────────────────────────────────────────────────────


def _check_bind_origin_or_abort() -> None:
    """Reject browser-originated cross-origin POSTs and DNS rebinding.

    Decision tree:
      1. ``Origin`` present and allowlisted (loopback host + ``_FLASK_PORT``
         + HTTP scheme) → check Referer if also present.
      2. ``Origin`` present but not allowlisted (including ``null``) → 403.
      3. ``Origin`` absent + ``Referer`` present and allowlisted → pass.
         (Some browsers strip Origin from same-site POSTs; Referer is the
         legitimate fallback signal.)
      4. ``Origin`` absent + ``Referer`` absent → 403 (state-changing
         routes MUST carry at least one signal).
      5. When both Origin and Referer are present, BOTH must allowlist —
         a mismatch indicates an off-origin redirect chain.

    HTTPS Origin claiming a loopback host is rejected: our webui is
    HTTP-only on loopback; an HTTPS origin is a TLS-stripping vector.
    """
    from flask import abort
    from urllib.parse import urlparse

    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")

    def _is_allowlisted(url: str | None) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme != "http":
            return False
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            return False
        # Default port for http is 80; absent port means 80, which
        # doesn't match _FLASK_PORT. Compare explicit ports only.
        if parsed.port != _FLASK_PORT:
            return False
        return True

    origin_ok = _is_allowlisted(origin) if origin else None  # None = absent
    referer_ok = _is_allowlisted(referer) if referer else None

    if origin is not None and not origin_ok:
        abort(403)
    if referer is not None and not referer_ok:
        abort(403)
    if origin is None and referer is None:
        abort(403)
    # At this point: at least one signal is present and that signal
    # allowlists. If both are present, both allowlist. Pass.


def _refuse_when_allow_network() -> None:
    """Hard-disable bind endpoints when the operator has opted into
    network exposure via ``BACKLINK_PUBLISHER_ALLOW_NETWORK=1``.

    Returns 403 with a JSON body carrying the discriminator
    ``"bind_disabled_under_allow_network"`` so the operator / UI knows
    this isn't a generic auth or CSRF rejection. Plan 003 Key Technical
    Decisions reserves the future env var name ``BACKLINK_PUBLISHER_BIND_
    TOKEN`` for a possible v1.1 escape hatch (not implemented here).
    """
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        from flask import abort, make_response, jsonify
        response = make_response(
            jsonify(
                error="bind_disabled_under_allow_network",
                message=(
                    "Bind endpoints are disabled when "
                    "BACKLINK_PUBLISHER_ALLOW_NETWORK=1. Bind in v1 "
                    "requires loopback-only access; un-set the env var, "
                    "bind locally, then re-export it."
                ),
            ),
            403,
        )
        abort(response)


def _parse_lines(raw: str) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _wire_content_fetch_ttl_from_env() -> None:
    if _is_fetch_verify_disabled():
        return
    raw = os.environ.get("BACKLINK_GATE_CACHE_TTL_SECONDS", "900").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    if seconds <= 0:
        return
    content_fetch.set_default_max_age(seconds)


# _DERIVED_BRANDED_MAX, _DERIVED_PARTIAL_MAX, _DERIVED_PARTIAL_KEEP,
# _DERIVED_PARTIAL_SPLIT_RE, _derive_branded_pool, _derive_partial_pool,
# _derive_exact_pool → moved to helpers/url_meta.py (Plan 2026-05-21-007 Unit 1)


# ─────────────────────────────────────────────────────────────────────────────
# Medium browser status (filesystem-only probe, no network / Playwright calls)
# ─────────────────────────────────────────────────────────────────────────────


def _get_medium_browser_status(cfg, *, session=None) -> dict:
    """Return a dict describing the Medium browser fallback readiness.

    Reads only the filesystem and Python import state — no Playwright launch,
    no network call.  ``logged_in`` state is set only via flask.session after
    a successful probe_login_status() invocation (Unit 5).
    """
    import platform as _plat
    from datetime import datetime, timezone

    try:
        from backlink_publisher.publishing.adapters.medium_browser import (
            sync_playwright as _spw,
        )
        playwright_installed = _spw is not None
    except Exception:
        playwright_installed = False

    brave_macos = _plat.system() == "Darwin"
    user_data_dir = cfg.medium_user_data_dir or (cfg.config_dir / "chrome-profile-default")
    cookies_path = user_data_dir / "Default" / "Cookies"
    singleton_path = user_data_dir / "SingletonLock"

    profile_has_cookies = cookies_path.exists()
    singleton_lock_present = singleton_path.exists()
    cookies_mtime: str | None = None
    cookies_age_days: int | None = None

    if profile_has_cookies:
        try:
            mtime = cookies_path.stat().st_mtime
            dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            cookies_mtime = dt.isoformat()
            cookies_age_days = (datetime.now(timezone.utc) - dt).days
        except OSError:
            cookies_age_days = 0

    if not playwright_installed and not brave_macos:
        state = "not_installed"
    elif not profile_has_cookies:
        state = "no_profile"
    elif session is not None and session.get("medium_probe_logged_in"):
        state = "logged_in"
    else:
        state = "profile_exists_unverified"

    return dict(
        playwright_installed=playwright_installed,
        brave_macos=brave_macos,
        profile_dir=str(user_data_dir),
        profile_has_cookies=profile_has_cookies,
        cookies_mtime=cookies_mtime,
        cookies_age_days=cookies_age_days,
        singleton_lock_present=singleton_lock_present,
        state=state,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Settings context
# ─────────────────────────────────────────────────────────────────────────────


def _token_paste_status(cfg, channel: str, load_fn, *, token_field: str = "token") -> dict:
    """Status dict consumed by _settings_channel_token_paste.html.

    Reads the platform's token file via the load function injected by
    the caller (so e.g. callers passing `load_ghpages_token` don't need
    to know the file path). Returns {bound, masked, dofollow}.
    Defensive against any load failure — broken token files surface as
    "unbound" rather than crashing the settings page render.

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
    rather than the single 'token' field used by ghpages/hashnode/devto.
    Mirrors ``_token_paste_status`` but reads integration_token for the
    masked display and checks both fields for bound status.
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
        load_hashnode_token,
        load_medium_token,
        load_notion_token,
    )
    from backlink_publisher.cli._bind.channels import CHANNELS
    from webui_store.channel_status import list_all as _channel_list_all
    from ..services.bind_job import BIND_ERROR_MESSAGES

    cfg = load_config()
    token_data = load_blogger_token(cfg.blogger_token_path)
    medium_token_data = load_medium_token()

    # Phase 3 token-paste platforms (2026-05-20).
    ghpages_status = _token_paste_status(cfg, "ghpages", load_ghpages_token)
    hashnode_status = _token_paste_status(cfg, "hashnode", load_hashnode_token)
    ghpages_config_summary = [
        ("repo", cfg.ghpages.repo if cfg.ghpages else ""),
        ("branch", cfg.ghpages.branch if cfg.ghpages else "gh-pages"),
        ("path_template", cfg.ghpages.path_template if cfg.ghpages else "_posts/{date}-{slug}.md"),
    ]
    hashnode_config_summary = [
        ("publication_id", cfg.hashnode.publication_id if cfg.hashnode else ""),
    ]

    # Phase 2 Plan 003 token-paste platforms (2026-05-21): Notion + Dev.to.
    notion_status = _token_paste_status_notion(cfg, load_notion_token)
    devto_status = _token_paste_status(cfg, "devto", load_devto_token, token_field="api_key")
    notion_config_summary: list[tuple[str, str]] = []
    devto_config_summary: list[tuple[str, str]] = []

    token = cfg.medium_integration_token or ""
    masked = ("*" * 8 + token[-4:]) if len(token) > 4 else ("*" * len(token))

    all_targets = sorted(
        set(cfg.blogger_blog_ids.keys()) | set(cfg.target_anchor_keywords.keys())
    )

    # Plan 2026-05-19-003 Unit 5: refresh medium's last_verified_at /
    # status via the liveness probe BEFORE reading the store. The probe
    # short-circuits when the cache is fresh (< 5 min) or when the store
    # already says expired/unbound — typical cost is one stat call. When
    # the active probe is enabled (default False until Spike 2 confirms
    # anti-bot safety), the probe runs in a worker thread with a 10s
    # budget; total Settings GET latency is capped.
    try:
        from ..medium_liveness import medium_liveness_check
        medium_liveness_check()
    except Exception:  # noqa: BLE001 — Settings render must not depend on probe
        pass

    try:
        channel_statuses = _channel_list_all()
    except Exception:
        channel_statuses = {}

    # csrf_token consumed by Unit 5's bind_channel.js (via <meta name="csrf-token">)
    try:
        csrf_token = _ensure_csrf_token()
    except Exception:
        csrf_token = ""

    velog_status = _get_velog_status()

    try:
        from backlink_publisher.publishing.registry import registered_platforms
        from ..binding_status import get_channel_status, HIDDEN_FROM_UI
        dashboard_channels = [
            (name, get_channel_status(name, cfg))
            for name in registered_platforms()
            if name not in HIDDEN_FROM_UI
        ]
    except Exception:
        dashboard_channels = []

    return dict(
        flash=flash,
        csrf_token=csrf_token,
        dashboard_channels=dashboard_channels,
        medium_browser_status=_get_medium_browser_status(cfg, session=_flask_session),
        blogger_token=bool(token_data),
        blogger_client_id=cfg.blogger_oauth.client_id if cfg.blogger_oauth else "",
        # Boolean only — raw secret stays out of the template render context
        # so a future regression like value="{{ blogger_client_secret }}" can't
        # accidentally leak it (P3 defence-in-depth).
        blogger_client_secret_set=bool(cfg.blogger_oauth and cfg.blogger_oauth.client_secret),
        blog_ids=cfg.blogger_blog_ids,
        medium_token_set=bool(token),
        medium_token_masked=masked if token else "",
        # Single-source truth for whether a medium-token.json exists on disk.
        # Used to show/hide the "clear OAuth token" button for legacy users.
        # Avoids the AND-race where save_config drops [medium.oauth] block,
        # causing medium_oauth_configured to silently flip False.
        medium_token_file_exists=bool(medium_token_data),
        medium_oauth_configured=bool(medium_token_data and cfg.medium_oauth),
        config_path=str(cfg.config_dir / "config.toml"),
        token_path=str(cfg.blogger_token_path),
        port=_FLASK_PORT,
        callback_uri=_oauth_callback_uri(),
        profiles=_profiles_store.load(),
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
        hashnode_status=hashnode_status,
        hashnode_config_summary=hashnode_config_summary,
        notion_status=notion_status,
        notion_config_summary=notion_config_summary,
        devto_status=devto_status,
        devto_config_summary=devto_config_summary,
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
    # Detect silent-failure: exit 0 with empty stdout AND empty stderr is
    # almost always a broken entry-point (e.g. `python -m <package>` against
    # an empty __main__.py, or a module that defines main() without a
    # `if __name__ == "__main__":` guard). Surface a real diagnostic instead
    # of letting callers fall back to misleading hardcoded error strings.
    if stdin and not result.stdout.strip() and not result.stderr.strip():
        invoked = ' '.join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        raise Exception(
            f"CLI '{invoked}' produced no output (exit 0, stdout/stderr empty). "
            f"Likely a missing __main__.py or `if __name__ == \"__main__\":` "
            f"guard. Rewritten command: {new_cmd}"
        )
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
      - history, blogger_token_status, profiles, draft_queue, tasks,
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
    if 'tasks' not in kwargs:
        try:
            kwargs['tasks'] = _queue_store.load()
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
    '_check_bind_origin_or_abort', '_refuse_when_allow_network',
    '_parse_lines',
    '_wire_content_fetch_ttl_from_env',
    '_derive_branded_pool', '_derive_partial_pool', '_derive_exact_pool',
    '_settings_context', '_draft_tab_extra',
    'run_pipe', '_render', '_parse_run_result',
]
