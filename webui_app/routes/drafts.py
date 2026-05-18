"""/ce:draft/* — Plan Unit 3."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask import Blueprint, redirect, request, session

from webui_store import drafts_store as _drafts_store

from ..helpers import _calc_next_available

bp = Blueprint("drafts", __name__)


@bp.route('/ce:draft/save', methods=['POST'])
def ce_draft_save():
    """Save current validated plans as a draft queue item."""
    plans_jsonl = request.form.get('plans', '').strip()
    if not plans_jsonl:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=没有可保存的内容')
    config = session.get('config', {})
    platform = request.form.get('platform', config.get('platform', 'medium'))
    publish_mode = request.form.get('publish_mode', 'draft')
    target_url = config.get('target_url', request.form.get('target_url', 'unknown'))
    language = config.get('target_language', 'zh-CN')

    item = {
        'id': str(uuid.uuid4())[:8],
        'target_url': target_url,
        'platform': platform,
        'language': language,
        'publish_mode': publish_mode,
        'plans_jsonl': plans_jsonl,
        'status': 'pending',
        'scheduled_at': None,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'article_urls': [],
        'error': None,
    }
    _drafts_store.insert_first(item)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已加入草稿栏')


@bp.route('/ce:draft/schedule', methods=['POST'])
def ce_draft_schedule():
    """Schedule a draft item for publishing at a given datetime."""
    item_id = request.form.get('id', '')
    scheduled_at_str = request.form.get('scheduled_at', '')
    if not item_id or not scheduled_at_str:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')

    try:
        requested_dt = datetime.fromisoformat(scheduled_at_str)
    except ValueError:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=时间格式错误')

    final_dt = _calc_next_available(requested_dt)
    _drafts_store.update_item(item_id, status='scheduled',
                              scheduled_at=final_dt.isoformat())

    from ..scheduler import _publish_draft_job, _scheduler
    _scheduler.add_job(
        _publish_draft_job, trigger='date', run_date=final_dt,
        id=item_id, args=[item_id], replace_existing=True,
    )
    adjusted = final_dt != requested_dt
    msg = f'已排程：{final_dt.strftime("%Y-%m-%d %H:%M")}'
    if adjusted:
        msg += '（已依间隔设定自动调整）'
    return redirect(f'/?tab=draft&flash_type=success&flash_msg={msg}')


@bp.route('/ce:draft/publish-now', methods=['POST'])
def ce_draft_publish_now():
    """Immediately schedule a draft item to publish in ~5 seconds."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    run_date = datetime.now() + timedelta(seconds=5)
    _drafts_store.update_item(item_id, status='scheduled',
                              scheduled_at=run_date.isoformat())
    from ..scheduler import _publish_draft_job, _scheduler
    _scheduler.add_job(
        _publish_draft_job, trigger='date', run_date=run_date,
        id=item_id, args=[item_id], replace_existing=True,
    )
    return redirect('/?tab=draft&flash_type=info&flash_msg=正在发布，请稍候刷新页面')


@bp.route('/ce:draft/cancel', methods=['POST'])
def ce_draft_cancel():
    """Cancel a scheduled draft job."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    try:
        from ..scheduler import _scheduler
        _scheduler.remove_job(item_id)
    except Exception:
        pass
    _drafts_store.update_item(item_id, status='pending', scheduled_at=None)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已取消排程')


@bp.route('/ce:draft/delete', methods=['POST'])
def ce_draft_delete():
    """Delete a draft item (cancel job if scheduled)."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    try:
        from ..scheduler import _scheduler
        _scheduler.remove_job(item_id)
    except Exception:
        pass
    _drafts_store.delete_item(item_id)
    return redirect('/?tab=draft&flash_type=success&flash_msg=已删除')
