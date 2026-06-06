"""Persistent store for already-seen URLs (watch-service dedup).

Tracks which URLs have been discovered from each seed source so the
watch service can detect new URLs without re-processing known ones.

Schema (dict keyed by ``url_hash`` — first 16 hex chars of SHA-256)::

    {
      "<url_hash>": {
        "url": str,
        "url_hash": str,
        "source_type": "sitemap" | "manual" | "bookmark",
        "source_origin": str,   # sitemap URL, "manual", or bookmark file path
        "discovered_at": str,   # ISO-8601
        "last_seen_at": str,    # ISO-8601
        "coverage": {           # channel → status
          "<channel>": "pending" | "published" | "failed" | "skipped"
        }
      }
    }
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .base import JsonStore


# Keep in sync with webui_store/__init__.py __all__
__all__ = ["SeenUrlsStore"]


def _url_hash(url: str) -> str:
    """Return first 16 hex chars of SHA-256 of the normalized URL."""
    normalized = url.strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


class SeenUrlsStore(JsonStore):
    """JSON store tracking which URLs have been seen by the watch service."""

    def __init__(self, path) -> None:
        super().__init__(path, default_factory=dict)

    # ── Lookup helpers ─────────────────────────────────────────────────

    def is_new(self, url: str) -> bool:
        """Return True if *url* has never been recorded."""
        data = self.load()
        return _url_hash(url) not in data

    def get_by_source(self, source_type: str) -> list[dict[str, Any]]:
        """Return all records matching a given source type."""
        data = self.load()
        return [r for r in data.values() if r["source_type"] == source_type]

    def get_uncovered(self) -> list[dict[str, Any]]:
        """Return records where no channel has ``"published"`` status."""
        data = self.load()
        uncovered = []
        for record in data.values():
            cov = record.get("coverage", {})
            if not any(s == "published" for s in cov.values()):
                uncovered.append(record)
        return uncovered

    # ── Mutation helpers ────────────────────────────────────────────────

    def mark_seen(
        self,
        url: str,
        source_type: str,
        source_origin: str,
    ) -> dict[str, Any]:
        """Record a URL as seen. If already known, update ``last_seen_at``."""
        h = _url_hash(url)
        now = datetime.now(timezone.utc).isoformat()

        def _upsert(data: dict) -> dict:
            if h in data:
                data[h]["last_seen_at"] = now
            else:
                data[h] = {
                    "url": url,
                    "url_hash": h,
                    "source_type": source_type,
                    "source_origin": source_origin,
                    "discovered_at": now,
                    "last_seen_at": now,
                    "coverage": {},
                }
            return data

        self.update(_upsert)
        return {
            "url": url,
            "url_hash": h,
            "source_type": source_type,
            "source_origin": source_origin,
        }

    def update_coverage(self, url_hash: str, channel: str, status: str) -> None:
        """Set coverage status for *channel* on the given URL record."""

        def _update(data: dict) -> dict:
            if url_hash in data:
                data[url_hash].setdefault("coverage", {})[channel] = status
            return data

        self.update(_update)
