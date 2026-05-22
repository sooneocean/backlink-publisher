"""Channel and feature status probes for the WebUI settings page.

Extracted from helpers/contexts.py — these are pure status-read helpers
called by _settings_context() and _render().
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import session
from google.oauth2.credentials import Credentials

from backlink_publisher.config import load_blogger_token, load_config
from backlink_publisher.publishing.adapters.medium_browser import (
    sync_playwright as _spw,
)
from backlink_publisher.publishing.adapters.velog_graphql import (
    _effective_cap,
    _read_count,
)


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
    """Return velog channel status for the WebUI badge (6 states)."""
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

        if not cookies_path.exists():
            return {
                'state': 'err',
                'label': '未绑定',
                'guide': '运行: velog-login',
                'cookies_path': str(cookies_path),
                'count': 0,
                'cap': cap,
            }

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

def _get_medium_browser_status(cfg, *, session=None) -> dict:
    """Return a dict describing the Medium browser fallback readiness.

    Reads only the filesystem and Python import state — no Playwright launch,
    no network call.  ``logged_in`` state is set only via flask.session after
    a successful probe_login_status() invocation.
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

