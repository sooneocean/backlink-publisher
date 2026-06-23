"""Automation state-authority contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from backlink_publisher.automation._state import (
    AutomationPipelineState,
    get_current_state,
    set_current_state,
)
from webui_store.queue_store import QueueStore


def test_automation_state_is_run_local_metrics_only():
    state = AutomationPipelineState()
    state.planned_count = 3
    state.validated_count = 2
    state.published_count = 1
    state.mark_stage_completed("plan")
    state.record_recovery({"action": "rebind_task_created", "platform": "velog"})

    set_current_state(state)
    try:
        current = get_current_state()
        assert current is state
        assert current.stages_completed == ["plan"]
        assert current.recovery_actions == [
            {"action": "rebind_task_created", "platform": "velog"}
        ]
    finally:
        set_current_state(None)

    assert get_current_state() is None


def test_queue_blocked_task_is_not_runnable_or_publish_success(tmp_path):
    store = QueueStore(tmp_path / "queue.json", default_factory=list)
    store.save([
        {"id": "pending-1", "status": "pending"},
        {"id": "blocked-1", "status": "blocked", "reason": "quality_blocked"},
        {"id": "success-1", "status": "success", "published_url": "https://x.example/p"},
    ])

    runnable = store.get_runnable()

    assert [task["id"] for task in runnable] == ["pending-1"]
    assert not any(task["id"] == "blocked-1" for task in runnable)


def test_queue_failed_task_waits_until_retry_time(tmp_path):
    store = QueueStore(tmp_path / "queue.json", default_factory=list)
    future = (datetime.now() + timedelta(hours=1)).isoformat()
    past = (datetime.now() - timedelta(minutes=1)).isoformat()
    store.save([
        {"id": "future-failed", "status": "failed", "next_retry_at": future},
        {"id": "past-failed", "status": "failed", "next_retry_at": past},
    ])

    runnable = store.get_runnable()

    assert [task["id"] for task in runnable] == ["past-failed"]


def test_failed_task_without_retry_time_is_not_runnable(tmp_path):
    """A permanently-failed task (non-429, next_retry_at=None) must not be
    returned by get_runnable().

    Regression: ``not t.get("next_retry_at")`` evaluated True for None, so
    every permanently-failed task was re-run every minute by the queue
    processor until the operator manually retried it.
    """
    store = QueueStore(tmp_path / "queue.json", default_factory=list)
    store.save([
        {"id": "perm-failed", "status": "failed", "next_retry_at": None},
    ])

    runnable = store.get_runnable()

    assert runnable == [], (
        f"permanently-failed task (next_retry_at=None) must not be runnable: {runnable}"
    )
