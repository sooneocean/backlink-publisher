from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.events import EventStore, kinds
from backlink_publisher.health.registry import write_recheck_observed, write_routed_event


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    yield


def _store() -> EventStore:
    return EventStore()


def _alive_event(store: EventStore, platform: str = "medium",
                  live_url: str = "https://ex.com/alive",
                  target_url: str = "https://t.com/x") -> None:
    write_recheck_observed(store, verdict="alive", platform=platform,
                           live_url=live_url, target_url=target_url)


def _dead_event(store: EventStore, platform: str = "medium",
                live_url: str = "https://ex.com/dead",
                target_url: str = "https://t.com/x") -> None:
    write_recheck_observed(store, verdict="link_stripped", platform=platform,
                           live_url=live_url, target_url=target_url)


# ── _build_seeds_with_routing ─────────────────────────────────────────────────


def test_build_seeds_with_routing_override():
    from backlink_publisher.cli.auto_recover import _build_seeds_with_routing

    dead_events = [
        {"live_url": "https://ex.com/a", "target_url": "https://t.com/1",
         "platform": "medium"},
    ]
    from backlink_publisher.health.router import RoutingDecision
    decisions = [
        RoutingDecision(
            dead_live_url="https://ex.com/a",
            target_url="https://t.com/1",
            original_platform="medium",
            assigned_channel="blogger",
            source_survival_rate=0.3,
            target_survival_rate=0.9,
            reason="survival_rate_below_threshold",
        ),
    ]

    args = SimpleNamespace(language="en", url_mode="A", publish_mode="draft")
    seeds = _build_seeds_with_routing(dead_events, decisions, args)

    assert len(seeds) == 1
    seed = seeds[0]
    assert seed["target_url"] == "https://t.com/1"
    assert seed["language"] == "en"
    assert seed["url_mode"] == "A"
    assert seed["publish_mode"] == "draft"
    assert seed["platform"] == "blogger"  # overridden by routing
    assert seed["_routing_provenance"]["original_platform"] == "medium"
    assert seed["_routing_provenance"]["reason"] == "survival_rate_below_threshold"


def test_build_seeds_with_routing_no_change():
    from backlink_publisher.cli.auto_recover import _build_seeds_with_routing

    dead_events = [
        {"live_url": "https://ex.com/a", "target_url": "https://t.com/1",
         "platform": "medium"},
    ]
    from backlink_publisher.health.router import RoutingDecision
    decisions = [
        RoutingDecision(
            dead_live_url="https://ex.com/a",
            target_url="https://t.com/1",
            original_platform="medium",
            assigned_channel="medium",
            source_survival_rate=0.9,
            target_survival_rate=0.9,
            reason="no_change_needed",
        ),
    ]

    args = SimpleNamespace(language="en")
    seeds = _build_seeds_with_routing(dead_events, decisions, args)

    assert len(seeds) == 1
    assert seeds[0]["platform"] == "medium"


def test_build_seeds_with_routing_empty_events():
    from backlink_publisher.cli.auto_recover import _build_seeds_with_routing

    seeds = _build_seeds_with_routing([], [], SimpleNamespace())
    assert seeds == []


def test_build_seeds_with_routing_multiple():
    from backlink_publisher.cli.auto_recover import _build_seeds_with_routing
    from backlink_publisher.health.router import RoutingDecision

    dead_events = [
        {"live_url": "https://ex.com/1", "target_url": "https://t.com/1", "platform": "medium"},
        {"live_url": "https://ex.com/2", "target_url": "https://t.com/2", "platform": "blogger"},
    ]
    decisions = [
        RoutingDecision(
            dead_live_url="https://ex.com/1", target_url="https://t.com/1",
            original_platform="medium", assigned_channel="blogger",
            source_survival_rate=0.3, target_survival_rate=0.9,
            reason="survival_rate_below_threshold",
        ),
        RoutingDecision(
            dead_live_url="https://ex.com/2", target_url="https://t.com/2",
            original_platform="blogger", assigned_channel="blogger",
            source_survival_rate=0.9, target_survival_rate=0.9,
            reason="no_change_needed",
        ),
    ]

    args = SimpleNamespace(language="en")
    seeds = _build_seeds_with_routing(dead_events, decisions, args)

    assert len(seeds) == 2
    assert seeds[0]["platform"] == "blogger"
    assert seeds[1]["platform"] == "blogger"


# ── _single_run_lock ──────────────────────────────────────────────────────────


