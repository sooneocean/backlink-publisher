from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.health.registry import (
    ChannelHealthRegistry,
    write_recheck_observed,
)
from backlink_publisher.health.router import (
    HealthRouter,
    MAX_CONSECUTIVE_FAILURES,
    RoutingDecision,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _store() -> EventStore:
    return EventStore()


def _seed_channel_health(store: EventStore, channel: str, alive: int, dead: int) -> None:
    """Seed recheck events for a channel with given alive/dead counts."""
    i = 0
    for _ in range(alive):
        write_recheck_observed(
            store, verdict="alive", platform=channel,
            live_url=f"https://ex.com/{i}", target_url=f"https://t.com/{i}",
        )
        i += 1
    for _ in range(dead):
        write_recheck_observed(
            store, verdict="host_gone", platform=channel,
            live_url=f"https://ex.com/{i}", target_url=f"https://t.com/{i}",
        )
        i += 1


def _dead_event(live_url: str = "https://ex.com/dead", target_url: str = "https://t.com/x",
                platform: str = "medium") -> dict:
    return {"live_url": live_url, "target_url": target_url, "platform": platform, "verdict": "link_stripped"}


# ── route: no_change_needed ──────────────────────────────────────────────────


def test_route_healthy_channel_stays():
    s = _store()
    _seed_channel_health(s, "medium", alive=9, dead=1)  # 90% survival >= 0.7
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([_dead_event(platform="medium")])

    assert len(decisions) == 1
    d = decisions[0]
    assert d.assigned_channel == "medium"
    assert d.reason == "no_change_needed"
    assert d.source_survival_rate == pytest.approx(0.9)


def test_route_no_data_channel_stays():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([_dead_event(platform="medium")])

    assert len(decisions) == 1
    d = decisions[0]
    assert d.assigned_channel == "medium"
    assert d.reason == "no_change_needed"
    assert d.source_survival_rate is None


def test_route_no_platform_returns_unknown():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([{"live_url": "https://ex.com/a", "target_url": "https://t.com/x"}])

    assert len(decisions) == 1
    d = decisions[0]
    assert d.assigned_channel == "unknown"
    assert d.reason == "channel_unavailable"
    assert d.original_platform is None


# ── route: routing to better channel ─────────────────────────────────────────


def test_route_below_threshold_routes_to_healthiest():
    s = _store()
    _seed_channel_health(s, "medium", alive=2, dead=8)   # 20% — below threshold
    _seed_channel_health(s, "blogger", alive=9, dead=1)  # 90% — best available
    _seed_channel_health(s, "velog", alive=7, dead=3)    # 70% — good but not best
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([_dead_event(platform="medium")])

    assert len(decisions) == 1
    d = decisions[0]
    assert d.assigned_channel == "blogger"  # healthiest
    assert d.reason == "survival_rate_below_threshold"
    assert d.original_platform == "medium"
    assert d.target_survival_rate == pytest.approx(0.9)


def test_route_below_threshold_no_available():
    s = _store()
    _seed_channel_health(s, "medium", alive=1, dead=9)  # 10% — below threshold
    # No other channel with data
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([_dead_event(platform="medium")])

    assert len(decisions) == 1
    d = decisions[0]
    assert d.assigned_channel == "medium"
    assert d.reason == "no_available_channel"


def test_route_exclude_channels():
    s = _store()
    _seed_channel_health(s, "medium", alive=2, dead=8)   # 20%
    _seed_channel_health(s, "blogger", alive=9, dead=1)  # 90%
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    decisions = router.route([_dead_event(platform="medium")], exclude_channels={"blogger"})

    assert len(decisions) == 1
    d = decisions[0]
    # Blogger excluded, no other channel available
    assert d.assigned_channel == "medium"
    assert d.reason == "no_available_channel"


# ── route: multiple events ────────────────────────────────────────────────────


def test_route_multiple_events():
    s = _store()
    _seed_channel_health(s, "medium", alive=1, dead=9)   # 10%
    _seed_channel_health(s, "blogger", alive=8, dead=2)  # 80%
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    events = [
        _dead_event(live_url="https://ex.com/1", platform="medium"),
        _dead_event(live_url="https://ex.com/2", platform="medium"),
    ]
    decisions = router.route(events)

    assert len(decisions) == 2
    for d in decisions:
        assert d.assigned_channel == "blogger"
        assert d.reason == "survival_rate_below_threshold"


# ── routing decision dataclass ────────────────────────────────────────────────


def test_routing_decision_fields():
    d = RoutingDecision(
        dead_live_url="https://ex.com/a",
        target_url="https://t.com/x",
        original_platform="medium",
        assigned_channel="blogger",
        source_survival_rate=0.3,
        target_survival_rate=0.9,
        reason="survival_rate_below_threshold",
    )
    assert d.dead_live_url == "https://ex.com/a"
    assert d.target_url == "https://t.com/x"
    assert d.original_platform == "medium"
    assert d.assigned_channel == "blogger"


# ── failure backoff ──────────────────────────────────────────────────────────


def test_record_failure_tracks_consecutive():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    assert router._consecutive_failures.get("medium", 0) == 0

    router.record_failure("medium")
    assert router._consecutive_failures["medium"] == 1

    router.record_failure("medium")
    assert router._consecutive_failures["medium"] == 2


def test_in_backoff_before_threshold():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    router._consecutive_failures["medium"] = MAX_CONSECUTIVE_FAILURES - 1
    assert router._in_backoff("medium") is False


def test_in_backoff_at_threshold():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    router._consecutive_failures["medium"] = MAX_CONSECUTIVE_FAILURES
    router._failure_timestamps["medium"] = datetime.now(timezone.utc)

    assert router._in_backoff("medium") is True


def test_reset_failures():
    s = _store()
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    router.record_failure("medium")
    router.record_failure("medium")
    assert router._consecutive_failures.get("medium", 0) == 2

    router.reset_failures("medium")
    assert router._consecutive_failures.get("medium", 0) == 0
    assert "medium" not in router._failure_timestamps


def test_channel_in_backoff_excluded_from_routing():
    s = _store()
    _seed_channel_health(s, "medium", alive=1, dead=9)   # 10%
    _seed_channel_health(s, "blogger", alive=9, dead=1)  # 90%
    _seed_channel_health(s, "velog", alive=8, dead=2)    # 80%
    reg = ChannelHealthRegistry(s)
    router = HealthRouter(reg)

    # Put velog in backoff
    router._consecutive_failures["velog"] = MAX_CONSECUTIVE_FAILURES
    router._failure_timestamps["velog"] = datetime.now(timezone.utc)

    # Medium is below threshold, should route to blogger (healthiest not in backoff)
    decisions = router.route([_dead_event(platform="medium")])

    assert decisions[0].assigned_channel == "blogger"


# ── threshold config ─────────────────────────────────────────────────────────


def test_custom_threshold():
    s = _store()
    _seed_channel_health(s, "medium", alive=8, dead=2)  # 80% — above default 0.7
    _seed_channel_health(s, "blogger", alive=9, dead=1) # 90%
    reg = ChannelHealthRegistry(s)

    # With a stricter 0.85 threshold, medium is now below it
    router = HealthRouter(reg, threshold=0.85)

    decisions = router.route([_dead_event(platform="medium")])
    assert decisions[0].assigned_channel == "blogger"
    assert decisions[0].reason == "survival_rate_below_threshold"
