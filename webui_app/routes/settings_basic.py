"""/settings (GET) + /settings/save-* (non-OAuth) — Plan Unit 3."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request

from backlink_publisher.config import load_config, save_config

from ..helpers import _save_schedule_settings, _settings_context

bp = Blueprint("settings_basic", __name__)


@bp.route('/settings')
def settings():
    flash_type = request.args.get('flash_type')
    flash_msg = request.args.get('flash_msg')
    flash = {"type": flash_type, "msg": flash_msg} if flash_type else None
    return render_template('settings.html', **_settings_context(flash=flash))


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
