"""Blogger OAuth flows.

Medium OAuth registration has been closed since 2023-03-02 (Medium API archived).
The oauth-start and oauth-callback routes have been removed. Existing
medium-token.json files remain valid; the /settings/clear-medium-oauth route
is kept so legacy users can revoke their token via the UI.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlparse

from flask import Blueprint, redirect, request, session

from backlink_publisher.config import load_config, save_config

from ..helpers.security import _oauth_callback_uri, _safe_flash_redirect

bp = Blueprint("oauth", __name__)


# Plan 2026-05-21-006 Unit 3.2 — `OAUTHLIB_INSECURE_TRANSPORT=1` allows
# Google's oauthlib to accept http (not just https) callback URIs, needed
# because the WebUI binds to loopback http. The previous implementation
# mutated os.environ permanently in two request handlers, leaving the
# variable set for every subsequent OAuth-using code path in this process
# (and any subprocess that inherits the env).
#
# This context manager:
#   1. Asserts the callback URI is loopback BEFORE enabling insecure transport.
#      Off-loopback OAuth must use https; refusing to enable the bypass
#      keeps an off-loopback deployment from silently downgrading TLS.
#   2. Sets the env var only for the duration of the block.
#   3. Restores the prior value (or unsets it) on exit, even on exception.
_OAUTH_ENV_VAR = "OAUTHLIB_INSECURE_TRANSPORT"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_uri(uri: str) -> bool:
    try:
        host = urlparse(uri).hostname
    except Exception:
        return False
    return (host or "").lower() in _LOOPBACK_HOSTS


@contextmanager
def _oauthlib_insecure_transport(callback_uri: str):
    """Scope OAUTHLIB_INSECURE_TRANSPORT to a single OAuth handler.

    Refuses to enable the bypass when callback_uri is not a loopback host —
    that situation requires real TLS and the bypass would be a downgrade.
    """
    if not _is_loopback_uri(callback_uri):
        raise RuntimeError(
            f"refusing to enable OAUTHLIB_INSECURE_TRANSPORT: "
            f"callback URI {callback_uri!r} is not loopback. "
            f"Off-loopback OAuth must use https without the bypass."
        )
    prev = os.environ.get(_OAUTH_ENV_VAR)
    os.environ[_OAUTH_ENV_VAR] = "1"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_OAUTH_ENV_VAR, None)
        else:
            os.environ[_OAUTH_ENV_VAR] = prev


# ── Medium OAuth ────────────────────────────────────────────────────────────
# Note: /settings/medium/oauth-start and /settings/medium/oauth-callback
# have been removed — Medium closed new app registration on 2023-03-02.
# Users can still revoke an existing stored token via the route below.


@bp.route('/settings/clear-medium-oauth', methods=['POST'])
def settings_clear_medium_oauth():
    """Clear Medium OAuth token file."""
    try:
        from backlink_publisher.config import _config_dir
        token_file = _config_dir() / "medium-token.json"
        if token_file.exists():
            os.remove(token_file)
        return _safe_flash_redirect(
            '/settings', flash_type='success',
            msg='Medium OAuth 授权已清除', fragment='channel-medium')
    except Exception as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'清除失败: {e}', fragment='channel-medium')


# ── Blogger OAuth ───────────────────────────────────────────────────────────


@bp.route('/settings/save-blogger-oauth', methods=['POST'])
def settings_save_blogger_oauth():
    """Save Client ID / Secret only — no OAuth redirect."""
    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    cfg_existing = load_config()
    # P3: blank client_secret preserves the stored value (template no longer
    # round-trips the secret in HTML — see _settings_channel_blogger.html).
    if not client_secret and cfg_existing.blogger_oauth:
        client_secret = cfg_existing.blogger_oauth.client_secret or ''
    if not client_id or not client_secret:
        return _safe_flash_redirect(
            '/settings', flash_type='warning',
            msg='请填写 Client ID 和 Client Secret',
            fragment='channel-blogger')
    try:
        save_config(cfg_existing,
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
        return _safe_flash_redirect(
            '/settings', flash_type='success',
            msg='凭据已确认绑定，可随时点击「使用 Google 帐号登入」完成授权',
            fragment='channel-blogger')
    except Exception as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'保存失败: {e}', fragment='channel-blogger')


@bp.route('/settings/blogger/oauth-start', methods=['POST'])
def settings_blogger_oauth_start():
    """Save credentials, generate Google auth URL, redirect user's browser there."""
    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    cfg_existing = load_config()
    # P3: blank client_secret preserves stored (see _settings_channel_blogger.html).
    if not client_secret and cfg_existing.blogger_oauth:
        client_secret = cfg_existing.blogger_oauth.client_secret or ''

    if not client_id or not client_secret:
        return _safe_flash_redirect(
            '/settings', flash_type='warning',
            msg='请填写 Client ID 和 Client Secret 后再登入',
            fragment='channel-blogger')

    try:
        save_config(cfg_existing,
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
    except Exception as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'凭据保存失败: {e}', fragment='channel-blogger')

    cb_uri = _oauth_callback_uri()
    # Plan 2026-05-21-006 Unit 3.2 — scope OAUTHLIB_INSECURE_TRANSPORT to
    # this handler only (was a permanent os.environ mutation).
    try:
        with _oauthlib_insecure_transport(cb_uri):
            from google_auth_oauthlib.flow import Flow
            from backlink_publisher.publishing.adapters.blogger_api import _SCOPES

            client_config = {
                'installed': {
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'redirect_uris': ['http://localhost', cb_uri],
                    'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                    'token_uri': 'https://oauth2.googleapis.com/token',
                }
            }

            flow = Flow.from_client_config(client_config, scopes=_SCOPES,
                                           redirect_uri=cb_uri)
            auth_url, state = flow.authorization_url(
                access_type='offline', prompt='consent')

            session['oauth_state'] = state
            session['oauth_client_config'] = client_config
            session['oauth_code_verifier'] = getattr(flow, 'code_verifier', None)
        return redirect(auth_url)
    except RuntimeError as e:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'OAuth 启动失败: {e}', fragment='channel-blogger')


