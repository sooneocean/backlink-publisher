"""Thin CLI-side helper that appends published records to publish-history.json.

``publish-backlinks`` uses this after a successful publish run so that
``equity-ledger`` can count ``live_dofollow`` without requiring a manual
JSON patch or a running WebUI session.  The WebUI's ``HistoryStore`` is
intentionally NOT imported here — this module only touches the file directly
and is safe to call from any PYTHONPATH that includes ``src/``.

Thread/process safety: uses ``atomic_write_json`` (write-to-tmp + rename)
which is safe for single-writer scenarios.  Concurrent CLI writers are rare
(operator-facing tool), and the read-modify-write window is tiny.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HISTORY_FILENAME = "publish-history.json"


def _config_dir() -> Path:
    from backlink_publisher.config.loader import _config_dir as _cd

    return _cd()


def _history_path(config_dir: Path | None = None) -> Path:
    return (config_dir or _config_dir()) / _HISTORY_FILENAME


def _load_history(path: Path) -> list[dict[str, Any]]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _existing_article_urls(history: list[dict[str, Any]]) -> set[str]:
    return {
        url
        for item in history
        for url in (item.get("article_urls") or [])
        if isinstance(url, str)
    }


def append_published_rows(
    rows: list[dict[str, Any]],
    *,
    config_dir: Path | None = None,
    verified_at: str | None = None,
) -> int:
    """Append successfully published rows to publish-history.json.

    Only rows with ``status == "published"`` and at least one ``article_url``
    are appended.  URLs already present in history are skipped (idempotent).

    Returns the number of new entries written.
    """
    path = _history_path(config_dir)
    history = _load_history(path)
    existing = _existing_article_urls(history)
    now = verified_at or datetime.now(timezone.utc).isoformat()

    new_entries: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "published":
            continue
        # Support both formats: article_urls list (old) and published_url string (new)
        article_urls = [u for u in (row.get("article_urls") or []) if u]
        if not article_urls:
            pub_url = row.get("published_url") or row.get("draft_url")
            if pub_url:
                article_urls = [pub_url]
        if not article_urls:
            continue
        # Skip if primary URL already recorded (idempotent on re-run)
        if article_urls[0] in existing:
            continue
        entry: dict[str, Any] = {
            "id": uuid.uuid4().hex[:16],
            "platform": row.get("platform"),
            "target_url": row.get("target_url"),
            "article_urls": article_urls,
            "status": "published",
            "verified_at": now,
            "created_at": now,
        }
        new_entries.append(entry)
        existing.update(article_urls)

    if not new_entries:
        return 0

    updated = history + new_entries
    _atomic_write_history(path, updated)
    return len(new_entries)


def _atomic_write_history(path: Path, data: list[dict[str, Any]]) -> None:
    """Write history atomically (tmp + rename) — safe for single-writer use."""
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
