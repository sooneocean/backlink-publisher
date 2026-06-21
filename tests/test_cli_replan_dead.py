"""Deterministic tests for the replan-dead CLI."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone

from backlink_publisher.cli import replan_dead
from backlink_publisher.events.kinds import LINK_RECHECKED, PUBLISH_CONFIRMED
from backlink_publisher.events.store import EventStore
from backlink_publisher.recheck import verdicts
from backlink_publisher.remediation.events_io import emit_event as emit_remediation_event


def _run_replan_dead(monkeypatch, argv: list[str] | None = None):
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    replan_dead.main(argv or [])

    return stdout.getvalue(), stderr.getvalue()


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _append_rechecked(
    store: EventStore,
    *,
    verdict: str,
    live_url: str = "https://publisher.example/dead-post",
    target_url: str = "https://target.example/page",
    platform: str = "telegraph",
) -> None:
    store.append(
        LINK_RECHECKED,
        {
            "verdict": verdict,
            "live_url": live_url,
            "platform": platform,
        },
        target_url=target_url,
        host="publisher.example",
        ts_utc=datetime.now(timezone.utc).isoformat(),
    )


def test_replan_dead_emits_seed_for_host_gone(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store = EventStore()
    _append_rechecked(store, verdict=verdicts.HOST_GONE)

    stdout, stderr = _run_replan_dead(
        monkeypatch,
        ["--days", "30", "--min-gap", "3", "--language", "en"],
    )

    assert stderr == ""
    rows = _jsonl(stdout)
    assert rows == [
        {
            "target_url": "https://target.example/page",
            "language": "en",
            "url_mode": "A",
            "publish_mode": "draft",
            "platform": "telegraph",
            "_replan_provenance": {
                "dead_live_url": "https://publisher.example/dead-post",
                "host": "publisher.example",
                "reason": "dead_link_auto_replan",
            },
        }
    ]


def test_replan_dead_skips_dofollow_lost(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _append_rechecked(EventStore(), verdict=verdicts.DOFOLLOW_LOST)

    stdout, stderr = _run_replan_dead(monkeypatch, ["--days", "30"])

    assert stdout == ""
    assert stderr == ""


def test_replan_dead_skips_resolved_live_url(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store = EventStore()
    live_url = "https://publisher.example/dead-post"
    _append_rechecked(store, verdict=verdicts.LINK_STRIPPED, live_url=live_url)
    emit_remediation_event(
        store,
        live_url,
        "resolve",
        host="publisher.example",
        target_url="https://target.example/page",
    )

    stdout, stderr = _run_replan_dead(monkeypatch, ["--days", "30", "--emit-stderr"])

    assert stdout == ""
    assert "resolved link(s) excluded" in stderr
    assert "emitted 0 seed(s)" in stderr


def test_replan_dead_respects_min_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store = EventStore()
    target_url = "https://target.example/page"
    _append_rechecked(store, verdict=verdicts.HOST_GONE, target_url=target_url)
    for idx in range(3):
        store.append(
            PUBLISH_CONFIRMED,
            {"live_url": f"https://publisher.example/live-{idx}"},
            target_url=target_url,
            host="publisher.example",
        )

    stdout, stderr = _run_replan_dead(
        monkeypatch,
        ["--days", "30", "--min-gap", "3", "--emit-stderr"],
    )

    assert stdout == ""
    assert "already has 3 live link(s) >= --min-gap=3" in stderr
