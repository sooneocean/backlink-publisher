"""/settings (GET) + /settings/save-* (non-OAuth) — Plan Unit 3.

Plan 2026-05-19-006 Unit 4 — adds generic /api/<channel>/{status,verify,dry-run}
routes consumed by the dashboard JS in Unit 5.
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, redirect, render_template, request

from backlink_publisher.config import load_config, save_config
from backlink_publisher.publishing.adapters import verify_adapter_setup
from backlink_publisher.publishing.registry import registered_platforms

from ..binding_status import get_channel_status
from ..helpers import _check_csrf_or_abort, _save_schedule_settings, _settings_context

bp = Blueprint("settings_basic", __name__)


@bp.route('/settings')
def settings():
    flash_type = request.args.get('flash_type')
    flash_msg = request.args.get('flash_msg')
    flash = {"type": flash_type, "msg": flash_msg} if flash_type else None
    return render_template('settings.html', **_settings_context(flash=flash))


# ── Generic channel binding API (Plan 2026-05-19-006 Unit 4) ─────────────────


def _verify_result_to_json(result) -> dict:
    """Serialize VerifyResult dataclass → plain dict for JSON response."""
    return {
        "ok": result.ok,
        "identity": result.identity,
        "last_verified_at": result.last_verified_at,
        "last_verify_result": result.last_verify_result,
        "blockers": list(result.blockers),
        "dofollow": result.dofollow,
    }


def _require_known_channel(channel: str) -> None:
    """404 for any platform not in the dynamic registry. Drift between this
    route and dashboard cards is enforced by ``tests/test_dashboard_drift``.
    """
    if channel not in registered_platforms():
        abort(404)


@bp.route('/api/<channel>/status', methods=['GET'])
def api_channel_status(channel: str):
    """Cheap offline status — config presence, no network call."""
    _require_known_channel(channel)
    config = load_config()
    return jsonify(get_channel_status(channel, config))


@bp.route('/api/<channel>/verify', methods=['POST'])
def api_channel_verify(channel: str):
    """Live verify — calls platform's lightweight verify endpoint.

    CSRF guarded. Per-channel live impl deferred to Unit 6 backfill — Unit 4
    ships the dispatch + JSON contract.
    """
    _check_csrf_or_abort()
    _require_known_channel(channel)
    config = load_config()
    result = verify_adapter_setup(channel, config, mode='live')
    return jsonify(_verify_result_to_json(result))


@bp.route('/api/<channel>/dry-run', methods=['POST'])
def api_channel_dry_run(channel: str):
    """Dry-run — builds payload but ZERO real HTTP (defense-in-depth via
    Session.send monkey-patch in publishing._verify.dry_run_intercept)."""
    _check_csrf_or_abort()
    _require_known_channel(channel)
    config = load_config()
    payload = request.get_json(silent=True) or {}
    result = verify_adapter_setup(channel, config, mode='dry-run', payload=payload)
    return jsonify(_verify_result_to_json(result))


@bp.route('/settings/save-target-keywords', methods=['POST'])
def settings_save_target_keywords():
    """Save SEO anchor keyword pools for all target domains."""
    try:
        count = int(request.form.get('domain_count', 0))
        new_pools: dict[str, list[str]] = {}
        dup_warnings: list[str] = []

        for i in range(1, count + 1):
            domain = request.form.get(f'domain_{i}', '').strip()
            raw = request.form.get(f'keywords_{i}', '')
            if not domain:
                continue

            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            invalid = [ln for ln in lines if len(ln) > 60]
            if invalid:
                return redirect(
                    f'/settings?flash_type=danger&flash_msg='
                    f'关键词过长（>60字符）: {invalid[0][:30]}…'
                )

            seen: set[str] = set()
            deduped: list[str] = []
            for kw in lines:
                if kw in seen:
                    dup_warnings.append(domain)
                else:
                    seen.add(kw)
                    deduped.append(kw)

            new_pools[domain] = deduped

        save_config(load_config(), target_anchor_keywords=new_pools,
                    target_three_url=None)
        msg = '关键词已保存'
        if dup_warnings:
            msg += f'（已自动去重 {len(set(dup_warnings))} 个域名）'
        return redirect(f'/settings?flash_type=success&flash_msg={msg}')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@bp.route('/settings/schedule', methods=['POST'])
def settings_schedule_save():
    """Save schedule interval settings."""
    try:
        min_hours = float(request.form.get('min_interval_hours', 4))
        jitter_mins = int(request.form.get('jitter_minutes', 30))
        _save_schedule_settings({
            'min_interval_hours': max(0.5, min_hours),
            'jitter_minutes': max(0, jitter_mins),
        })
        return redirect('/settings?flash_type=success&flash_msg=排程设定已保存')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}')


@bp.route('/settings/save-blog-ids', methods=['POST'])
def settings_save_blog_ids():
    domains = request.form.getlist('domain[]')
    blog_ids_list = request.form.getlist('blog_id[]')
    mapping = {d.strip(): b.strip() for d, b in zip(domains, blog_ids_list)
               if d.strip() and b.strip()}
    try:
        cfg = load_config()
        cfg.blogger_blog_ids = mapping
        save_config(cfg, extra_blogger_ids={}, target_three_url=None)
        return redirect('/settings?flash_type=success&flash_msg=Blog ID 映射已保存#channel-blogger')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}#channel-blogger')


@bp.route('/settings/save-medium-token', methods=['POST'])
def settings_save_medium_token():
    token = request.form.get('medium_token', '').strip()
    try:
        save_config(load_config(), medium_token=token, target_three_url=None)
        msg = 'Medium Token 已保存' if token else 'Medium Token 已清除'
        return redirect(f'/settings?flash_type=success&flash_msg={msg}#channel-medium')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=保存失败: {e}#channel-medium')


@bp.route('/settings/clear-medium-token', methods=['POST'])
def settings_clear_medium_token():
    try:
        save_config(load_config(), medium_token="", target_three_url=None)
        return redirect('/settings?flash_type=success&flash_msg=Medium Token 已清除#channel-medium')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=清除失败: {e}#channel-medium')


@bp.route('/settings/revoke-blogger', methods=['POST'])
def settings_revoke_blogger():
    cfg = load_config()
    try:
        cfg.blogger_token_path.unlink(missing_ok=True)
        return redirect('/settings?flash_type=success&flash_msg=Blogger 授权已撤销#channel-blogger')
    except Exception as e:
        return redirect(f'/settings?flash_type=danger&flash_msg=撤销失败: {e}#channel-blogger')
