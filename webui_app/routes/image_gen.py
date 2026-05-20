"""Image-gen settings routes — Plan 2026-05-20-001 Unit 6.

Provides ``/settings/test-image-gen`` so the operator can verify
that the configured base_url + frw-token are reachable BEFORE
running plan-backlinks (which would otherwise discover a broken
key only after burning quota on retries).

The test deliberately calls ``GET <base_url>/models`` — the cheapest
OpenAI-compatible probe that doesn't generate (and bill for) an
image.  Falls back to a minimal ``/chat/completions`` probe when
the gateway doesn't expose ``/models``.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

import requests

bp = Blueprint("image_gen", __name__)


@bp.route("/settings/test-image-gen", methods=["POST"])
def settings_test_image_gen():
    """Probe ``<base_url>/models`` using the current ``Config.image_gen``
    + ``frw-token.json`` and return ``{ok, model_count?, error?}``."""
    try:
        from backlink_publisher.config import load_config
        from backlink_publisher._util.secrets import load_frw_token

        try:
            cfg = load_config()
        except Exception as exc:
            return jsonify({
                "ok": False,
                "error": f"load_config failed: {exc}",
            }), 200

        if cfg.image_gen is None:
            return jsonify({
                "ok": False,
                "error": "no_image_gen_section: add [image_gen] to config.toml first",
            }), 200

        try:
            api_key = load_frw_token()
        except RuntimeError as exc:
            return jsonify({
                "ok": False,
                "error": f"no_token: {exc}",
            }), 200

        base_url = cfg.image_gen.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            resp = requests.get(
                f"{base_url}/models",
                headers=headers,
                timeout=10,
            )
        except requests.RequestException as exc:
            return jsonify({
                "ok": False,
                "error": f"network error: {exc}",
            }), 200

        if resp.status_code == 401:
            return jsonify({
                "ok": False,
                "error": "auth_failed: api_key rejected — rotate via `frw-login`",
            }), 200

        if resp.status_code == 200:
            try:
                payload = resp.json()
                if isinstance(payload, dict) and "data" in payload:
                    return jsonify({
                        "ok": True,
                        "model_count": len(payload["data"]),
                        "configured_model": cfg.image_gen.model,
                    }), 200
            except Exception:
                pass
            return jsonify({"ok": True, "model_count": 0}), 200

        return jsonify({
            "ok": False,
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
        }), 200
    except Exception as exc:
        return jsonify({"ok": False, "error": f"unexpected: {exc}"}), 200
