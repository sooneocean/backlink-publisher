"""DraftsStore — draft-queue specialized JsonStore with item helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import JsonStore


class DraftsStore(JsonStore):
    """Stores the draft queue (list of dicts) plus item-level helpers
    that mirror the legacy ``_get_draft_item`` / ``_update_draft_item``
    / ``_delete_draft_item`` semantics from ``webui.py``.

    All item operations go through ``update()`` so the per-store lock
    protects in-process concurrency between scheduler-triggered writes
    (background ``BackgroundScheduler`` jobs) and HTTP-handler writes.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path, default_factory=list)

    def get_item(self, item_id: str) -> dict | None:
        """Return the matching draft, or ``None``. Read-only; no lock."""
        for item in self.load():
            if item.get("id") == item_id:
                return item
        return None

    def update_item(self, item_id: str, **fields: Any) -> bool:
        """Locate by id, merge ``fields`` in place, save. Returns False
        if no matching id was found (no-op write skipped)."""
        def _apply(items: list[dict]) -> tuple[list[dict], bool]:
            for it in items:
                if it.get("id") == item_id:
                    it.update(fields)
                    return items, True
            return items, False

        with self._lock:
            items = self.load()
            for it in items:
                if it.get("id") == item_id:
                    it.update(fields)
                    self.save(items)
                    return True
            return False

    def delete_item(self, item_id: str) -> bool:
        """Remove the matching draft. Returns False if absent."""
        with self._lock:
            items = self.load()
            new_items = [it for it in items if it.get("id") != item_id]
            if len(new_items) == len(items):
                return False
            self.save(new_items)
            return True

    def insert_first(self, item: dict) -> list[dict]:
        """Atomic head-insert (legacy ``items.insert(0, item)`` pattern)."""
        return self.update(lambda items: [item, *items])
