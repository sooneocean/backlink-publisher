"""Publish-history canonical write path.

Extracted from webui_app/helpers/__init__.py in Plan 2026-05-21-007 Unit 2.
All callers must write history through these helpers — never directly via
_history_store.update (publish-history invariant per PR #87/#97/#156/#167).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from webui_store import history_store as _history_store


_HISTORY_MAX_ITEMS = 100

# Statuses that require at least one article URL — operator-visible "success"
# states must be backed by a real URL or the publish-history invariant is broken.
_REQUIRES_URL_STATUSES: frozenset[str] = frozenset({"published", "drafted"})


def _parse_publish_results(jsonl_str):
    results = []
    for line in (jsonl_str or '').strip().split('\n'):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def _apply_history_cap(hist: list[dict]) -> list[dict]:
    """Trim history to the configured maximum, newest-first order preserved."""
    return hist[:_HISTORY_MAX_ITEMS]


def _push_history_per_row(
    rows: list[dict],
    *,
    target_url_fallback: str = "unknown",
    platform_fallback: str = "",
    language_fallback: str = "",
) -> list[dict]:
    """Append one history entry per CLI publish-result row, preserving the
    per-row ``status`` field (including ``*_unverified`` suffixes).

    Plan 2026-05-19-006 Unit 1 root-cause fix: previously the three WebUI
    callsites (``_publish_draft_job`` / batch / publish-real) collapsed a
    multi-row publish-backlinks stdout into one history entry whose status
    was hard-coded to ``'drafted'`` or ``'published'`` regardless of the
    real per-row outcome. The ``*_unverified`` rows therefore showed up
    as solid green ✓ even though the outside site never received the
    article.

    This helper writes one history item per row, transparently carrying
    the row's real ``status`` and ``error``, and synthesises a ``failed``
    status when both ``published_url`` and ``draft_url`` are empty (which
    means the adapter returned no usable URL).
    """
    if not rows:
        return _history_store.load()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    # Rows from one publish call share a run_id so the UI can group them
    # under one collapsible card instead of N separate entries.
    run_id = str(uuid.uuid4())[:8]
    new_items: list[dict] = []
    for row in rows:
        published_url = (row.get("published_url") or "").strip()
        draft_url = (row.get("draft_url") or "").strip()
        article_urls = [u for u in (published_url, draft_url) if u]
        raw_error = row.get("error")
        status = row.get("status") or ""
        # Coerce "no URL returned but no error" to failed — adapter silently
        # gave us nothing usable.
        if not article_urls and not raw_error and not status.endswith("_unverified"):
            status = "failed"
            raw_error = "no URL returned by adapter"
        elif not status:
            status = "failed" if raw_error else "published"
        item = {
            "id": str(uuid.uuid4())[:8],
            "run_id": run_id,
            "target_url": row.get("target_url") or target_url_fallback,
            "platform": row.get("platform") or platform_fallback,
            "language": row.get("language") or language_fallback,
            "status": status,
            "created_at": row.get("created_at") or now_str,
            "article_urls": article_urls,
            "title": row.get("title", ""),
            "adapter": row.get("adapter", ""),
        }
        if raw_error:
            item["error"] = raw_error
        new_items.append(item)
    return _history_store.update(
        lambda hist: [*new_items, *hist][:_HISTORY_MAX_ITEMS]
    )


def _push_history_single_failure(
    *,
    target_url: str,
    platform: str,
    language: str,
    error: str,
) -> list[dict]:
    """Append one synthetic ``failed`` history entry — used when the publish
    CLI itself blew up (subprocess returncode!=0 or exception) and there
    are no per-row outputs to forward."""
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    item = {
        "id": str(uuid.uuid4())[:8],
        "target_url": target_url or "unknown",
        "platform": platform,
        "language": language,
        "status": "failed",
        "created_at": now_str,
        "article_urls": [],
        "title": "",
        "adapter": "",
        "error": error or "publish failed",
    }
    return _history_store.update(
        lambda hist: [item, *hist][:_HISTORY_MAX_ITEMS]
    )


def _push_history_aggregate(entry: dict) -> list[dict]:
    """Append a single caller-built aggregate entry to publish history.

    Unlike ``_push_history_per_row`` (which writes one entry per CLI row),
    this helper is for callers that have already collapsed N rows into one
    entry — e.g. ``checkpoint.py`` which writes a per-resume summary rather
    than per-row details.

    Invariant: if ``entry['status']`` is in ``_REQUIRES_URL_STATUSES`` then
    ``entry['article_urls']`` must be non-empty.  Callers whose status-collapse
    logic (e.g. exit-code 4 = failed_partial) produces statuses outside this
    set are always accepted.

    Raises:
        ValueError: if the invariant is violated.
    """
    if (entry.get("status") in _REQUIRES_URL_STATUSES
            and not entry.get("article_urls")):
        raise ValueError(
            f"_push_history_aggregate: entry status={entry.get('status')!r} "
            f"requires non-empty article_urls; got {entry.get('article_urls')!r}"
        )
    return _history_store.update(
        lambda hist: _apply_history_cap([entry, *hist])
    )
