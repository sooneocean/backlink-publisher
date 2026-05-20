"""Daily + per-run image-gen caps + auto-disable safety.

Plan 2026-05-20-001 Unit 3.

Events flow into the existing ``events.EventStore`` (SQLite under
``<cache_dir>/events.db``).  Three free-form kinds:

  * ``image_gen_invoked`` — one row per successful generation.
    payload = ``{"prompt_sha": "...", ...}``.
  * ``image_gen_capped`` — one row per skipped generation due to
    cap.  payload = ``{"reason": "daily_cap"|"per_run_cap"}``.
  * ``image_gen_disabled_auto`` — one row per auto-disable trip.
    payload = ``{"threshold": N}``.

No projector / event-registry changes are required; ``EventStore``
treats ``kind`` as a free-form string.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from backlink_publisher.config import ImageGenConfig
from backlink_publisher.events.store import EventStore


@dataclass(frozen=True)
class CapDecision:
    """Result of ``check_caps``.

    ``allowed`` is True when both caps still have headroom; False
    indicates which cap blocked via ``reason`` (``"daily_cap"`` or
    ``"per_run_cap"``).
    """

    allowed: bool
    reason: str | None


def _today_utc_date_str() -> str:
    """``YYYY-MM-DD`` for today UTC.

    Indirected so tests can pin without monkeypatching datetime
    globally.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _count_today(store: EventStore) -> int:
    """How many ``image_gen_invoked`` events were recorded today.

    ``image_gen_capped`` rows are deliberately NOT counted — they
    represent skipped attempts, not consumed quota.
    """
    today = _today_utc_date_str()
    rows = store.query(
        "SELECT COUNT(*) AS n FROM events "
        "WHERE kind = 'image_gen_invoked' AND substr(ts_utc, 1, 10) = ?",
        (today,),
    )
    if not rows:
        return 0
    return int(rows[0]["n"])


def check_caps(
    store: EventStore,
    config: ImageGenConfig,
    *,
    run_counter: int,
) -> CapDecision:
    """Decide whether the next generation is allowed.

    Per-run cap is evaluated first because it is the tighter of the
    two — if we are over the per-run budget, callers should treat
    this run as done regardless of daily headroom.
    """
    if run_counter >= config.per_run_cap:
        return CapDecision(allowed=False, reason="per_run_cap")

    if _count_today(store) >= config.daily_cap:
        return CapDecision(allowed=False, reason="daily_cap")

    return CapDecision(allowed=True, reason=None)


def record_invocation(store: EventStore, prompt_sha: str) -> None:
    """Persist an ``image_gen_invoked`` event."""
    store.append("image_gen_invoked", {"prompt_sha": prompt_sha})


def record_cap_hit(store: EventStore, reason: str) -> None:
    """Persist an ``image_gen_capped`` event."""
    store.append("image_gen_capped", {"reason": reason})


def record_auto_disable(store: EventStore, threshold: int) -> None:
    """Persist an ``image_gen_disabled_auto`` event."""
    store.append("image_gen_disabled_auto", {"threshold": threshold})


class AutoDisableTracker:
    """Counts consecutive image-gen failures and trips at threshold.

    Used by plan-backlinks's per-run loop to defuse a key-revocation
    scenario where every call returns 401 — without this guard,
    operators discover the broken key only after burning through
    the entire daily cap on retries.

    A single success ``record_success()`` resets the counter to 0.
    The tracker is in-process state (one per ``plan-backlinks``
    invocation); cross-process auto-disable is intentionally out of
    scope.
    """

    def __init__(self, *, threshold: int) -> None:
        if threshold < 1:
            raise ValueError(
                f"AutoDisableTracker threshold must be >= 1, got {threshold!r}"
            )
        self.threshold = threshold
        self._consecutive = 0
        self.disabled = False

    def record_failure(self) -> None:
        self._consecutive += 1
        if self._consecutive >= self.threshold:
            self.disabled = True

    def record_success(self) -> None:
        self._consecutive = 0
