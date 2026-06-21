"""Channel health registry and routing for the survival-rate optimisation loop.

Provides the read-side ``ChannelHealthRegistry`` (aggregation over events.db),
write helpers for channel-health events, and ``HealthRouter`` for dead-backlink
routing decisions.
"""

from __future__ import annotations

from backlink_publisher.health.registry import (
    ChannelHealth,
    ChannelHealthRegistry,
    write_published_to_event,
    write_recheck_observed,
    write_routed_event,
)
from backlink_publisher.health.router import HealthRouter, RoutingDecision

__all__ = [
    "ChannelHealth",
    "ChannelHealthRegistry",
    "HealthRouter",
    "RoutingDecision",
    "write_published_to_event",
    "write_recheck_observed",
    "write_routed_event",
]
