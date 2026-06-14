"""Official URL intake blueprint."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from webui_store import queue_store

from ..services.official_url_intake import (
    OfficialUrlIntakeError,
    build_target_profile,
    enqueue_official_url_tasks,
    resolve_channel_eligibility,
)


bp = Blueprint("official_url", __name__)


@bp.route("/official-url", methods=["GET", "POST"])
def official_url_page():
    profile = None
    eligibility = []
    queue_ids: list[str] = []
    error = ""
    official_url = ""

    if request.method == "POST":
        official_url = (request.form.get("official_url") or "").strip()
        profile = build_target_profile(official_url)
        if profile.get("ok"):
            eligibility = resolve_channel_eligibility()
            if request.form.get("action") == "enqueue":
                selected = request.form.getlist("channels")
                try:
                    queue_ids = enqueue_official_url_tasks(
                        profile,
                        selected_channels=selected,
                        eligibility=eligibility,
                        queue_store=queue_store,
                    )
                except OfficialUrlIntakeError as exc:
                    error = exc.reason
        else:
            error = profile.get("reason", "invalid_url")

    return render_template(
        "official_url.html",
        official_url=official_url,
        profile=profile,
        eligibility=eligibility,
        queue_ids=queue_ids,
        error=error,
    )
