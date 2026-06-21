"""Official URL intake service for draft-first automation.

This module is intentionally WebUI-adjacent: it builds a safe target profile,
resolves channel eligibility from the publisher registry, and creates queue
tasks. It never calls the publish pipeline directly.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlparse, urlunparse

from backlink_publisher._util.url_derive import derive_path_tiers
from webui_app.helpers.url_meta import detect_language, get_main_domain


ProbeFn = Callable[[str], tuple[bool, str | None, str | None]]

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LEN = 2048
_BOUND_STATES = frozenset({"bound", "ok", "fresh"})


class OfficialUrlIntakeError(ValueError):
    """Typed error surfaced by route handlers without leaking internals."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_official_url(raw_url: str) -> tuple[str | None, str | None, str]:
    """Return `(clean_url, reject_reason, host)` for an operator-entered URL."""
    if not isinstance(raw_url, str):
        return None, "invalid_url", ""
    raw = raw_url.strip()
    if not raw:
        return None, "invalid_url", ""
    if len(raw) > _MAX_URL_LEN:
        return None, "invalid_url", ""
    if raw.lower().startswith(("javascript:", "file:", "ftp:", "data:", "mailto:")):
        return None, "blocked_scheme", ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return None, "invalid_url", ""
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return None, "blocked_scheme", ""
    host = parsed.hostname
    if not host:
        return None, "invalid_url", ""
    try:
        host_ascii = host.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, UnicodeDecodeError, UnicodeEncodeError, ValueError):
        return None, "invalid_url", ""
    netloc = host_ascii + (f":{port}" if port else "")
    clean = urlunparse(parsed._replace(netloc=netloc))
    return clean, None, host_ascii


def _default_probe(url: str) -> tuple[bool, str | None, str | None]:
    from backlink_publisher.content import fetch as content_fetch

    return content_fetch.verify_url_has_content(
        url,
        max_age_seconds=0,
        timeout_seconds=5,
        max_redirects=3,
    )


def build_target_profile(
    raw_url: str,
    *,
    probe_fn: ProbeFn | None = None,
) -> dict[str, Any]:
    """Build a safe target profile from one official website URL."""
    clean_url, reject_reason, _host = normalize_official_url(raw_url)
    if reject_reason is not None or clean_url is None:
        return {"ok": False, "reason": reject_reason or "invalid_url"}

    probe = probe_fn or _default_probe
    try:
        ok, reason, title = probe(clean_url)
    except Exception:
        ok, reason, title = False, "network_error", None
    if not ok:
        return {
            "ok": False,
            "reason": reason or "network_error",
            "official_url": clean_url,
        }

    tiers = derive_path_tiers(clean_url)
    main_url = tiers.get("main") or get_main_domain(clean_url)
    category_url = tiers.get("category")
    work_url = tiers.get("work")
    target_url = work_url or category_url or main_url or clean_url

    return {
        "ok": True,
        "reason": "ok",
        "official_url": clean_url,
        "target_url": target_url,
        "main_url": main_url,
        "category_url": category_url,
        "work_url": work_url,
        "main_domain": get_main_domain(main_url),
        "language": detect_language(clean_url),
        "title": title or "",
        "probe_reason": reason or "ok",
    }


def _load_status_data(
    channel_status: Any | None,
    platform_slugs: Iterable[str] | None = None,
) -> dict[str, Any]:
    if channel_status is None:
        try:
            from backlink_publisher.config import load_config
            from webui_app.binding_status import get_channel_status

            cfg = load_config()
            loaded = {
                slug: get_channel_status(slug, cfg)
                for slug in (platform_slugs or [])
            }
        except Exception:
            loaded = {}
    elif hasattr(channel_status, "load"):
        loaded = channel_status.load()
    else:
        loaded = channel_status
    return loaded if isinstance(loaded, dict) else {}


def _load_history(history: Any | None) -> list[dict[str, Any]]:
    if history is None:
        try:
            from webui_store import history_store

            loaded = history_store.load()
        except Exception:
            loaded = []
    elif hasattr(history, "load"):
        loaded = history.load()
    else:
        loaded = history
    return loaded if isinstance(loaded, list) else []


