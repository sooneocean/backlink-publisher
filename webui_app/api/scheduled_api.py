"""Scheduled-item query surface for the WebUI schedule page."""
from __future__ import annotations

from typing import Any

from webui_store import drafts_store as _drafts_store


def list_scheduled() -> dict[str, Any]:
    """Return ``{ok: True, items: [...]}`` for drafts with a future or pending
    ``scheduled_at``/``status == "scheduled"``.
    """
    try:
        items = [
            item for item in _drafts_store.load()
            if item.get("status") == "scheduled"
            or item.get("scheduled_at")
        ]
        return {"ok": True, "items": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "items": []}
