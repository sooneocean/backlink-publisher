"""Auto-recovery workflows — self-healing actions for automation.

Handles:
- Channel re-bind recommendations
- Target re-publish for dead backlinks
- Platform quarantine lift on recovery
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher.canary.store import is_quarantined, QUARANTINE_AFTER_N

_log = logging.getLogger("auto-recovery")


def recommend_channel_rebind(platform: str, error_code: str) -> dict[str, Any]:
    """Create a rebind recommendation for an expired channel.

    Returns action dict for queue storage and operator notification.
    """
    return {
        "action": "bind_required",
        "platform": platform,
        "error_code": error_code,
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    }


def create_rebind_task(platform: str, error_code: str) -> str:
    """Create a rebind task in the queue store.

    Returns the task ID.
    """
    from webui_store import queue_store

    task = recommend_channel_rebind(platform, error_code)
    task_id = f"rebind-{platform}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    current = queue_store.load() or []
    current.append({
        "id": task_id,
        "type": "rebind",
        "platform": platform,
        **task,
    })
    queue_store.save(current)

    return task_id


def recommend_requeue_dead_targets(targets: list[str], platform: str) -> list[dict[str, Any]]:
    """Create requeue recommendations for dead backlinks.

    Uses plan-gap to fan out deficit publishing per plan.
    """
    recommendations = []
    for target in targets:
        recommendations.append({
            "action": "republish_required",
            "platform": platform,
            "target_url": target,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
        })
    return recommendations


def process_recheck_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Process recheck verdict signals and generate recovery actions.

    Args:
        signals: List of recheck verdict signal dicts.

    Returns:
        List of recovery action dicts.
    """
    actions: list[dict[str, Any]] = []

    for signal in signals:
        signal_type = signal.get("signal_type", "")
        platform = signal.get("platform", "")
        payload = signal.get("payload", {})

        if signal_type.endswith("host_gone"):
            actions.append({
                "action": "requeue_target",
                "platform": platform,
                "target_url": payload.get("target_url"),
                "live_url": payload.get("live_url"),
                "reason": "host_gone",
            })
        elif signal_type.endswith("link_stripped"):
            actions.append({
                "action": "requeue_target",
                "platform": platform,
                "target_url": payload.get("target_url"),
                "live_url": payload.get("live_url"),
                "reason": "link_stripped",
            })

    return actions


def run_recovery(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Execute recovery actions based on incoming signals.

    This is the main entry point for the recovery engine.
    """
    actions: list[dict[str, Any]] = []

    for signal in signals:
        signal_type = signal.get("signal_type", "")

        if signal_type == "canary_status.drift-confirmed":
            actions.extend(_handle_drift_signal(signal))
        elif signal_type == "channel_status.expired":
            actions.extend(_handle_expired_signal(signal))

    return actions


def _handle_drift_signal(signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle drift-confirmed signal."""
    platform = signal.get("platform", "")
    payload = signal.get("payload", {})

    action = {
        "action": "platform_quarantine_recommended",
        "platform": platform,
        "consecutive": payload.get("consecutive", 0),
        "ts_utc": signal.get("ts_utc", ""),
    }

    return [action]


def _handle_expired_signal(signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Handle channel expired signal."""
    platform = signal.get("platform", "")
    payload = signal.get("payload", {})

    # Create rebind task
    task_id = create_rebind_task(platform, payload.get("error_code", "unknown"))

    action = {
        "action": "rebind_task_created",
        "platform": platform,
        "task_id": task_id,
        "error_code": payload.get("error_code", "unknown"),
        "ts_utc": signal.get("ts_utc", ""),
    }

    return [action]