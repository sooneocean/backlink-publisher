from __future__ import annotations
from typing import Literal

VALID_STATES = Literal["absent", "attempting", "done", "failed", "uncertain"]
GateVerdict = Literal["dispatch", "skip", "hold", "conflict"]

def validate_transition(current: str, next_state: str) -> bool:
    """Validates state machine transitions."""
    transitions = {
        "absent": ["attempting"],
        "attempting": ["done", "failed", "uncertain"],
        "done": [],
        "failed": ["attempting"],
        "uncertain": ["done", "failed"]
    }
    return next_state in transitions.get(current, [])

def get_gate_verdict(current_state: str, is_stale: bool, force: bool = False) -> GateVerdict:
    """Decides the action based on the current state of the dedup record."""
    if current_state == "done":
        return "skip"
    if current_state == "uncertain":
        return "hold"
    if current_state == "attempting":
        if is_stale and force:
            return "dispatch"
        if is_stale:
            return "hold" # Requires adjudication
        return "hold"
    if current_state == "failed":
        return "dispatch"
    if current_state == "absent":
        return "dispatch"
    return "hold"
