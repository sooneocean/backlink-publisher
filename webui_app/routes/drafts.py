"""/ce:draft/* — Plan Unit 3.

Phase A refactoring: delegates to ``DraftAPI`` for all operations.
"""

from __future__ import annotations

from flask import Blueprint, redirect, request, session

from ..api import DraftAPI

bp = Blueprint("drafts", __name__)
_draft = DraftAPI()

# Scheduler-job-removal honesty lives in ``DraftAPI`` (api/drafts_api.py
# ``_remove_scheduled_job``); these routes just surface the result's
# ``flash_type`` ("warning" when a job may still fire).


@bp.route('/ce:draft/save', methods=['POST'])
def ce_draft_save():
    """Save current validated plans as a draft queue item."""
    plans_jsonl = request.form.get('plans', '').strip()
    config = session.get('config', {})
    platform = request.form.get('platform', config.get('platform', 'blogger'))
    target_url = config.get('target_url', request.form.get('target_url', 'unknown'))
    language = config.get('target_language', 'zh-CN')

    result = _draft.create(
        plans_jsonl, config,
        platform=platform,
        target_url=target_url,
        language=language,
    )
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "danger")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/schedule', methods=['POST'])
def ce_draft_schedule():
    """Schedule a draft item for publishing at a given datetime."""
    item_id = request.form.get('id', '')
    scheduled_at_str = request.form.get('scheduled_at', '')
    result = _draft.schedule(item_id, scheduled_at_str)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "danger")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/publish-now', methods=['POST'])
def ce_draft_publish_now():
    """Immediately schedule a draft item to publish in ~5 seconds."""
    item_id = request.form.get('id', '')
    result = _draft.publish_now(item_id)
    flash_type = result.get("flash_type") or ("info" if result["ok"] else "danger")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/ai-accept', methods=['POST'])
def ce_draft_ai_accept():
    """Accept an AI-reviewed draft so it can be published or scheduled."""
    item_id = request.form.get('id', '')
    result = _draft.accept_ai_review(item_id)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "warning")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/ai-fallback', methods=['POST'])
def ce_draft_ai_fallback():
    """Accept the fallback-safe draft path without rewriting article content."""
    item_id = request.form.get('id', '')
    result = _draft.fallback_ai_review(item_id)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "warning")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/cancel', methods=['POST'])
def ce_draft_cancel():
    """Cancel a scheduled draft job."""
    item_id = request.form.get('id', '')
    result = _draft.cancel(item_id)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "danger")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/delete', methods=['POST'])
def ce_draft_delete():
    """Delete a draft item (cancel job if scheduled)."""
    item_id = request.form.get('id', '')
    result = _draft.delete(item_id)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "danger")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


# ── Bulk operations —───────────────────────────────────────────────────────


@bp.route('/ce:draft/bulk-delete', methods=['POST'])
def ce_draft_bulk_delete():
    """Delete multiple drafts by id. Form: ids=<id1>&ids=<id2>..."""
    ids = request.form.getlist('ids')
    result = _draft.bulk_delete(ids)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "warning")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/bulk-publish-now', methods=['POST'])
def ce_draft_bulk_publish_now():
    """Schedule multiple drafts for near-immediate publish, staggered by 5s."""
    ids = request.form.getlist('ids')
    result = _draft.bulk_publish_now(ids)
    flash_type = result.get("flash_type") or ("info" if result["ok"] else "warning")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')


@bp.route('/ce:draft/bulk-cancel', methods=['POST'])
def ce_draft_bulk_cancel():
    """Cancel scheduling for multiple drafts (revert to pending)."""
    ids = request.form.getlist('ids')
    result = _draft.bulk_cancel(ids)
    flash_type = result.get("flash_type") or ("success" if result["ok"] else "warning")
    return redirect(f'/?tab=draft&flash_type={flash_type}&flash_msg={result["flash_msg"]}')