@bp.route('/settings/blogger/oauth-callback')
def settings_blogger_oauth_callback():
    """Google redirects here after the user approves."""
    err = request.args.get('error')
    if err:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'Google 拒绝授权: {err}', fragment='channel-blogger')

    state = session.get('oauth_state')
    client_config = session.get('oauth_client_config')
    if not state or not client_config:
        return _safe_flash_redirect(
            '/settings', flash_type='warning',
            msg='授权会话已过期，请重新点击登入按钮',
            fragment='channel-blogger')

    # Defense-in-depth OAuth-CSRF check: the returned ``state`` must match the
    # value stored at oauth-start. google-auth-oauthlib also validates state
    # inside fetch_token, but asserting it explicitly here makes the security
    # property legible/testable and reports a mismatch as a clear auth failure
    # rather than a generic token-exchange error.
    if request.args.get('state') != state:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg='OAuth state 校验失败，疑似跨站请求，请重新点击登入按钮',
            fragment='channel-blogger')

    # Transport-security gate, checked explicitly *before* the try block.
    # Previously this relied on catching ``RuntimeError`` raised by
    # ``_oauthlib_insecure_transport`` — but ``fetch_token`` (and the helpers
    # below) can raise ``RuntimeError`` too, so a token-exchange failure was
    # mislabeled as a transport-security failure. Checking the URI here keeps
    # the discriminator precise and lets genuine errors hit the generic branch.
    cb_uri = _oauth_callback_uri()
    if not _is_loopback_uri(cb_uri):
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'OAuth 回调传输安全检查失败: 回调地址 {cb_uri} 非 loopback，'
                f'需使用 https 且不启用不安全传输旁路',
            fragment='channel-blogger')
    try:
        from google_auth_oauthlib.flow import Flow
        from backlink_publisher.publishing.adapters.blogger_api import _SCOPES, json_from_creds
        from backlink_publisher.config import save_blogger_token

        # Plan 2026-05-21-006 Unit 3.2 — same loopback-asserting context
        # manager wraps the token-exchange call that needs the bypass. The
        # loopback precondition is verified above, so the gate never raises here.
        with _oauthlib_insecure_transport(cb_uri):
            flow = Flow.from_client_config(client_config, scopes=_SCOPES,
                                           redirect_uri=cb_uri, state=state)
            code_verifier = session.get('oauth_code_verifier')
            if code_verifier:
                flow.code_verifier = code_verifier

            flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials

        cfg = load_config()
        cfg.blogger_token_path.parent.mkdir(parents=True, exist_ok=True)
        save_blogger_token(json_from_creds(creds), cfg.blogger_token_path)
        session.pop('oauth_state', None)
        session.pop('oauth_client_config', None)
        session.pop('oauth_code_verifier', None)  # PKCE verifier — clear it too
        return _safe_flash_redirect(
            '/settings', flash_type='success',
            msg='Google 帐号授权成功！Token 已保存。',
            fragment='channel-blogger')
    except Exception as exc:
        return _safe_flash_redirect(
            '/settings', flash_type='danger',
            msg=f'授权处理失败: {exc}', fragment='channel-blogger')
