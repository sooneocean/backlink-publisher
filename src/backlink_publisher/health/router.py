from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backlink_publisher.health.registry import ChannelHealth, ChannelHealthRegistry

DEFAULT_THRESHOLD: float = 0.7
MIN_SURVIVAL_RATE: float = 0.1
CONSECUTIVE_FAILURE_BACKOFF_HOURS: int = 24
MAX_CONSECUTIVE_FAILURES: int = 3


@dataclass(frozen=True)
class RoutingDecision:
    """Result of routing a single dead backlink to a publishing channel."""

    dead_live_url: str
    target_url: str
    original_platform: str | None
    assigned_channel: str
    source_survival_rate: float | None
    target_survival_rate: float | None
    reason: str


class HealthRouter:
    """Decides which channel a dead backlink should be re-published through.

    V1 strategy: compare original channel's survival_rate against threshold.
    If below threshold, pick the healthiest available channel.
    """

    def __init__(
        self,
        registry: ChannelHealthRegistry,
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._registry = registry
        self._threshold = threshold
        self._consecutive_failures: dict[str, int] = {}
        self._failure_timestamps: dict[str, datetime] = {}

    def route(
        self,
        dead_events: list[dict[str, Any]],
        *,
        exclude_channels: set[str] | None = None,
    ) -> list[RoutingDecision]:
        decisions: list[RoutingDecision] = []
        for event in dead_events:
            decision = self._route_one(event, exclude_channels=exclude_channels)
            decisions.append(decision)
        return decisions

    def _route_one(
        self,
        event: dict[str, Any],
        *,
        exclude_channels: set[str] | None = None,
    ) -> RoutingDecision:
        dead_live_url: str = event.get("live_url", "")
        target_url: str = event.get("target_url", "")
        original: str | None = event.get("platform")

        if not original:
            return RoutingDecision(
                dead_live_url=dead_live_url,
                target_url=target_url,
                original_platform=None,
                assigned_channel="unknown",
                source_survival_rate=None,
                target_survival_rate=None,
                reason="channel_unavailable",
            )

        health = self._registry.get_health(original, window_days=30)

        # Check consecutive failure backoff.
        if self._in_backoff(original):
            # Since original is backed off, find the best available alternative.
            best = self._pick_best_available(exclude_channels)
            if best is not None:
                return RoutingDecision(
                    dead_live_url=dead_live_url,
                    target_url=target_url,
                    original_platform=original,
                    assigned_channel=best[0],
                    source_survival_rate=health.survival_rate,
                    target_survival_rate=best[1],
                    reason="survival_rate_below_threshold",
                )
            return RoutingDecision(
                dead_live_url=dead_live_url,
                target_url=target_url,
                original_platform=original,
                assigned_channel=original,
                source_survival_rate=health.survival_rate,
                target_survival_rate=health.survival_rate,
                reason="no_available_channel",
            )

        # No data or healthy enough -> keep original.
        if not health.has_data or health.survival_rate is None:
            return RoutingDecision(
                dead_live_url=dead_live_url,
                target_url=target_url,
                original_platform=original,
                assigned_channel=original,
                source_survival_rate=health.survival_rate,
                target_survival_rate=health.survival_rate,
                reason="no_change_needed",
            )

        if health.survival_rate >= self._threshold:
            return RoutingDecision(
                dead_live_url=dead_live_url,
                target_url=target_url,
                original_platform=original,
                assigned_channel=original,
                source_survival_rate=health.survival_rate,
                target_survival_rate=health.survival_rate,
                reason="no_change_needed",
            )

        # Original channel is below threshold. Try to route elsewhere.
        exclude = set(exclude_channels or ()) | {original}
        best = self._pick_best_available(exclude)
        if best is not None:
            return RoutingDecision(
                dead_live_url=dead_live_url,
                target_url=target_url,
                original_platform=original,
                assigned_channel=best[0],
                source_survival_rate=health.survival_rate,
                target_survival_rate=best[1],
                reason="survival_rate_below_threshold",
            )

        return RoutingDecision(
            dead_live_url=dead_live_url,
            target_url=target_url,
            original_platform=original,
            assigned_channel=original,
            source_survival_rate=health.survival_rate,
            target_survival_rate=health.survival_rate,
            reason="no_available_channel",
        )

    def _pick_best_available(
        self,
        exclude_channels: set[str] | None = None,
    ) -> tuple[str, float] | None:
        exclude = set(exclude_channels or ())
        all_health = self._registry.get_all_health(window_days=30)
        candidates: list[tuple[float, str, ChannelHealth]] = []
        for ch, health in all_health.items():
            if ch in exclude:
                continue
            if not health.has_data:
                continue
            if health.survival_rate is None:
                continue
            if health.survival_rate < MIN_SURVIVAL_RATE:
                continue
            if self._in_backoff(ch):
                continue
            candidates.append((health.survival_rate, ch, health))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return (candidates[0][1], candidates[0][0])

    def record_failure(self, channel: str) -> None:
        self._consecutive_failures[channel] = (
            self._consecutive_failures.get(channel, 0) + 1
        )
        self._failure_timestamps[channel] = datetime.now(timezone.utc)

    def reset_failures(self, channel: str) -> None:
        self._consecutive_failures.pop(channel, None)
        self._failure_timestamps.pop(channel, None)

    def _in_backoff(self, channel: str) -> bool:
        count = self._consecutive_failures.get(channel, 0)
        if count < MAX_CONSECUTIVE_FAILURES:
            return False
        last_fail = self._failure_timestamps.get(channel)
        if last_fail is None:
            return False
        elapsed = datetime.now(timezone.utc) - last_fail
        return elapsed.total_seconds() < (
            CONSECUTIVE_FAILURE_BACKOFF_HOURS * 3600
        )
