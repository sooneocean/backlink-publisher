"""/ + /ce:clear — Plan 2026-05-18-001 Unit 3."""

from __future__ import annotations

from flask import Blueprint, request, session

from ..helpers import _render

bp = Blueprint("main", __name__)


@bp.route('/')
def index():
    config = session.get('config', {})
    ready_to_publish = None
    validated = session.get('validated', '')
    if validated:
        ready_to_publish = {
            'data': validated,
            'platform': config.get('platform', 'medium'),
        }
    tab = request.args.get('tab', '')
    flash_type = request.args.get('flash_type', '')
    flash_msg = request.args.get('flash_msg', '')
    flash = {'type': flash_type, 'msg': flash_msg} if flash_type else None
    history_active = tab == 'draft'
    return _render('index.html', config=config, ready_to_publish=ready_to_publish,
                   history_active=history_active, flash=flash)


@bp.route('/ce:clear', methods=['POST'])
def ce_clear():
    """Clear session and restart."""
    session.clear()
    return _render('index.html')
