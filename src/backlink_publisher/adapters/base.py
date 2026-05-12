"""Shared types for publisher adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterResult:
    """Normalised result returned by every adapter."""

    status: str          # "drafted" | "published" | "failed"
    adapter: str         # e.g. "blogger-api", "medium-api", "medium-browser"
    platform: str        # "blogger" | "medium"
    draft_url: str = ""
    published_url: str = ""
    error: str | None = None
    _dry_run: bool = False
    _command: str = ""

    def to_publish_output(self, row: dict[str, Any], created_at: str) -> dict[str, Any]:
        """Convert to the JSONL output shape expected by publish_backlinks."""
        return {
            "id": row.get("id", ""),
            "platform": self.platform,
            "status": self.status,
            "title": row.get("title", ""),
            "draft_url": self.draft_url,
            "published_url": self.published_url,
            "created_at": created_at,
            "adapter": self.adapter,
            "error": self.error,
        }