def _today_count(channel: str, history: list[dict[str, Any]]) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for row in history:
        if not isinstance(row, dict):
            continue
        if (row.get("platform") or row.get("channel")) != channel:
            continue
        created = str(row.get("created_at") or "")
        if created.startswith(today):
            count += 1
    return count


def resolve_channel_eligibility(
    *,
    channel_status: Any | None = None,
    history: Any | None = None,
    daily_cap: int = 10,
) -> list[dict[str, Any]]:
    """Resolve active channels into eligible/ineligible rows with reasons."""
    import backlink_publisher.publishing.adapters  # noqa: F401
    from backlink_publisher.publishing._registry_manifest import active_platforms, ui_meta
    from backlink_publisher.publishing.registry import (
        auth_type,
        dofollow_status,
        referral_value,
    )

    platforms = active_platforms()
    status_data = _load_status_data(channel_status, platforms)
    history_rows = _load_history(history)
    rows: list[dict[str, Any]] = []

    for slug in platforms:
        meta = ui_meta(slug)
        dofollow = dofollow_status(slug)
        referral = referral_value(slug)
        auth = auth_type(slug)
        rec = status_data.get(slug, {}) if isinstance(status_data, dict) else {}
        status = rec.get("status", "unbound") if isinstance(rec, dict) else "unbound"
        bound = bool(rec.get("bound")) if isinstance(rec, dict) else False

        eligible = False
        reason = "eligible"
        if dofollow is False:
            reason = "nofollow_only"
        elif dofollow == "uncertain":
            reason = "dofollow_uncertain"
        elif status == "expired":
            reason = "auth_expired"
        elif status == "identity_mismatch":
            reason = "identity_mismatch"
        elif auth != "anon" and not bound and status not in _BOUND_STATES:
            reason = "unbound"
        elif _today_count(slug, history_rows) >= daily_cap:
            reason = "daily_cap_exhausted"
        else:
            eligible = True

        rows.append(
            {
                "slug": slug,
                "display_name": meta.display_name if meta else slug.title(),
                "domain": meta.domain if meta else "",
                "category": meta.category if meta else "",
                "auth_type": auth,
                "dofollow": dofollow,
                "referral_value": referral,
                "status": status,
                "eligible": eligible,
                "selected": eligible,
                "reason": "eligible" if eligible else reason,
            }
        )

    return rows


def enqueue_official_url_tasks(
    profile: dict[str, Any],
    *,
    selected_channels: Iterable[str],
    eligibility: Iterable[dict[str, Any]],
    queue_store: Any,
) -> list[str]:
    """Create draft-first queue tasks for selected eligible channels."""
    if not profile.get("ok"):
        raise OfficialUrlIntakeError("invalid_profile")

    eligible = {
        str(row.get("slug")): row
        for row in eligibility
        if row.get("eligible") and row.get("slug")
    }
    selected = [slug for slug in selected_channels if slug]
    if not selected:
        raise OfficialUrlIntakeError("no_channels_selected")
    invalid = [slug for slug in selected if slug not in eligible]
    if invalid:
        raise OfficialUrlIntakeError(
            "ineligible_channel",
            f"ineligible_channel: {', '.join(sorted(invalid))}",
        )

    now = _now_iso()
    task_ids: list[str] = []
    tasks_to_add: list[dict[str, Any]] = []
    for slug in selected:
        task_id = str(uuid.uuid4())[:8]
        task_ids.append(task_id)
        tasks_to_add.append(
            {
                "id": task_id,
                "status": "pending",
                "created_at": now,
                "urls": [profile["target_url"]],
                "config": {
                    "platform": slug,
                    "target_url": profile["target_url"],
                    "official_url": profile["official_url"],
                    "main_domain": profile["main_domain"],
                    "category_url": profile.get("category_url") or "",
                    "work_url": profile.get("work_url") or "",
                    "target_language": profile.get("language") or "zh-CN",
                    "url_mode": "C",
                    "publish_mode": "draft",
                    "custom_title": profile.get("title") or "",
                    "custom_tags": "",
                    "source": "official_url_intake",
                    "source_type": "official_url",
                },
            }
        )

    def _enqueue(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tasks.extend(tasks_to_add)
        return tasks

    queue_store.update(_enqueue)
    return task_ids
