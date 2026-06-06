"""Schedule decision engine — decoupled from APScheduler for testability.

Extracted from ``webui_app.helpers.contexts._calc_next_available`` so the
min-interval + jitter policy can be exercised from CLI tests without booting
Flask. Pure function.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol


class _Store(Protocol):
    def load(self) -> list[dict]: ...


def calc_next_available(
    requested_dt: datetime,
    *,
    load_drafts: Callable[[], list[dict]],
    load_history: Callable[[], list[dict]],
    min_interval_hours: int = 4,
    jitter_minutes: int = 30,
) -> datetime:
    """Return the earliest publish time that respects min-interval + jitter.

    Scans ``drafts_store`` for published/scheduled entries and
    ``history_store`` for drafted/published rows, picks the latest
    timestamp, and pushes it forward by ``min_interval_hours`` +
    uniform ``jitter_minutes``. Returns ``requested_dt`` unchanged
    when there is no prior publication history.
    """
    last_published: datetime | None = None

    for item in load_drafts():
        if item.get("status") in ("published", "scheduled"):
            ts = item.get("published_at") or item.get("scheduled_at")
            if ts:
                try:
                    dt = (
                        datetime.fromisoformat(ts)
                        if "T" in ts
                        else datetime.strptime(ts, "%Y-%m-%d %H:%M")
                    )
                    if last_published is None or dt > last_published:
                        last_published = dt
                except ValueError:
                    pass

    for item in load_history():
        ts = item.get("created_at")
        if ts and item.get("status") in ("drafted", "published"):
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
                if last_published is None or dt > last_published:
                    last_published = dt
            except ValueError:
                pass

    if last_published is None:
        return requested_dt
    earliest = last_published + timedelta(hours=min_interval_hours)
    if jitter_minutes > 0:
        earliest += timedelta(minutes=random.randint(0, jitter_minutes))
    return max(requested_dt, earliest)
