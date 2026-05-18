"""Medium + Blogger OAuth flows — Plan Unit 3."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

from flask import Blueprint, redirect, request, session

from backlink_publisher.config import load_config, save_config

from ..helpers import _oauth_callback_uri

bp = Blueprint("oauth", __name__)


# ── Medium OAuth ────────────────────────────────────────────────────────────


@bp.route('/settings/medium/oauth-start', methods=['POST'])
def settings_medium_oauth_start():
    """Save credentials, generate Medium auth URL, redirect user's browser there."""
    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()

    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '请填写 Client ID 和 Client Secret')

    try:
        from backlink_publisher.config import MediumOAuthConfig
        cfg = load_config()
        cfg.medium_oauth = MediumOAuthConfig(
            client_id=client_id, client_secret=client_secret,
        )
        session['medium_client_id'] = client_id
        session['medium_client_secret'] = client_secret
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=凭据保存失败: {e}')

    state = secrets.token_urlsafe(32)
    session['medium_oauth_state'] = state

    redirect_uri = _oauth_callback_uri().replace(
        '/blogger/oauth-callback', '/medium/oauth-callback'
    )
    oauth_params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'state': state,
        'scope': 'basicProfile,publishPost',
    }
    auth_url = f"https://medium.com/m/oauth/authorize?{urlencode(oauth_params)}"
    return redirect(auth_url)


@bp.route('/settings/medium/oauth-callback')
def settings_medium_oauth_callback():
    """Medium redirects here after user approves."""
    import requests as req

    err = request.args.get('error')
    if err:
        SAFE_ERROR_MESSAGES = {
            'access_denied': '用户拒绝了授权',
            'invalid_scope': '请求的权限无效',
            'invalid_request': '授权请求参数有误',
            'server_error': 'Medium 服务器出错，请稍后重试',
            'temporarily_unavailable': 'Medium 服务暂时不可用，请稍后重试',
        }
        error_msg = SAFE_ERROR_MESSAGES.get(err, '授权失败，请重试')
        return redirect(f'/settings?flash_type=danger&flash_msg={error_msg}')

    state = session.get('medium_oauth_state')
    code = request.args.get('code')
    client_id = session.get('medium_client_id')
    client_secret = session.get('medium_client_secret')

    if not state or not code or not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '授权会话已过期，请重新点击授权按钮')

    if request.args.get('state') != state:
        return redirect('/settings?flash_type=danger&flash_msg='
                        + 'OAuth state 不匹配（可能是 CSRF 攻击）')

    redirect_uri = _oauth_callback_uri().replace(
        '/blogger/oauth-callback', '/medium/oauth-callback'
    )
    try:
        token_resp = req.post(
            "https://api.medium.com/v1/tokens",
            data={
                "code": code, "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=30,
        )
        if token_resp.status_code != 200:
            raise Exception(f"Token exchange failed with status {token_resp.status_code}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise Exception("Missing access_token in Medium response")

        if "expires_in" in token_data and "expires_at" not in token_data:
            token_data["expires_at"] = (
                int(datetime.now(timezone.utc).timestamp())
                + int(token_data["expires_in"])
            )

        from backlink_publisher.config import (
            MediumOAuthConfig, save_medium_token,
        )
        save_medium_token(token_data)
        cfg = load_config()
        cfg.medium_oauth = MediumOAuthConfig(
            client_id=client_id, client_secret=client_secret,
        )
        save_config(cfg, target_three_url=None)

        session.pop('medium_oauth_state', None)
        session.pop('medium_client_id', None)
        session.pop('medium_client_secret', None)

        return redirect('/settings?flash_type=success&flash_msg=Medium OAuth 授权成功！')

    except Exception:
        return redirect('/settings?flash_type=danger&flash_msg=获取 Token 失败，请检查凭证并重试')


@bp.route('/settings/clear-medium-oauth', methods=['POST'])
def settings_clear_medium_oauth():
    """Clear Medium OAuth configuration."""
    try:
        from backlink_publisher.config import _config_dir
        token_file = _config_dir() / "medium-token.json"
        if token_file.exists():
            os.remove(token_file)
        return redirect('/settings?flash_type=success&flash_msg=Medium OAuth 授权已清除')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}')


# ── Blogger OAuth ───────────────────────────────────────────────────────────


@bp.route('/settings/save-blogger-oauth', methods=['POST'])
def settings_save_blogger_oauth():
    """Save Client ID / Secret only — no OAuth redirect."""
    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg=请填写 Client ID 和 Client Secret')
    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
        return redirect('/settings?flash_type=success&flash_msg=凭据已确认绑定，可随时点击「使用 Google 帐号登入」完成授权')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@bp.route('/settings/blogger/oauth-start', methods=['POST'])
def settings_blogger_oauth_start():
    """Save credentials, generate Google auth URL, redirect user's browser there."""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()

    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '请填写 Client ID 和 Client Secret 后再登入')

    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=凭据保存失败: {e}')

    from google_auth_oauthlib.flow import Flow
    from backlink_publisher.adapters.blogger_api import _SCOPES

    cb_uri = _oauth_callback_uri()
    client_config = {
        'installed': {
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uris': ['http://localhost', cb_uri],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
        }
    }

    flow = Flow.from_client_config(client_config, scopes=_SCOPES, redirect_uri=cb_uri)
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')

    session['oauth_state'] = state
    session['oauth_client_config'] = client_config
    session['oauth_code_verifier'] = getattr(flow, 'code_verifier', None)
    return redirect(auth_url)


@bp.route('/settings/blogger/oauth-callback')
def settings_blogger_oauth_callback():
    """Google redirects here after the user approves."""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    err = request.args.get('error')
    if err:
        return redirect(f'/settings?flash_type=danger&flash_msg=Google 拒绝授权: {err}')

    state = session.get('oauth_state')
    client_config = session.get('oauth_client_config')
    if not state or not client_config:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '授权会话已过期，请重新点击登入按钮')

    from google_auth_oauthlib.flow import Flow
    from backlink_publisher.adapters.blogger_api import _SCOPES, json_from_creds
    from backlink_publisher.config import save_blogger_token

    cb_uri = _oauth_callback_uri()
    try:
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
        return redirect('/settings?flash_type=success&flash_msg='
                        + 'Google 帐号授权成功！Token 已保存。')
    except Exception as exc:
        return redirect(f'/settings?flash_type=danger&flash_msg=授权处理失败: {exc}')
