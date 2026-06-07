"""Watchdog service — monitors canary health and triggers recovery workflows.

Monitors the canary-health.json store and emits health signals when
thresholds are crossed. Implements debouncing to prevent false positives.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher.canary.store import (
    QUARANTINE_AFTER_N,
    REARM_AFTER_M,
    list_all,
    read_canary_config,
)

from .signals import (
    HealthSignal,
    make_canary_drift_signal,
    make_channel_expired_signal,
    SIGNAL_CHANNEL_EXPIRED,
)

_log = logging.getLogger("watchdog")


# Alert cooldown to prevent flooding
_COOLDOWN_HOURS = 24
_last_alert_ts: dict[str, datetime] = {}


def check_canary_health() -> list[HealthSignal]:
    """Check canary health and emit signals for threshold crossings.

    Returns list of HealthSignal objects for any triggered conditions.
    """
    signals: list[HealthSignal] = []

    for platform, rec in (list_all() or {}).items():
        status = rec.get("status", "not-configured")
        consecutive_failures = int(rec.get("consecutive_failures", 0) or 0)
        consecutive_oks = int(rec.get("consecutive_oks", 0) or 0)
        quarantined = bool(rec.get("quarantined", False))

        # Check for quarantine trigger (drift confirmed)
        if status == "drift-confirmed" and consecutive_failures >= QUARANTINE_AFTER_N:
            if not quarantined:
                signal = make_canary_drift_signal(
                    platform=platform,
                    consecutive=consecutive_failures,
                )
                signals.append(signal)
                _log.recon("watchdog_drift_detected",
                           platform=platform, consecutive=consecutive_failures)

        # Check for re-arm opportunity (platform recovered)
        if quarantined and consecutive_oks >= REARM_AFTER_M and status == "link-alive":
            _log.recon("watchdog_rearm_available",
                       platform=platform, consecutive_oks=consecutive_oks)

    return signals


def check_channel_status() -> list[HealthSignal]:
    """Check channel binding status and emit expired signals.

    Returns list of HealthSignal objects for expired channels.
    """
    signals: list[HealthSignal] = []

    from webui_store.channel_status import channel_status_store

    status = channel_status_store.load() or {}
    for channel, rec in (status.items() if status else []):
        if rec.get("status") == "expired":
            signal = make_channel_expired_signal(
                platform=channel,
                error_code=rec.get("error_code", "unknown"),
            )
            signals.append(signal)
            _log.recon("watchdog_channel_expired", channel=channel)

    return signals


def run_watch_cycle(input_stream: Any = None) -> int:
    """Run one watchdog cycle, emitting signals to stdout.

    Args:
        input_stream: Ignored (for compatibility with scheduler pattern).

    Returns:
        Number of signals emitted.
    """
    signals = check_canary_health() + check_channel_status()

    if signals:
        for signal in signals:
            print(json.dumps(signal.to_jsonl_dict(), ensure_ascii=False))

        _log.recon(
            "watchdog_cycle_complete",
            signals_emitted=len(signals),
        )

    return len(signals)


def should_alert(platform: str, now: datetime | None = None) -> bool:
    """Check if an alert should be sent (cooldown-aware).

    Args:
        platform: Platform to check.
        now: Current time (defaults to UTC now).

    Returns:
        True if alert should be sent, False if in cooldown.
    """
    now = now or datetime.now(timezone.utc)
    last = _last_alert_ts.get(platform)
    if last is None:
        _last_alert_ts[platform] = now
        return True

    hours_since = (now - last).total_seconds() / 3600
    if hours_since >= _COOLDOWN_HOURS:
        _last_alert_ts[platform] = now
        return True

    return False


def clear_alert_timestamps() -> None:
    """Clear alert timestamps (for testing)."""
    _last_alert_ts.clear()