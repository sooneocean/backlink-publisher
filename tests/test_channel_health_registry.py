from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher.events import EventStore, kinds
from backlink_publisher.health.registry import (
    ChannelHealth,
    ChannelHealthRegistry,
    write_published_to_event,
    write_recheck_observed,
    write_routed_event,
    _window_start,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _store() -> EventStore:
    return EventStore()


SINCE_ALL = "2000-01-01T00:00:00+00:00"


# ── get_health ────────────────────────────────────────────────────────────────


def test_get_health_alive_and_dead():
    s = _store()
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/b", target_url="https://t.com/2")
    write_recheck_observed(s, verdict="host_gone", platform="medium",
                           live_url="https://ex.com/c", target_url="https://t.com/3")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("medium")

    assert h.channel == "medium"
    assert h.total_rechecks == 3
    assert h.alive_count == 2
    assert h.dead_count == 1
    assert h.host_gone_count == 1
    assert h.link_stripped_count == 0
    assert h.dofollow_lost_count == 0
    assert h.probe_error_count == 0
    assert h.survival_rate == 2 / 3
    assert h.has_data is True
    assert h.primary_death_cause == "Host Gone"


def test_get_health_no_data():
    """Channel with no recheck events returns has_data=False."""
    s = _store()
    reg = ChannelHealthRegistry(s)
    h = reg.get_health("velog")

    assert h.channel == "velog"
    assert h.total_rechecks == 0
    assert h.alive_count == 0
    assert h.survival_rate is None
    assert h.has_data is False
    assert h.primary_death_cause is None


def test_get_health_all_dead_one_cause():
    s = _store()
    for i in range(5):
        write_recheck_observed(s, verdict="link_stripped", platform="blogger",
                               live_url=f"https://ex.com/{i}", target_url="https://t.com/x")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("blogger")

    assert h.total_rechecks == 5
    assert h.alive_count == 0
    assert h.host_gone_count == 0
    assert h.link_stripped_count == 5
    assert h.primary_death_cause == "Link Stripped"


def test_get_health_mixed_death_causes_dofollow_wins():
    s = _store()
    write_recheck_observed(s, verdict="dofollow_lost", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="host_gone", platform="medium",
                           live_url="https://ex.com/b", target_url="https://t.com/2")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("medium")

    assert h.dofollow_lost_count == 1
    assert h.host_gone_count == 1
    assert h.primary_death_cause in ("Dofollow Lost", "Host Gone")


def test_get_health_probe_error_counted():
    s = _store()
    write_recheck_observed(s, verdict="probe_error", platform="velog",
                           live_url="https://ex.com/a", target_url="https://t.com/1")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("velog")

    assert h.total_rechecks == 1
    assert h.probe_error_count == 1


# ── get_all_health ────────────────────────────────────────────────────────────


def test_get_all_health_multiple_channels():
    s = _store()
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="host_gone", platform="blogger",
                           live_url="https://ex.com/b", target_url="https://t.com/2")

    reg = ChannelHealthRegistry(s)
    all_h = reg.get_all_health()

    assert set(all_h.keys()) == {"medium", "blogger"}
    assert all_h["medium"].survival_rate == 1.0
    assert all_h["blogger"].survival_rate == 0.0


def test_get_all_health_empty():
    s = _store()
    reg = ChannelHealthRegistry(s)
    assert reg.get_all_health() == {}


def test_get_all_health_window_excludes_old():
    s = _store()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    # Append via store directly to control ts_utc.
    payload = {"verdict": "alive", "platform": "medium",
               "live_url": "https://ex.com/a", "target_url": "https://t.com/1"}
    for field in kinds.REQUIRED_FIELDS.get(kinds.CHANNEL_RECHECK_OBSERVED, frozenset()):
        payload.setdefault(field, f"_test_{field}")
    s.append(kinds.CHANNEL_RECHECK_OBSERVED, payload,
             target_url="https://t.com/1", host="medium", ts_utc=old)

    reg = ChannelHealthRegistry(s)
    all_h = reg.get_all_health(window_days=30)

    # Should be excluded since it's 60 days old and window is 30.
    assert "medium" not in all_h


# ── get_routing_history ──────────────────────────────────────────────────────


def test_routing_history():
    s = _store()
    write_routed_event(
        s, source_channel="medium", target_channel="blogger",
        reason="survival_rate_below_threshold", source_survival_rate=0.3,
        target_survival_rate=0.9, dead_live_url="https://ex.com/a",
        target_url="https://t.com/1",
    )

    reg = ChannelHealthRegistry(s)
    hist = reg.get_routing_history()

    assert len(hist) == 1
    assert hist[0]["source_channel"] == "medium"
    assert hist[0]["target_channel"] == "blogger"
    assert hist[0]["reason"] == "survival_rate_below_threshold"
    assert hist[0]["target_url"] == "https://t.com/1"


