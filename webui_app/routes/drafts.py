"""/ce:draft/* — Plan Unit 3."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from flask import Blueprint, redirect, request, session

from backlink_publisher._util.logger import plan_logger
from webui_store import drafts_store as _drafts_store

from ..helpers.contexts import _calc_next_available
from ..scheduler import _schedule_draft_job, _scheduler

bp = Blueprint("drafts", __name__)


def _remove_scheduled_job(job_id: str) -> bool:
    """Remove a scheduler job, distinguishing benign absence from real failure.

    Returns True when removal was clean (job removed, or the job never existed —
    the expected state for a draft that was never scheduled). Returns False when
    removal genuinely failed (the job may still fire); logs the real cause.
    """
    try:
        _scheduler.remove_job(job_id)
    except JobLookupError:
        # Draft was never scheduled — nothing to remove. Benign.
        return True
    except Exception as exc:
        plan_logger.warn("draft_job_remove_failed", item_id=job_id,
                         reason=type(exc).__name__)
        return False
    return True


@bp.route('/ce:draft/save', methods=['POST'])
def ce_draft_save():
    """Save current validated plans as a draft queue item."""
    plans_jsonl = request.form.get('plans', '').strip()
    if not plans_jsonl:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=没有可保存的内容')
    config = session.get('config', {})
    platform = request.form.get('platform', config.get('platform', 'blogger'))
    publish_mode = request.form.get('publish_mode', 'publish')
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

    _schedule_draft_job(item_id, final_dt)
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
    _schedule_draft_job(item_id, run_date)
    return redirect('/?tab=draft&flash_type=info&flash_msg=正在发布，请稍候刷新页面')


@bp.route('/ce:draft/cancel', methods=['POST'])
def ce_draft_cancel():
    """Cancel a scheduled draft job."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    removed = _remove_scheduled_job(item_id)
    _drafts_store.update_item(item_id, status='pending', scheduled_at=None)
    if not removed:
        return redirect('/?tab=draft&flash_type=warning'
                        '&flash_msg=已取消排程，但排程任务可能仍会触发')
    return redirect('/?tab=draft&flash_type=success&flash_msg=已取消排程')


@bp.route('/ce:draft/delete', methods=['POST'])
def ce_draft_delete():
    """Delete a draft item (cancel job if scheduled)."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/?tab=draft&flash_type=danger&flash_msg=参数缺失')
    removed = _remove_scheduled_job(item_id)
    _drafts_store.delete_item(item_id)
    if not removed:
        return redirect('/?tab=draft&flash_type=warning'
                        '&flash_msg=已删除，但排程任务可能仍会触发')
    return redirect('/?tab=draft&flash_type=success&flash_msg=已删除')


# ──────────────────────────────────────────────────────────────────────────
# Bulk operations — Plan 2026-05-19-006 Unit 3
# ──────────────────────────────────────────────────────────────────────────


@bp.route('/ce:draft/bulk-delete', methods=['POST'])
def ce_draft_bulk_delete():
    """Delete multiple drafts by id. Form: ids=<id1>&ids=<id2>..."""
    ids = request.form.getlist('ids')
    if not ids:
        return redirect('/?tab=draft&flash_type=warning&flash_msg=未选择任何项')
    job_failures = sum(not _remove_scheduled_job(item_id) for item_id in ids)
    removed = _drafts_store.bulk_delete(ids)
    if job_failures:
        return redirect(
            f'/?tab=draft&flash_type=warning'
            f'&flash_msg=已删除 {removed} 项，其中 {job_failures} 项的排程任务可能仍会触发'
        )
    return redirect(
        f'/?tab=draft&flash_type=success&flash_msg=已删除 {removed} 项'
    )


@bp.route('/ce:draft/bulk-publish-now', methods=['POST'])
def ce_draft_bulk_publish_now():
    """Schedule multiple drafts for near-immediate publish, staggered by 5s."""
    ids = request.form.getlist('ids')
    if not ids:
        return redirect('/?tab=draft&flash_type=warning&flash_msg=未选择任何项')
    from ..scheduler import _publish_draft_job, _scheduler
    base = datetime.now()
    scheduled = 0
    for i, item_id in enumerate(ids):
        if not _drafts_store.get_item(item_id):
            continue
        run_date = base + timedelta(seconds=5 + i * 5)
        _drafts_store.update_item(
            item_id, status='scheduled', scheduled_at=run_date.isoformat()
        )
        _scheduler.add_job(
            _publish_draft_job, trigger='date', run_date=run_date,
            id=item_id, args=[item_id], replace_existing=True,
        )
        scheduled += 1
    return redirect(
        f'/?tab=draft&flash_type=info&flash_msg=正在批量发布 {scheduled} 项，请稍候刷新页面'
    )


@bp.route('/ce:draft/bulk-cancel', methods=['POST'])
def ce_draft_bulk_cancel():
    """Cancel scheduling for multiple drafts (revert to pending)."""
    ids = request.form.getlist('ids')
    if not ids:
        return redirect('/?tab=draft&flash_type=warning&flash_msg=未选择任何项')
    cancelled = 0
    job_failures = 0
    for item_id in ids:
        item = _drafts_store.get_item(item_id)
        if not item or item.get('status') != 'scheduled':
            continue
        if not _remove_scheduled_job(item_id):
            job_failures += 1
        _drafts_store.update_item(item_id, status='pending', scheduled_at=None)
        cancelled += 1
    if job_failures:
        return redirect(
            f'/?tab=draft&flash_type=warning'
            f'&flash_msg=已取消 {cancelled} 项排程，其中 {job_failures} 项的排程任务可能仍会触发'
        )
    return redirect(
        f'/?tab=draft&flash_type=success&flash_msg=已取消 {cancelled} 项排程'
    )
