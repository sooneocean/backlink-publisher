"""Automation watchdog and health-signal contract tests."""

from __future__ import annotations

import json


def test_health_signal_import_and_serializes_ts():
    from backlink_publisher.automation.signals import make_canary_drift_signal

    signal = make_canary_drift_signal("telegraph", consecutive=3)
    row = signal.to_jsonl_dict()

    assert row["signal_type"] == "canary_status.drift-confirmed"
    assert row["platform"] == "telegraph"
    assert row["payload"] == {"consecutive": 3}
    assert isinstance(row["ts_utc"], str) and row["ts_utc"]


def test_watch_cycle_emits_clean_jsonl_signal(monkeypatch, capsys):
    from backlink_publisher.automation.signals import make_channel_expired_signal
    from backlink_publisher.automation import watchdog

    monkeypatch.setattr(
        watchdog,
        "check_canary_health",
        lambda: [make_channel_expired_signal("velog", "oauth_expired")],
    )
    monkeypatch.setattr(watchdog, "check_channel_status", lambda: [])

    emitted = watchdog.run_watch_cycle()
    captured = capsys.readouterr()

    assert emitted == 1
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert rows == [
        {
            "signal_type": "channel_status.expired",
            "platform": "velog",
            "payload": {"error_code": "oauth_expired"},
            "ts_utc": rows[0]["ts_utc"],
        }
    ]
    diagnostics = [json.loads(line) for line in captured.err.splitlines()]
    assert diagnostics[0]["level"] == "RECON"
    assert diagnostics[0]["msg"] == "watchdog_cycle_complete"
