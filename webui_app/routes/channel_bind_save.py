"""Registry-driven credential save route — Plan 2026-05-26-002 Unit 4.

Single POST endpoint dispatching channel→auth-type→saver.  Handles TOKEN,
TOKEN+FIELDS, PASTE-BLOB, USERPASS, and ANON auth types.

Security guarantees
-------------------
* ``_refuse_when_allow_network()`` — hard-disabled when not on loopback.
* ``_check_bind_origin_or_abort()`` — Origin/Referer must be loopback.
* CSRF is enforced globally by ``_global_csrf_guard`` in ``create_app()``;
  no duplicate check here.
* Secrets never appear in flash messages (``_safe_flash_redirect`` sanitises).
* SSRF: URL fields (site, site_url) are validated via ``_check_url_for_ssrf``
  and must use https.
* Paste-blob: size-capped, JSON-schema checked, domain-validated per channel.
* Credential files written via ``atomic_write_json`` 0600; userpass via the
  adapter's own ``store_credentials`` (preserves livejournal md5 vs cnblogs
  plaintext divergence — dispatch by module, not bare symbol import).

Channels devto / ghpages / notion keep their existing routes in
``token_paste.py``; this route ignores them to avoid conflicts.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

from flask import Blueprint, request

from backlink_publisher.config import load_config
from backlink_publisher._util.io import atomic_write_json
from backlink_publisher._util.net_safety import _check_url_for_ssrf
from backlink_publisher.config.tokens import (
    save_wordpresscom_token,
    save_writeas_token,
)
from backlink_publisher.publishing.registry import auth_type as _registry_auth_type

from ..helpers.security import (
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
    _safe_flash_redirect,
)

bp = Blueprint("channel_bind_save", __name__)

# Channels with dedicated existing routes — never handled here.
_SKIP_CHANNELS: frozenset[str] = frozenset({"devto", "ghpages", "notion"})

# TOKEN — single secret field.  (channel, token_key) → saver fn + basename.
_TOKEN_DISPATCH: dict[str, tuple] = {
    "writeas": (save_writeas_token, "writeas-token.json", "token"),
}

# TOKEN+FIELDS — secret + extra config fields; all stored in token JSON file.
# (channel) → (saver_fn, basename, [field_names])
# URL fields listed here are SSRF-validated (must be https, non-private).
_TOKEN_FIELDS_DISPATCH: dict[str, tuple] = {
    "wordpresscom": (save_wordpresscom_token, "wordpresscom-token.json",
                     ["token", "site"]),
}
_URL_FIELDS: frozenset[str] = frozenset({"site", "site_url"})

# PASTE-BLOB — pasted {"cookies":[...]} JSON; written as <channel>-credentials.json.
# Each entry maps channel → (basename, expected_domain_suffix).
# Domain suffix is checked against at least one cookie's domain field (advisory
# only — warn, not reject — because some channels use multiple subdomains).
_PASTE_BLOB_CHANNELS: dict[str, tuple[str, str]] = {
    "csdn":          ("csdn-credentials.json",          "csdn.net"),
    "habr":          ("habr-credentials.json",           "habr.com"),
    "jianshu":       ("jianshu-credentials.json",        "jianshu.com"),
    "juejin":        ("juejin-credentials.json",         "juejin.cn"),
    "note":          ("note-credentials.json",           "note.com"),
    "pikabu":        ("pikabu-credentials.json",         "pikabu"),
    "segmentfault":  ("segmentfault-credentials.json",   "segmentfault.com"),
    "substack":      ("substack-credentials.json",       "substack.com"),
    "zhihu":         ("zhihu-credentials.json",          "zhihu.com"),
}
_PASTE_BLOB_MAX_BYTES = 100_000

# USERPASS — module path for dispatch; call module.store_credentials(config, u, p).
_USERPASS_MODULES: dict[str, str] = {
    "livejournal": "backlink_publisher.publishing.adapters.livejournal_api",
    "cnblogs":     "backlink_publisher.publishing.adapters.cnblogs_api",
}


@bp.route("/settings/save-channel-credential", methods=["POST"])
def save_channel_credential():
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()

    channel = (request.form.get("channel", "") or "").strip()
    auth_type = (request.form.get("auth_type", "") or "").strip()

    if not channel:
        return _safe_flash_redirect("/settings", flash_type="danger",
                                    msg="channel 参数缺失")

    if channel in _SKIP_CHANNELS:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} 使用专用保存路由，不经此接口",
            fragment=f"channel-{channel}",
        )

    # Verify channel is registered and auth_type agrees with registry.
    registry_at = _registry_auth_type(channel)
    if registry_at is None:
        return _safe_flash_redirect("/settings", flash_type="danger",
                                    msg=f"未知渠道: {channel}")
    if auth_type and auth_type != registry_at:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} auth_type 不匹配（表单: {auth_type}, 注册表: {registry_at}）",
            fragment=f"channel-{channel}",
        )
    auth_type = registry_at

    is_clear = bool(request.form.get("clear"))

    if auth_type == "anon":
        return _save_anon(channel, is_clear)
    if auth_type == "token":
        return _save_token(channel, is_clear)
    if auth_type == "token_fields":
        return _save_token_fields(channel, is_clear)
    if auth_type == "paste_blob":
        return _save_paste_blob(channel, is_clear)
    if auth_type == "userpass":
        return _save_userpass(channel, is_clear)

    return _safe_flash_redirect(
        "/settings", flash_type="danger",
        msg=f"{channel} 的 auth_type={auth_type!r} 不支持通用保存路由",
        fragment=f"channel-{channel}",
    )


# ── auth-type handlers ────────────────────────────────────────────────────────


def _save_anon(channel: str, is_clear: bool):
    return _safe_flash_redirect(
        "/settings", flash_type="info",
        msg=f"{channel} 为匿名渠道，无需凭据",
        fragment=f"channel-{channel}",
    )


def _save_token(channel: str, is_clear: bool):
    if channel not in _TOKEN_DISPATCH:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} token 保存未实现（渠道可能已退役）",
            fragment=f"channel-{channel}",
        )
    save_fn, basename, field_key = _TOKEN_DISPATCH[channel]
    cfg = load_config()
    token_path = cfg.config_dir / basename
    frag = f"channel-{channel}"

    if is_clear:
        return _do_unlink(token_path, channel, frag)

    token = (request.form.get("token", "") or "").strip()
    if not token:
        return _safe_flash_redirect(
            "/settings", flash_type="info",
            msg=f"未填入 token，{channel} 配置未变更",
            fragment=frag,
        )
    try:
        save_fn({field_key: token})
    except Exception:
        _log.exception("save_token failed for channel=%s", channel)
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"保存 {channel} token 失败（详见服务器日志）",
            fragment=frag,
        )
    return _safe_flash_redirect(
        "/settings", flash_type="success",
        msg=f"{channel} token 已绑定 ✓",
        fragment=frag,
    )


def _save_token_fields(channel: str, is_clear: bool):
    if channel not in _TOKEN_FIELDS_DISPATCH:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} token_fields 保存未实现（渠道可能已退役或待实现）",
            fragment=f"channel-{channel}",
        )
    save_fn, basename, fields = _TOKEN_FIELDS_DISPATCH[channel]
    cfg = load_config()
    token_path = cfg.config_dir / basename
    frag = f"channel-{channel}"

    if is_clear:
        return _do_unlink(token_path, channel, frag)

    data: dict = {}
    for field_name in fields:
        val = (request.form.get(field_name, "") or "").strip()
        if val:
            data[field_name] = val

    if not data:
        return _safe_flash_redirect(
            "/settings", flash_type="info",
            msg=f"未填入任何字段，{channel} 配置未变更",
            fragment=frag,
        )

    # Validate URL fields against SSRF before any write.
    for field_name, val in data.items():
        if field_name in _URL_FIELDS:
            err = _validate_url_field(channel, field_name, val)
            if err:
                return err

    # Leave-as-is: merge with existing data for fields not submitted.
    existing: dict = {}
    if token_path.exists():
        try:
            existing = json.loads(token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    merged = {**existing, **data}

    try:
        save_fn(merged)
    except Exception:
        _log.exception("save_token_fields failed for channel=%s", channel)
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"保存 {channel} 凭据失败（详见服务器日志）",
            fragment=frag,
        )
    return _safe_flash_redirect(
        "/settings", flash_type="success",
        msg=f"{channel} 凭据已绑定 ✓",
        fragment=frag,
    )


def _save_paste_blob(channel: str, is_clear: bool):
    if channel not in _PASTE_BLOB_CHANNELS:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} paste_blob 保存未实现（渠道可能已退役）",
            fragment=f"channel-{channel}",
        )
    basename, expected_domain = _PASTE_BLOB_CHANNELS[channel]
    cfg = load_config()
    cred_path = cfg.config_dir / basename
    frag = f"channel-{channel}"

    if is_clear:
        return _do_unlink(cred_path, channel, frag)

    blob_raw = request.form.get("blob", "") or ""
    if not blob_raw.strip():
        return _safe_flash_redirect(
            "/settings", flash_type="info",
            msg=f"未填入 Cookie JSON，{channel} 配置未变更",
            fragment=frag,
        )

    if len(blob_raw.encode("utf-8")) > _PASTE_BLOB_MAX_BYTES:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"Cookie JSON 超过 {_PASTE_BLOB_MAX_BYTES // 1000}KB 限制",
            fragment=frag,
        )

    # Parse once — reused by validation and write.
    try:
        data = json.loads(blob_raw)
    except json.JSONDecodeError as exc:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"Cookie JSON 解析失败: {exc}",
            fragment=frag,
        )

    err = _validate_cookie_blob(data, expected_domain)
    if err:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=err,
            fragment=frag,
        )

    try:
        cred_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(cred_path, data, mode=0o600)
    except OSError:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"写入 {channel} cookie 文件失败（详见服务器日志）",
            fragment=frag,
        )
    return _safe_flash_redirect(
        "/settings", flash_type="success",
        msg=f"{channel} cookies 已绑定 ✓",
        fragment=frag,
    )


def _save_userpass(channel: str, is_clear: bool):
    if channel not in _USERPASS_MODULES:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} userpass 保存未实现（渠道可能已退役）",
            fragment=f"channel-{channel}",
        )
    frag = f"channel-{channel}"
    module_path = _USERPASS_MODULES[channel]

    if is_clear:
        cfg = load_config()
        cred_path = cfg.config_dir / f"{channel}-credentials.json"
        return _do_unlink(cred_path, channel, frag)

    username = (request.form.get("username", "") or "").strip()
    password = (request.form.get("password", "") or "").strip()

    if not username and not password:
        return _safe_flash_redirect(
            "/settings", flash_type="info",
            msg=f"未填入凭据，{channel} 配置未变更",
            fragment=frag,
        )
    if not username or not password:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} 用户名和密码必须同时填写",
            fragment=frag,
        )

    try:
        mod = importlib.import_module(module_path)
        cfg = load_config()
        mod.store_credentials(cfg, username, password)
    except Exception:
        _log.exception("save_userpass failed for channel=%s", channel)
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"保存 {channel} 凭据失败（详见服务器日志）",
            fragment=frag,
        )
    return _safe_flash_redirect(
        "/settings", flash_type="success",
        msg=f"{channel} 凭据已绑定 ✓",
        fragment=frag,
    )


# ── helpers ───────────────────────────────────────────────────────────────────


def _do_unlink(path: Path, channel: str, frag: str):
    try:
        if path.exists():
            path.unlink()
            return _safe_flash_redirect(
                "/settings", flash_type="success",
                msg=f"{channel} 凭据已清除",
                fragment=frag,
            )
        return _safe_flash_redirect(
            "/settings", flash_type="info",
            msg=f"{channel} 凭据文件不存在，无需清除",
            fragment=frag,
        )
    except OSError:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"清除 {channel} 凭据失败（详见服务器日志）",
            fragment=frag,
        )


def _validate_url_field(channel: str, field_name: str, val: str):
    if not val.startswith("https://"):
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} {field_name} 必须以 https:// 开头",
            fragment=f"channel-{channel}",
        )
    ssrf_err = _check_url_for_ssrf(val)
    if ssrf_err:
        return _safe_flash_redirect(
            "/settings", flash_type="danger",
            msg=f"{channel} {field_name} 地址被拒绝（安全校验）",
            fragment=f"channel-{channel}",
        )
    return None


def _validate_cookie_blob(data: object, expected_domain: str) -> str | None:
    """Return an error message string, or None if the blob looks valid."""
    if not isinstance(data, dict):
        return "Cookie JSON 必须是 JSON 对象（{...}）"

    cookies = data.get("cookies")
    if cookies is None:
        return 'Cookie JSON 缺少 "cookies" 键'
    if not isinstance(cookies, list):
        return '"cookies" 字段必须是数组'
    if len(cookies) == 0:
        return '"cookies" 数组不能为空'

    for i, c in enumerate(cookies):
        if not isinstance(c, dict):
            return f"cookies[{i}] 必须是对象"
        if "name" not in c:
            return f"cookies[{i}] 缺少 name 字段"
        if "value" not in c:
            return f"cookies[{i}] 缺少 value 字段"

    # Advisory domain check — at least one cookie's domain should match
    # the expected channel domain (warns operator if they pasted wrong site).
    if expected_domain:
        domains = [
            c.get("domain", "") for c in cookies if isinstance(c, dict)
        ]
        if not any(expected_domain in (d or "") for d in domains):
            return (
                f"Cookie 域名校验失败：未发现包含 {expected_domain!r} 的 domain。"
                "请确认是否导出了正确站点的 Cookie。"
            )

    return None
