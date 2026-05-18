"""/profiles/* — Plan Unit 3."""

from __future__ import annotations

from flask import Blueprint, jsonify, redirect, request

from webui_store import profiles_store as _profiles_store

bp = Blueprint("profiles", __name__)


@bp.route('/profiles/save', methods=['POST'])
def profiles_save():
    """Save a campaign profile (AJAX JSON)."""
    name = request.form.get('profile_name', '').strip()
    if not name:
        return jsonify({'ok': False, 'error': '名称不能为空'})

    profile_data = {
        'platform': request.form.get('platform', 'blogger'),
        'language': request.form.get('language', 'zh-CN'),
        'url_mode': request.form.get('url_mode', 'A'),
        'publish_mode': request.form.get('publish_mode', 'draft'),
    }

    def _upsert(profiles):
        for p in profiles:
            if p.get('name') == name:
                p.update(profile_data)
                return profiles
        profiles.append({'name': name, **profile_data})
        return profiles

    _profiles_store.update(_upsert)
    return jsonify({'ok': True})


@bp.route('/profiles/delete', methods=['POST'])
def profiles_delete():
    """Delete a campaign profile by name."""
    name = request.form.get('profile_name', '').strip()
    _profiles_store.update(
        lambda profiles: [p for p in profiles if p.get('name') != name]
    )
    return redirect(request.referrer or '/')