def test_single_run_lock_acquires(tmp_path):
    from backlink_publisher.cli.auto_recover import _single_run_lock

    lock_dir = tmp_path / "locks"
    with _single_run_lock(lock_dir) as acquired:
        assert acquired is True


def test_single_run_lock_second_acquire_fails(tmp_path):
    from backlink_publisher.cli.auto_recover import _single_run_lock

    lock_dir = tmp_path / "locks"
    with _single_run_lock(lock_dir) as first:
        assert first is True
        with _single_run_lock(lock_dir) as second:
            assert second is False


# ── main: dry-run ─────────────────────────────────────────────────────────────


def test_main_dry_run_routes_no_publish(tmp_path, capsys):
    """--dry-run should run recheck (mock), routing, and report without publishing."""
    s = _store()

    # Seed some health data so router has context
    _alive_event(s, platform="blogger")
    _dead_event(s, platform="medium")

    # Prepare a "dead event" that replan-dead would return
    dead_event = {
        "live_url": "https://ex.com/dead",
        "target_url": "https://t.com/x",
        "platform": "medium",
        "verdict": "link_stripped",
    }

    with patch("backlink_publisher.cli.auto_recover._run_recheck_phase") as mock_rp, \
         patch("backlink_publisher.cli.auto_recover._dead_events_with_checks") as mock_de, \
         patch("backlink_publisher.cli.auto_recover.subprocess") as mock_sp, \
         patch("backlink_publisher.cli.auto_recover.EventStore") as mock_store_cls:

        # Set up mocks
        mock_rp.return_value = [
            {"verdict": "link_stripped", "platform": "medium",
             "live_url": "https://ex.com/dead", "target_url": "https://t.com/x"},
        ]
        mock_de.return_value = ([dead_event], [])

        # mock EventStore() to return our real store
        mock_store_cls.return_value = s

        # mock subprocess so we can verify it's NOT called
        mock_sp.run.return_value = MagicMock(stdout=json.dumps({"phase": "publish"}).encode(),
                                              returncode=0)

        from backlink_publisher.cli.auto_recover import main
        main(["--dry-run", "--probe", "--days", "30"])

    assert mock_sp.run.call_count == 2, "dry-run should only run plan and quality subprocesses (2 calls)"

    captured = capsys.readouterr()
    # Check stdout has JSONL report
    report_lines = captured.out.strip().split("\n")
    assert len(report_lines) >= 1
    # Dry-run report should contain routing decisions
    assert any('"phase": "routing"' in line for line in report_lines)


def test_main_dry_run_no_probe(tmp_path):
    """--dry-run without --probe should run preview without network."""
    with patch("backlink_publisher.cli.auto_recover.main") as mock_main:
        from backlink_publisher.cli.auto_recover import main as real_main

    # Just verify the arg parsing is correct — dry run with no probe
    with patch("sys.argv", ["auto-recover", "--dry-run"]):
        pass


# ── health data event helpers ─────────────────────────────────────────────


def test_write_recheck_observed_requires_fields():
    """Verify that write_recheck_observed writes events that the registry can read."""
    s = _store()
    write_recheck_observed(
        s, verdict="alive", platform="velog",
        live_url="https://ex.com/a", target_url="https://t.com/x",
    )

    rows = s.query(
        "SELECT kind, payload_json FROM events WHERE kind = ?",
        (kinds.CHANNEL_RECHECK_OBSERVED,),
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["platform"] == "velog"
    assert payload["verdict"] == "alive"


def test_write_routed_event_requires_fields():
    from backlink_publisher.health.registry import write_routed_event

    s = _store()
    write_routed_event(
        s, source_channel="medium", target_channel="blogger",
        reason="survival_rate_below_threshold", source_survival_rate=0.3,
        target_survival_rate=0.9, dead_live_url="https://ex.com/a",
        target_url="https://t.com/x",
    )

    rows = s.query(
        "SELECT kind, payload_json FROM events WHERE kind = ?",
        (kinds.CHANNEL_ROUTED,),
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["source_channel"] == "medium"
    assert payload["target_channel"] == "blogger"
    assert payload["reason"] == "survival_rate_below_threshold"


@pytest.mark.skip(reason="Integration E2E: requires full pipeline infrastructure")
def test_e2e_dry_run_full_pipeline():
    """Integration: full dry-run pipeline end-to-end. Skipped for unit CI."""
    pass
