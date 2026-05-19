"""Medium browser-login POST routes: launch / probe / clear.

CSRF: double-submit cookie pattern (no Flask-WTF dependency).
Existing routes in this project have no CSRF; these three are the first
to carry it because they spawn OS-level browser processes.
SEC-001 mitigation — Plan 013.
"""

from __future__ import annotations

import secrets

from flask import Blueprint, make_response, redirect, request, session

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config import load_config

from ..medium_login import (
    clear_browser_profile,
    launch_login_window,
    probe_login_status,
)

bp = Blueprint("medium_login", __name__)

_CSRF_COOKIE = "medium_csrf"
_CSRF_FIELD = "_csrf_token"


# ── CSRF helpers ──────────────────────────────────────────────────────────────

def _ensure_csrf_token() -> str:
    """Return the CSRF token, creating it on first call."""
    if _CSRF_COOKIE not in session:
        session[_CSRF_COOKIE] = secrets.token_hex(32)
    return session[_CSRF_COOKIE]


def _validate_csrf() -> bool:
    session_token = session.get(_CSRF_COOKIE)
    form_token = request.form.get(_CSRF_FIELD)
    return bool(session_token and form_token and secrets.compare_digest(
        session_token, form_token
    ))


@bp.before_request
def _csrf_check() -> None:
    if request.method == "POST":
        if not _validate_csrf():
            return redirect(
                "/settings?flash_type=danger"
                "&flash_msg=CSRF token 无效，请刷新页面后重试#channel-medium"
            )


# Jinja global so templates can call {{ medium_csrf_token() }}
@bp.app_context_processor
def _inject_csrf() -> dict:
    return {"medium_csrf_token": _ensure_csrf_token}


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/settings/medium/launch-browser-login", methods=["POST"])
def medium_launch_browser_login():
    """Open a headed Chromium for the user to log in to Medium."""
    cfg = load_config()
    try:
        result = launch_login_window(cfg)
        session["medium_probe_logged_in"] = result.get("logged_in", False)
        return redirect(
            "/settings?flash_type=success"
            "&flash_msg=Medium 浏览器登录完成！#channel-medium"
        )
    except DependencyError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )
    except ExternalServiceError as e:
        return redirect(
            f"/settings?flash_type=danger&flash_msg={e}#channel-medium"
        )


@bp.route("/settings/medium/probe-browser-login", methods=["POST"])
def medium_probe_browser_login():
    """Probe Medium login state via a short Playwright navigation."""
    cfg = load_config()
    try:
        result = probe_login_status(cfg)
        if result["logged_in"]:
            session["medium_probe_logged_in"] = True
            name = f" (@{result['username']})" if result.get("username") else ""
            msg = f"Medium 登录有效{name}，发布通道就绪"
        else:
            session.pop("medium_probe_logged_in", None)
            msg = "Medium 未登录，请点击「打开浏览器登录」完成登录"
        return redirect(
            f"/settings?flash_type=info&flash_msg={msg}#channel-medium"
        )
    except DependencyError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )
    except ExternalServiceError as e:
        return redirect(
            f"/settings?flash_type=warning&flash_msg={e}#channel-medium"
        )


@bp.route("/settings/medium/clear-browser-login", methods=["POST"])
def medium_clear_browser_login():
    """Delete the persistent Chromium profile (clears stored login cookies)."""
    cfg = load_config()
    try:
        clear_browser_profile(cfg)
        session.pop("medium_probe_logged_in", None)
        return redirect(
            "/settings?flash_type=success"
            "&flash_msg=浏览器登录已清除；下次发布前请重新登录#channel-medium"
        )
    except Exception as e:
        return redirect(
            f"/settings?flash_type=danger&flash_msg=清除失败: {e}#channel-medium"
        )
