"""URL verify route — Plan v1.0 Unit 3.

Security perimeter for the homepage URL auto-derive feature. Behind the
4-guard stack (loopback / ALLOW_NETWORK / Origin / CSRF) we validate and
normalize the URL server-side (R8e), gate through the per-session +
per-host throttle (Unit 2), then delegate the actual fetch to
``content_fetch.verify_url_has_content`` (Unit 1).

Contract
--------
POST /url-verify with JSON body ``{"url": "<string>"}``. Response is
always a JSON object with the closed shape ``{ok, status, title, reason}``
and HTTP 200 — except for guard failures (403) and the
``BACKLINK_NO_FETCH_VERIFY=1`` short-circuit (204).

``reason`` is a closed enum (R8e — UI relies on this for i18n keying):

  ok | invalid_url | network_error | ssrf_blocked | timeout | http_404
  | http_5xx | http_200_no_title | soft_404_title | body_too_small
  | blocked_scheme | rate_limited | host_busy | upstream_overloaded

The response body NEVER includes the resolved IP family or the raw URL —
only the reason discriminator. RECON log lines are emitted for security
events (CSRF/Origin reject, ALLOW_NETWORK reject, throttle rejection,
SSRF block) with a SHA-256/8 host hash, never the raw host or URL.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from urllib.parse import urlparse, urlunparse

from flask import Blueprint, abort, jsonify, request, session

from backlink_publisher._util.logger import get_logger

from ..helpers import (
    _LOOPBACK_HOSTS,
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
)
from ..helpers.url_meta import _is_fetch_verify_disabled
from ..services import url_verify_throttle as throttle


bp = Blueprint("url_verify", __name__)
_logger = get_logger("url-verify")

_MAX_URL_LEN = 2048
_TITLE_CAP = 24
_FETCH_TIMEOUT_S = 5
_FETCH_MAX_REDIRECTS = 3
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _host_hash(host: str) -> str:
    return hashlib.sha256(host.encode("utf-8")).hexdigest()[:8]


def _request_id() -> str:
    return secrets.token_hex(4)


def _session_id() -> str:
    return session.get("csrf_token", "")


def _emit_recon(reason: str, host: str = "") -> None:
    """Emit a RECON log line gated by the per-session 1/10s cap.

    Fields are intentionally minimal: host_hash + request_id + reason. No
    raw URL, no raw host, no IP — RECON is for operator visibility, not
    forensics.
    """
    sid = _session_id()
    if not throttle.should_emit_recon(sid):
        return
    _logger.recon(
        "url-verify.event",
        reason=reason,
        host_hash=_host_hash(host) if host else "",
        request_id=_request_id(),
    )


def _uniform(ok: bool, reason: str, title: str = "") -> tuple:
    return jsonify({
        "ok": bool(ok),
        "status": 200 if ok else 0,
        "title": title[:_TITLE_CAP] if title else "",
        "reason": reason,
    })


@bp.before_request
def _enforce_loopback() -> None:
    if request.remote_addr not in _LOOPBACK_HOSTS:
        _emit_recon("non_loopback_remote")
        abort(403)


def _parse_and_normalize(raw_url) -> tuple[str | None, str | None, str]:
    """Validate + normalize the URL. Returns (clean_url, reject_reason, host).

    On success: (clean_url, None, host_ascii).
    On failure: (None, reason, "") where reason ∈ {invalid_url, blocked_scheme}.
    """
    if not isinstance(raw_url, str) or not raw_url:
        return None, "invalid_url", ""
    if len(raw_url) > _MAX_URL_LEN:
        return None, "invalid_url", ""
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None, "invalid_url", ""
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return None, "blocked_scheme", ""
    host = parsed.hostname
    if not host:
        return None, "invalid_url", ""
    try:
        host_ascii = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError):
        return None, "invalid_url", ""
    # Strip userinfo by rebuilding netloc from hostname + optional port.
    new_netloc = host_ascii + (f":{parsed.port}" if parsed.port else "")
    clean = urlunparse(parsed._replace(netloc=new_netloc))
    return clean, None, host_ascii


@bp.route("/url-verify", methods=["POST"])
def url_verify():
    # Guard 2: hard-disable when ALLOW_NETWORK=1
    if os.environ.get("BACKLINK_PUBLISHER_ALLOW_NETWORK") == "1":
        _emit_recon("allow_network_refused")
    _refuse_when_allow_network()
    # Guard 3: cross-origin / DNS rebinding
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    if origin is None and referer is None:
        _emit_recon("origin_missing")
    _check_bind_origin_or_abort()
    # Guard 4: CSRF — handled by app-level ``_global_csrf_guard``
    # (webui_app/__init__.py). If we reach this line, the token already
    # passed validation.

    # Short-circuit BEFORE throttle consumption — the operator escape hatch
    # must not be silently rate-limited.
    if _is_fetch_verify_disabled():
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return _uniform(False, "invalid_url")
    raw_url = payload.get("url", "")

    clean_url, reject_reason, host_ascii = _parse_and_normalize(raw_url)
    if reject_reason is not None:
        return _uniform(False, reject_reason)

    rejection = throttle.try_acquire(
        session_id=_session_id(), host=host_ascii,
    )
    if rejection is not None:
        _emit_recon(rejection, host=host_ascii)
        return _uniform(False, rejection)

    # Lazy import: re-resolve at call time so test monkeypatch of
    # ``backlink_publisher.content_fetch.verify_url_has_content`` is honored
    # (the conftest autouse mock patches at the legacy alias module).
    try:
        from backlink_publisher.content import fetch as _cf
        ok, reason, title = _cf.verify_url_has_content(
            clean_url,
            max_age_seconds=0,
            timeout_seconds=_FETCH_TIMEOUT_S,
            max_redirects=_FETCH_MAX_REDIRECTS,
        )
    except Exception:
        # Fetch raised unexpectedly — uniform-shape failure so the UI keeps
        # working. RECON-gated since this is a security-adjacent signal.
        _emit_recon("network_error", host=host_ascii)
        ok, reason, title = False, "network_error", None
    finally:
        throttle.release(host_ascii)

    if not ok and reason == "ssrf_blocked":
        _emit_recon("ssrf_blocked", host=host_ascii)

    return _uniform(bool(ok), reason or "ok", title or "")
