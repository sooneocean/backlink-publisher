"""Event substrate (read-side projection of JSON state files).

Public API:
    - ``EventStore`` (U1) — write/read access to ``events.db``
    - ``flush_for`` (U4) — project a JSON source into the event store
    - ``ProjectionError`` (U4) — cursor / dispatch failures
    - ``ProjectionResult`` (U4) — counters returned by ``flush_for``
"""

from .projector import ProjectionError, ProjectionResult, flush_for
from .store import EventStore

__all__ = [
    "EventStore",
    "ProjectionError",
    "ProjectionResult",
    "flush_for",
]
