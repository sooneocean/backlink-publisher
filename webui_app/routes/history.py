"""/ce:history* — Plan Unit 3 + Plan 2026-05-19-006 bulk operations."""

from __future__ import annotations

from flask import Blueprint, redirect, request, session

from webui_store import history_store as _history_store

from ..helpers import _draft_tab_extra, _render

bp = Blueprint("history", __name__)


@bp.route('/ce:history', methods=['GET', 'POST'])
def ce_history():
    config = session.get('config', {})
    validated = session.get('validated', '')
    ready_to_publish = (
        {'data': validated, 'platform': config.get('platform', 'blogger')}
        if validated else None
    )
    return _render('index.html',
        history=_history_store.load(),
        history_active=True,
        ready_to_publish=ready_to_publish,
        config=config,
        **_draft_tab_extra())


@bp.route('/ce:history/delete', methods=['POST'])
def ce_history_delete():
    item_id = request.form.get('id', '')
    history = _history_store.update(
        lambda hist: [h for h in hist if h.get('id') != item_id]
    )
    return _render('index.html', history=history, history_active=True,
                   config=session.get('config', {}))


@bp.route('/ce:history/update-status', methods=['POST'])
def ce_history_update_status():
    item_id = request.form.get('id', '')
    new_status = request.form.get('status', '')

    def _apply(hist):
        for h in hist:
            if h.get('id') == item_id:
                h['status'] = new_status
                break
        return hist

    history = _history_store.update(_apply)
    return _render('index.html', history=history, history_active=True,
                   config=session.get('config', {}))


@bp.route('/ce:history/reuse', methods=['POST'])
def ce_history_reuse():
    target_url = request.form.get('target_url', '')
    session.pop('plans', None)
    session.pop('validated', None)
    return _render('index.html', target_url=target_url,
                   config=session.get('config', {}))


# ──────────────────────────────────────────────────────────────────────────
# Bulk operations — Plan 2026-05-19-006 Unit 4
# ──────────────────────────────────────────────────────────────────────────


@bp.route('/ce:history/bulk-delete', methods=['POST'])
def ce_history_bulk_delete():
    """Delete multiple history entries by id."""
    ids = request.form.getlist('ids')
    if not ids:
        return redirect('/ce:history?flash_type=warning&flash_msg=未选择任何项')
    removed = _history_store.bulk_delete(ids)
    return redirect(
        f'/ce:history?flash_type=success&flash_msg=已删除 {removed} 条历史记录'
    )


@bp.route('/ce:history/purge-failed', methods=['POST'])
def ce_history_purge_failed():
    """One-shot delete every history entry whose status is exactly 'failed'.
    Does not require ids — convenience for the common "clean up false alarms"
    flow."""
    removed = _history_store.purge_by_status('failed')
    if removed == 0:
        return redirect('/ce:history?flash_type=info&flash_msg=没有失败记录可清除')
    return redirect(
        f'/ce:history?flash_type=success&flash_msg=已清除 {removed} 条失败记录'
    )


@bp.route('/ce:history/recheck', methods=['POST'])
def ce_history_recheck():
    """Re-verify a single history item by id."""
    item_id = request.form.get('id', '')
    if not item_id:
        return redirect('/ce:history?flash_type=danger&flash_msg=参数缺失')
    item = _history_store.get_item(item_id)
    if not item:
        return redirect('/ce:history?flash_type=danger&flash_msg=记录不存在')
    from ..services.recheck import recheck_one
    mutation = recheck_one(item)
    mutation.pop('_outcome', None)
    _history_store.update_item(item_id, **mutation)
    status = mutation.get('status', '')
    msg = f'已重新核实：状态 → {status}'
    return redirect(f'/ce:history?flash_type=success&flash_msg={msg}')


@bp.route('/ce:history/bulk-recheck', methods=['POST'])
def ce_history_bulk_recheck():
    """Re-verify multiple history entries; updates store in one pass."""
    ids = request.form.getlist('ids')
    if not ids:
        return redirect('/ce:history?flash_type=warning&flash_msg=未选择任何项')
    items = [it for it in _history_store.load() if it.get('id') in set(ids)]
    if not items:
        return redirect('/ce:history?flash_type=warning&flash_msg=未匹配到记录')
    from ..services.recheck import recheck_many
    by_id, summary = recheck_many(items)
    # Apply mutations one-by-one (each has its own field set; bulk_update
    # only supports a single field set across ids).
    for item_id, mutation in by_id.items():
        _history_store.update_item(item_id, **mutation)
    return redirect(
        f'/ce:history?flash_type=success&flash_msg={summary.as_flash()}'
    )
