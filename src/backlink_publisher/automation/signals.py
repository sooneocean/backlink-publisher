"""Health signal types for automation orchestration.

Defines the signal vocabulary that the watchdog monitors and recovery
workflows consume. Mirrors the canary store status values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


# Signal type constants
SIGNAL_CANARY_DRIFT = "canary_status.drift-confirmed"
SIGNAL_CANARY_ALIVE = "canary_status.link-alive"
SIGNAL_CHANNEL_EXPIRED = "channel_status.expired"
SIGNAL_RECHECK_HOST_GONE = "recheck.verdict.host_gone"
SIGNAL_RECHECK_LINK_STRIPPED = "recheck.verdict.link_stripped"
SIGNAL_RECHECK_DOFOLLOW_LOST = "recheck.verdict.dofollow_lost"


@dataclass(frozen=True)
class HealthSignal:
    """A health signal emitted by monitoring or recheck processes."""

    signal_type: str
    platform: str
    payload: dict[str, Any]
    ts_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_jsonl_dict(self) -> dict[str, Any]:
        """Serialize to JSONL-compatible dict."""
        return {
            "signal_type": self.signal_type,
            "platform": self.platform,
            "payload": self.payload,
            "ts_utc": self.ts_utc,
        }


def make_canary_drift_signal(platform: str, consecutive: int, **extra: Any) -> HealthSignal:
    """Create a canary drift-confirmed signal."""
    return HealthSignal(
        signal_type=SIGNAL_CANARY_DRIFT,
        platform=platform,
        payload={"consecutive": consecutive, **extra},
    )


def make_channel_expired_signal(platform: str, error_code: str, **extra: Any) -> HealthSignal:
    """Create a channel expired signal for rebind recommendation."""
    return HealthSignal(
        signal_type=SIGNAL_CHANNEL_EXPIRED,
        platform=platform,
        payload={"error_code": error_code, **extra},
    )


def make_recheck_signal(
    platform: str,
    verdict: str,
    live_url: str,
    target_url: str,
    **extra: Any,
) -> HealthSignal:
    """Create a recheck verdict signal."""
    signal_type = f"recheck.verdict.{verdict}"
    return HealthSignal(
        signal_type=signal_type,
        platform=platform,
        payload={
            "live_url": live_url,
            "target_url": target_url,
            **extra,
        },
    )
