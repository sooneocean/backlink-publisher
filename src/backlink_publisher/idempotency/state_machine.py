from __future__ import annotations
from typing import Literal

VALID_STATES = Literal["absent", "attempting", "done", "failed", "uncertain"]

def validate_transition(current: str, next_state: str) -> bool:
    """Validates state machine transitions."""
    transitions = {
        "absent": ["attempting"],
        "attempting": ["done", "failed", "uncertain"],
        "done": [],
        "failed": [],
        "uncertain": ["done", "failed"]
    }
    return next_state in transitions.get(current, [])
