"""Checkpoint helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides checkpoint update and failure recording utilities.
"""

from __future__ import annotations

import sys
from typing import Any


def _try_update_ckpt_failed(
    run_id: str | None,
    row_id: str,
    error: str,
    error_class: str,
) -> str | None:
    from backlink_publisher import checkpoint as _checkpoint

    if run_id is None:
        return None
    try:
        _checkpoint.update_item(run_id, row_id, "failed", error=error, error_class=error_class)
    except Exception as ckpt_exc:
        print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
        return None
    return run_id


def _build_failure_row(
    status: str,
    row: dict[str, Any],
    platform: str,
    error: str,
    ts: str,
    *,
    adapter: str = "",
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.get("id", ""),
        "platform": platform,
        "status": status,
        "title": row.get("title", ""),
        "draft_url": "",
        "published_url": "",
        "created_at": ts,
        "adapter": adapter,
        "error": error,
    }
    out.update(extra)
    return out


def _build_skip_row(
    row: dict[str, Any], platform: str, live_url: str | None, ts: str
) -> dict[str, Any]:
    """A SKIP-DUPLICATE output row (enforce gate, U7): the backlink is already
    live, so it carries the recorded ``live_url`` and ``error=None`` — it counts
    as a present backlink for downstream, distinguished by its status."""
    return {
        "id": row.get("id", ""),
        "platform": platform,
        "status": "skipped_duplicate",
        "title": row.get("title", ""),
        "draft_url": "",
        "published_url": live_url or "",
        "created_at": ts,
        "adapter": platform,
        "error": None,
        "_dedup_verdict": "skip",
    }