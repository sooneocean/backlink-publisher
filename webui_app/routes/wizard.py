"""Setup wizard blueprint — multi-step onboarding for new operators.

GET /wizard         — render wizard page (redirects to / if already completed)
GET /api/wizard/status — JSON status for JS polling
POST /wizard/step/seed-sources — save seed source config
POST /wizard/step/channels    — save channel selection
POST /wizard/step/rules       — save automation rules
POST /wizard/step/launch      — complete wizard + start automation
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backlink_publisher._util.logger import plan_logger
from backlink_publisher.publishing.registry import registered_platforms

from ..helpers.contexts import _render

bp = Blueprint("wizard", __name__, template_folder="../templates")


# ── Helpers ──────────────────────────────────────────────────────────────────


_URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


def _is_valid_url(url: str) -> bool:
    """Basic URL validation — must start with http:// or https:// and have a valid host."""
    return bool(_URL_RE.match(url.strip()))


def _get_stores():
    """Lazy-import stores inside request context."""
    from flask import current_app

    stores = current_app.extensions["webui_stores"]
    return stores.wizard_config, stores.score, stores.seen_urls


@bp.route("/wizard")
def wizard_page():
    """Render the setup wizard, or redirect if already completed."""
    wizard_store, _, _ = _get_stores()
    if wizard_store.is_completed():
        from flask import redirect, url_for

        return redirect(url_for("main.index"))

    platforms = sorted(
        ({"name": p, "bound": False} for p in registered_platforms()),
        key=lambda x: x["name"],
    )
    return _render("wizard.html", wizard_platforms=platforms)


@bp.route("/api/wizard/status")
def wizard_status():
    """Return JSON status for JS polling (system active indicator)."""
    wizard_store, _, _ = _get_stores()
    cfg = wizard_store._get()
    return jsonify(
        {
            "completed": cfg.get("completed", False),
            "skipped": cfg.get("skipped", False),
            "completed_at": cfg.get("completed_at"),
        }
    )


@bp.route("/wizard/step/seed-sources", methods=["POST"])
def save_seed_sources():
    """Save seed source configuration from wizard step."""
    wizard_store, _, _ = _get_stores()
    data = request.get_json(force=True) or {}

    sources = []
    # Sitemap URLs
    for url in data.get("sitemap_urls", []):
        url = url.strip()
        if url:
            if not _is_valid_url(url):
                return jsonify({"error": f"Invalid URL: {url}"}), 400
            sources.append(
                {
                    "id": str(uuid.uuid4())[:8],
                    "type": "sitemap",
                    "value": url,
                    "label": url,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "enabled": True,
                }
            )
    # Manual targets
    manual_text = (data.get("manual_targets", "") or "").strip()
    if manual_text:
        sources.append(
            {
                "id": str(uuid.uuid4())[:8],
                "type": "manual",
                "value": manual_text,
                "label": "Manual target list",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "enabled": True,
            }
        )
    # Bookmark upload
    bookmark_url = data.get("bookmark_url", "")
    if bookmark_url:
        sources.append(
            {
                "id": str(uuid.uuid4())[:8],
                "type": "bookmark",
                "value": bookmark_url,
                "label": bookmark_url,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "enabled": True,
            }
        )

    def _update(data):
        cfg = data.setdefault("wizard_config", {})
        cfg["seed_sources"] = sources
        return data

    wizard_store.update(_update)
    return jsonify({"ok": True, "count": len(sources)})


@bp.route("/wizard/step/channels", methods=["POST"])
def save_channels():
    """Save channel selection + binding confirmation from wizard step."""
    wizard_store, _, _ = _get_stores()
    data = request.get_json(force=True) or {}

    selected = data.get("channels", [])
    channels = []
    for ch in selected:
        ch_name = ch.get("channel", "")
        bound = ch.get("bound", False)
        daily_cap = ch.get("daily_cap", 10)
        lang_whitelist = ch.get("language_whitelist", [])
        dofollow_pref = ch.get("dofollow_preference", True)
        channels.append(
            {
                "channel": ch_name,
                "bound": bound,
                "daily_cap": daily_cap,
                "dofollow_preference": dofollow_pref,
                "language_whitelist": lang_whitelist,
            }
        )

    def _update(data):
        cfg = data.setdefault("wizard_config", {})
        cfg["channels"] = channels
        return data

    wizard_store.update(_update)
    return jsonify({"ok": True, "count": len(channels)})


@bp.route("/wizard/step/rules", methods=["POST"])
def save_rules():
    """Save automation rules from wizard step."""
    wizard_store, _, _ = _get_stores()
    data = request.get_json(force=True) or {}

    rules = {
        "polling_interval_seconds": data.get("polling_interval_seconds", 21600),
        "default_daily_cap": data.get("default_daily_cap", 10),
        "max_daily_publish": data.get("max_daily_publish", 50),
        "language_filter": data.get("language_filter", []),
    }

    def _update(data):
        cfg = data.setdefault("wizard_config", {})
        cfg["automation_rules"] = rules
        return data

    wizard_store.update(_update)
    return jsonify({"ok": True})


@bp.route("/wizard/step/launch", methods=["POST"])
def launch_wizard():
    """Complete wizard: mark as done, trigger first watch cycle."""
    wizard_store, _, _ = _get_stores()

    def _complete(data):
        cfg = data.setdefault("wizard_config", {})
        cfg["completed"] = True
        cfg["completed_at"] = datetime.now(timezone.utc).isoformat()
        return data

    wizard_store.update(_complete)

    # Trigger immediate first watch cycle
    try:
        from webui_app.scheduler import _trigger_watch_cycle

        _trigger_watch_cycle()
    except Exception as exc:
        plan_logger.warn("watch_cycle_trigger_failed", error=str(exc))

    return jsonify({"status": "active", "redirect": "/"})
