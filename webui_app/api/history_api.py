"""HistoryAPI — structured CRUD wrapper around ``history_store`` + recheck.

Centralises history operations so routes never touch the store or recheck
service directly.  Every mutating method returns a dict with ``ok`` /
``flash_msg`` for the route's redirect (or JSON) response.
"""

from __future__ import annotations

from typing import Any

from flask import abort

from webui_store import history_store as _history_store
from webui_store import queue_store as _queue_store

from ..helpers.history import _REQUIRES_URL_STATUSES


# ── HistoryAPI ─────────────────────────────────────────────────────────────


class HistoryAPI:
    """Encapsulates publish-history CRUD and recheck operations.

    Usage::

        api = HistoryAPI()
        items = api.list()
        result = api.recheck("item-123")
        # result == {"ok": True, "flash_msg": "已重新核实：状态 → published"}
    """

    # ── list ──────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_item(item: dict) -> dict:
        """Backfill URL fields for older history rows."""
        normalized = dict(item)
        article_urls = normalized.get("article_urls")
        if not isinstance(article_urls, list) or not article_urls:
            article_urls = [
                u for u in (
                    (normalized.get("published_url") or "").strip(),
                    (normalized.get("draft_url") or "").strip(),
                ) if u
            ]
        if article_urls:
            normalized["article_urls"] = article_urls
        target_url = (normalized.get("target_url") or "").strip()
        if not target_url:
            normalized["target_url"] = article_urls[0] if article_urls else "unknown"
        return normalized

    @staticmethod
    def _normalize_items(items: list[dict]) -> list[dict]:
        return [HistoryAPI._normalize_item(item) for item in items]

    def list(self) -> list[dict]:
        """Return all history items, normalised."""
        return self._normalize_items(_history_store.load())

    # ── delete ────────────────────────────────────────────────────────────

    def delete(self, item_id: str) -> dict[str, Any]:
        """Delete a single history entry."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}
        history = _history_store.update(
            lambda hist: [h for h in hist if h.get("id") != item_id]
        )
        return {
            "ok": True,
            "history": self._normalize_items(history),
        }

    # ── update-status ─────────────────────────────────────────────────────

    def update_status(
        self,
        item_id: str,
        new_status: str,
    ) -> dict[str, Any]:
        """Update the status of a single history entry.

        Validates the server-side invariant: ``published`` / ``drafted``
        statuses require at least one article URL.
        """
        if not item_id or not new_status:
            return {"ok": False, "flash_msg": "参数缺失"}

        # Server-side invariant guard (F22)
        if new_status in _REQUIRES_URL_STATUSES:
            current = _history_store.load()
            matched = next((h for h in current if h.get("id") == item_id), None)
            if matched is not None and not matched.get("article_urls"):
                abort(400, description=(
                    f"invariant_violation: cannot set status={new_status!r} "
                    "on a history row with no article URLs"
                ))

        def _apply(hist):
            for h in hist:
                if h.get("id") == item_id:
                    h["status"] = new_status
                    break
            return hist

        history = _history_store.update(_apply)
        return {
            "ok": True,
            "history": self._normalize_items(history),
        }

    # ── reuse ─────────────────────────────────────────────────────────────

    def reuse(self, target_url: str) -> dict[str, Any]:
        """Prepare state for reusing a history entry's target URL."""
        return {"ok": True, "target_url": target_url}

    # ── bulk operations ──────────────────────────────────────────────────

    def bulk_delete(self, ids: list[str]) -> dict[str, Any]:
        """Delete multiple history entries by id."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}
        removed = _history_store.bulk_delete(ids)
        return {"ok": True, "flash_msg": f"已删除 {removed} 条历史记录"}

    def purge_failed(self) -> dict[str, Any]:
        """Delete every history entry whose status is exactly ``failed``.

        Returns ``ok=False`` when no records were removed so callers can
        set ``flash_type=info`` instead of ``flash_type=success``.
        """
        removed = _history_store.purge_by_status("failed")
        if removed == 0:
            return {"ok": False, "flash_msg": "没有失败记录可清除"}
        return {"ok": True, "flash_msg": f"已清除 {removed} 条失败记录"}

    # ── recheck ───────────────────────────────────────────────────────────

    def recheck(self, item_id: str) -> dict[str, Any]:
        """Re-verify a single history item."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}
        item = _history_store.get_item(item_id)
        if not item:
            return {"ok": False, "flash_msg": "记录不存在"}

        from ..services.recheck import recheck_one
        mutation = recheck_one(self._normalize_item(item))
        mutation.pop("_outcome", None)
        _history_store.update_item(item_id, **mutation)
        status = mutation.get("status", "")
        return {"ok": True, "flash_msg": f"已重新核实：状态 → {status}"}

    def bulk_recheck(self, ids: list[str]) -> dict[str, Any]:
        """Re-verify multiple history entries."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}
        items = [it for it in _history_store.load() if it.get("id") in set(ids)]
        if not items:
            return {"ok": False, "flash_msg": "未匹配到记录"}

        from ..services.recheck import recheck_many
        by_id, summary = recheck_many(self._normalize_items(items))
        for item_id, mutation in by_id.items():
            _history_store.update_item(item_id, **mutation)
        return {"ok": True, "flash_msg": summary.as_flash()}

    # ── retry-task (queue) ───────────────────────────────────────────────

    def retry_task(self, task_id: str) -> dict[str, Any]:
        """Reset a queue task to pending for retry."""
        if not task_id:
            return {"ok": False, "error": "Missing task_id"}
        _queue_store.update_task(task_id, {"status": "pending", "error": None, "next_retry_at": None})
        return {"ok": True, "message": "任务已重置为待发布状态"}