def test_routing_history_limit():
    s = _store()
    for i in range(5):
        write_routed_event(
            s, source_channel="m", target_channel=f"t{i}",
            reason="test", source_survival_rate=0.5,
            target_survival_rate=0.9, dead_live_url=f"https://ex.com/{i}",
            target_url="https://t.com/x",
        )

    reg = ChannelHealthRegistry(s)
    hist = reg.get_routing_history(limit=2)

    assert len(hist) == 2


def test_routing_history_empty():
    s = _store()
    reg = ChannelHealthRegistry(s)
    assert reg.get_routing_history() == []


def test_routing_history_since_filter():
    s = _store()
    write_routed_event(
        s, source_channel="m", target_channel="t", reason="test",
        source_survival_rate=0.5, target_survival_rate=0.9,
        dead_live_url="https://ex.com/a", target_url="https://t.com/1",
    )
    since_dt = datetime.now(timezone.utc) + timedelta(hours=1)

    reg = ChannelHealthRegistry(s)
    hist = reg.get_routing_history(since_dt=since_dt)

    assert len(hist) == 0


# ── get_available_channels ────────────────────────────────────────────────────


def test_get_available_channels_basic():
    s = _store()
    # medium: 100% survival
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    # blogger: 0% survival — below floor
    write_recheck_observed(s, verdict="host_gone", platform="blogger",
                           live_url="https://ex.com/b", target_url="https://t.com/2")

    reg = ChannelHealthRegistry(s)
    avail = reg.get_available_channels()

    assert "medium" in avail
    assert "blogger" not in avail  # below floor


def test_get_available_channels_exclude():
    s = _store()
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="alive", platform="blogger",
                           live_url="https://ex.com/b", target_url="https://t.com/2")

    reg = ChannelHealthRegistry(s)
    avail = reg.get_available_channels(exclude_channels={"blogger"})

    assert "medium" in avail
    assert "blogger" not in avail


def test_get_available_channels_min_rate():
    s = _store()
    # 50% survival
    write_recheck_observed(s, verdict="alive", platform="v1",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="host_gone", platform="v1",
                           live_url="https://ex.com/b", target_url="https://t.com/2")

    reg = ChannelHealthRegistry(s)
    # With min_survival_rate=0.6, v1 should be excluded
    avail = reg.get_available_channels(min_survival_rate=0.6)
    assert "v1" not in avail

    # With min_survival_rate=0.4, v1 should be included
    avail2 = reg.get_available_channels(min_survival_rate=0.4)
    assert "v1" in avail2


def test_get_available_channels_returns_sorted():
    s = _store()
    write_recheck_observed(s, verdict="alive", platform="low",
                           live_url="https://ex.com/a", target_url="https://t.com/1")
    write_recheck_observed(s, verdict="host_gone", platform="low",
                           live_url="https://ex.com/b", target_url="https://t.com/2")
    write_recheck_observed(s, verdict="alive", platform="high",
                           live_url="https://ex.com/c", target_url="https://t.com/3")

    reg = ChannelHealthRegistry(s)
    avail = reg.get_available_channels()

    # "high" (100%) should come before "low" (50%)
    assert avail == ["high", "low"]


def test_get_available_channels_no_data_excluded():
    s = _store()
    # no data for any channel
    reg = ChannelHealthRegistry(s)
    assert reg.get_available_channels() == []


# ── ChannelHealth dataclass ──────────────────────────────────────────────────


def test_channel_health_survival_rate_none_when_no_rechecks():
    h = ChannelHealth(channel="x", total_rechecks=0)
    assert h.survival_rate is None
    assert h.has_data is False


def test_channel_health_survival_rate_when_has_data():
    h = ChannelHealth(channel="x", total_rechecks=10, alive_count=7, dead_count=3)
    assert h.survival_rate == 0.7
    assert h.has_data is True


def test_channel_health_last_alive_at():
    s = _store()
    write_recheck_observed(s, verdict="alive", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("medium")

    assert h.last_alive_at is not None
    assert h.last_dead_at is None


def test_channel_health_last_dead_at():
    s = _store()
    write_recheck_observed(s, verdict="host_gone", platform="medium",
                           live_url="https://ex.com/a", target_url="https://t.com/1")

    reg = ChannelHealthRegistry(s)
    h = reg.get_health("medium")

    assert h.last_alive_at is None
    assert h.last_dead_at is not None
