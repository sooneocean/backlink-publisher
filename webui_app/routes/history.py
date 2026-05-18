"""/ce:history* — Plan Unit 3."""

from __future__ import annotations

from flask import Blueprint, request, session

from webui_store import history_store as _history_store

from ..helpers import _draft_tab_extra, _render

bp = Blueprint("history", __name__)


@bp.route('/ce:history', methods=['GET', 'POST'])
def ce_history():
    config = session.get('config', {})
    validated = session.get('validated', '')
    ready_to_publish = (
        {'data': validated, 'platform': config.get('platform', 'medium')}
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
