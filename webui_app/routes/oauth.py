"""Blogger OAuth flows.

Medium OAuth registration has been closed since 2023-03-02 (Medium API archived).
The oauth-start and oauth-callback routes have been removed. Existing
medium-token.json files remain valid; the /settings/clear-medium-oauth route
is kept so legacy users can revoke their token via the UI.
"""

from __future__ import annotations

import os

from flask import Blueprint, redirect, request, session

from backlink_publisher.config import load_config, save_config

from ..helpers import _oauth_callback_uri

bp = Blueprint("oauth", __name__)


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
        return redirect('/settings?flash_type=success&flash_msg=Medium OAuth 授权已清除#channel-medium')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}#channel-medium')


# ── Blogger OAuth ───────────────────────────────────────────────────────────


@bp.route('/settings/save-blogger-oauth', methods=['POST'])
def settings_save_blogger_oauth():
    """Save Client ID / Secret only — no OAuth redirect."""
    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()
    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg=请填写 Client ID 和 Client Secret#channel-blogger')
    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
        return redirect('/settings?flash_type=success&flash_msg=凭据已确认绑定，可随时点击「使用 Google 帐号登入」完成授权#channel-blogger')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}#channel-blogger')


@bp.route('/settings/blogger/oauth-start', methods=['POST'])
def settings_blogger_oauth_start():
    """Save credentials, generate Google auth URL, redirect user's browser there."""
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    client_id = request.form.get('client_id', '').strip()
    client_secret = request.form.get('client_secret', '').strip()

    if not client_id or not client_secret:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '请填写 Client ID 和 Client Secret 后再登入#channel-blogger')

    try:
        save_config(load_config(),
                    blogger_client_id=client_id,
                    blogger_client_secret=client_secret,
                    target_three_url=None)
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=凭据保存失败: {e}#channel-blogger')

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
        return redirect(f'/settings?flash_type=danger&flash_msg=Google 拒绝授权: {err}#channel-blogger')

    state = session.get('oauth_state')
    client_config = session.get('oauth_client_config')
    if not state or not client_config:
        return redirect('/settings?flash_type=warning&flash_msg='
                        + '授权会话已过期，请重新点击登入按钮#channel-blogger')

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
                        + 'Google 帐号授权成功！Token 已保存。#channel-blogger')
    except Exception as exc:
        return redirect(f'/settings?flash_type=danger&flash_msg=授权处理失败: {exc}#channel-blogger')
