"""Pipeline state tracking for automation runs.

Tracks stage execution, checkpointing, and recovery state for the
auto-publish orchestrator. Follows the existing checkpoint pattern but
scoped to automation workflows.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backlink_publisher.checkpoint import create_checkpoint


@dataclass
class AutomationPipelineState:
    """State for a single automation run.

    Tracks which stages have executed and their outcomes for potential
    recovery/retry scenarios.
    """

    run_id: str | None = None
    stages_completed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None

    # Metrics for the dashboard
    planned_count: int = 0
    validated_count: int = 0
    published_count: int = 0
    rechecked_count: int = 0
    recovery_actions: list[dict[str, Any]] = field(default_factory=list)

    def mark_stage_completed(self, stage: str) -> None:
        """Record a successfully completed stage."""
        if stage not in self.stages_completed:
            self.stages_completed.append(stage)

    def mark_stage_failed(self, stage: str) -> None:
        """Record a failed stage."""
        if stage not in self.stages_failed:
            self.stages_failed.append(stage)

    def increment_retry(self, stage: str) -> int:
        """Increment retry count for a stage, return new count."""
        self.retry_counts[stage] = self.retry_counts.get(stage, 0) + 1
        return self.retry_counts[stage]

    def is_stage_done(self, stage: str) -> bool:
        """Check if a stage has already succeeded."""
        return stage in self.stages_completed

    def record_recovery(self, action: dict[str, Any]) -> None:
        """Add a recovery action to the log."""
        self.recovery_actions.append(action)


# Thread-local storage for the current automation run
_state_lock = threading.Lock()
_current_state: AutomationPipelineState | None = None


def get_current_state() -> AutomationPipelineState | None:
    """Get the thread-local automation state."""
    with _state_lock:
        return _current_state


def set_current_state(state: AutomationPipelineState | None) -> None:
    """Set the thread-local automation state."""
    with _state_lock:
        global _current_state
        _current_state = state