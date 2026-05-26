"""R10: mass-quarantine alarm — a flood of quarantines records a degraded
health signal even though the run completes (quarantine-and-continue).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.checkpoint import checkpoint_path
from backlink_publisher.events import EventStore, flush_for
from backlink_publisher.events.projector import (
    _HEALTH_SOURCE,
    project_run_safe,
    record_projection_health,
)

_RUN_ID = "20260526T120000-abcd1234"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _health_state(store: EventStore) -> dict[str, Any]:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT last_seen_state_json FROM projection_cursor WHERE source = ?",
            (_HEALTH_SOURCE,),
        ).fetchone()
    return json.loads(row[0]) if row and row[0] else {}


def _write_checkpoint(items: list[dict[str, Any]]) -> Path:
    p = checkpoint_path(_RUN_ID)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"run_id": _RUN_ID, "started_at": "2026-05-26T12:00:00+00:00",
                    "platform": "blogger", "mode": "publish", "status": None,
                    "items": items, "flags": {}}),
        encoding="utf-8",
    )
    return p


def _items(n_ok: int, n_bad: int) -> list[dict[str, Any]]:
    out = [{"id": f"ok{i}", "status": "pending", "adapter": "blogger",
            "payload": {"target_url": f"https://example.com/{i}"}, "title": "t"}
           for i in range(n_ok)]
    out += [{"id": f"bad{i}", "status": "weird", "adapter": "blogger", "payload": {}}
            for i in range(n_bad)]
    return out


def test_flush_for_counts_on_first_projection(tmp_path):
    result = flush_for(_write_checkpoint(_items(n_ok=1, n_bad=3)))
    assert result.records_considered == 4
    assert result.quarantined == 3


def test_project_run_safe_flags_degraded_on_quarantine_flood(tmp_path):
    # 3 of 4 records (75%) quarantine -> degraded, though the run completes.
    _write_checkpoint(_items(n_ok=1, n_bad=3))
    project_run_safe(_RUN_ID)
    state = _health_state(EventStore())
    assert state["last_error"] is None  # run did not fail
    assert state["degraded"] is True
    assert state["last_quarantine_ratio"] == pytest.approx(0.75)


def test_lower_bound_sensitivity_half_quarantined_must_degrade(tmp_path):
    # Independent of the exact threshold: a run where >=50% of considered
    # records quarantine MUST record degraded (guards against an inert threshold).
    _write_checkpoint(_items(n_ok=2, n_bad=2))  # 50%
    project_run_safe(_RUN_ID)
    assert _health_state(EventStore())["degraded"] is True


def test_healthy_run_is_not_degraded(tmp_path):
    _write_checkpoint(_items(n_ok=4, n_bad=0))
    project_run_safe(_RUN_ID)
    state = _health_state(EventStore())
    assert state["degraded"] is False
    assert state["last_quarantine_ratio"] == 0.0


def test_record_projection_health_degraded_flag_direct(tmp_path):
    store = EventStore(path=tmp_path / "events.db")
    record_projection_health(store, ok=True, quarantine_ratio=0.5)
    assert _health_state(store)["degraded"] is True
    record_projection_health(store, ok=True, quarantine_ratio=0.05)
    assert _health_state(store)["degraded"] is False
