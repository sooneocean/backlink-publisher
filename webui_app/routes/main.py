"""/ + /ce:clear — Plan 2026-05-18-001 Unit 3."""

from __future__ import annotations

import os

from flask import Blueprint, request, session

from ..helpers.contexts import _render

bp = Blueprint("main", __name__)


@bp.route('/')
def index():
    if os.environ.get("BACKLINK_PUBLISHER_WIZARD_REDIRECT") == "1":
        try:
            from webui_store import wizard_config_store as _wcfg
            wc = _wcfg._get()
            if not wc.get("completed") and not wc.get("skipped"):
                from flask import redirect, url_for
                return redirect(url_for("wizard.wizard_page"))
        except Exception:
            pass

    config = session.get('config', {})
    tab = request.args.get('tab', '')
    flash_type = request.args.get('flash_type', '')
    flash_msg = request.args.get('flash_msg', '')
    flash = {'type': flash_type, 'msg': flash_msg} if flash_type else None
    history_active = tab == 'draft'
    return _render('index.html', config=config,
                   history_active=history_active, flash=flash)


@bp.route('/ce:clear', methods=['POST'])
def ce_clear():
    """Clear session and restart."""
    session.clear()
    return _render('index.html')


@bp.route('/favicon.ico')
def favicon():
    return "", 204
