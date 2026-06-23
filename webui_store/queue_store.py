"""Queue store — background publishing task persistence."""

from __future__ import annotations

from typing import Any

from .base import JsonStore


class QueueStore(JsonStore):
    """JsonStore specialised for task-queue semantics.

    Extends base load/save/update with task-level mutation helpers
    so callers don't have to spell out the read-modify-write pattern.
    """

    def update_task(self, task_id: str, updates: dict[str, Any]) -> None:
        def _apply(tasks: list[dict]) -> list[dict]:
            for t in tasks:
                if t.get("id") == task_id:
                    t.update(updates)
                    break
            return tasks

        self.update(_apply)

    def get_runnable(self) -> list[dict]:
        """Return pending tasks and failed-with-retry tasks whose retry time has passed.

        A ``failed`` task with ``next_retry_at=None`` is a permanent failure
        (non-429) and must NOT be returned — only the operator can re-queue it
        via ``retry_task()``.  The previous ``not t.get("next_retry_at")``
        condition evaluated True for None and caused permanent failures to be
        re-run every minute.
        """
        from datetime import datetime
        tasks = self.load()
        now = datetime.now()
        return [
            t for t in tasks
            if t.get("status") == "pending"
            or (
                t.get("status") == "failed"
                and t.get("next_retry_at")
                and datetime.fromisoformat(t["next_retry_at"]) <= now
            )
        ]
