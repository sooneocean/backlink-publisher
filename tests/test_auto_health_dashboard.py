"""Content-level tests for the automation health dashboard."""

from __future__ import annotations

import pytest

from backlink_publisher.events.kinds import PUBLISH_QUALITY_BLOCKED
from backlink_publisher.events.store import EventStore


@pytest.fixture
def client(disable_csrf):
    import webui

    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    return webui.app.test_client()


def _body(resp) -> str:
    return resp.data.decode("utf-8", errors="ignore")


def test_auto_health_shows_required_panels_and_claim_boundary(client):
    resp = client.get("/auto-health")

    assert resp.status_code == 200
    body = _body(resp)
    for text in [
        "Automation Health",
        "Pipeline Throughput",
        "Recovery Queue",
        "Canary Health Status",
        "Resource Budget",
        "Recent Alerts",
    ]:
        assert text in body
    assert "Draft, dry-run, and unverified HTTP 200 are not live dofollow success." in body


def test_auto_health_recent_alerts_reads_quality_blocked_events(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    store = EventStore()
    store.append(
        PUBLISH_QUALITY_BLOCKED,
        {
            "quality_check": "anchor_density_high",
            "draft_label": "seed_1",
        },
        target_url="https://example.com/a",
        host="example.com",
    )

    resp = client.get("/auto-health")

    assert resp.status_code == 200
    body = _body(resp)
    assert "anchor_density_high" in body
    assert "seed_1" in body
    assert "quality blocked" in body.lower()


class _FakeHistoryStore:
    def __init__(self, rows):
        self._rows = rows

    def load(self):
        return self._rows


def test_pipeline_throughput_drafts_not_counted_as_failed(monkeypatch):
    """A ``drafted`` history row is a successful draft creation (the default
    publish mode), not a failure. The throughput panel must not lump it into
    ``failed``.

    Regression: ``failed = total - successful`` counted every non-published
    row as failed, so a run of draft-mode publishes showed Failed = N and
    Success Rate = 0%, alarming the operator about non-failures.
    """
    from webui_app.routes import auto_health

    rows = [
        {"status": "drafted", "created_at": "2026-06-23 10:00"},
        {"status": "drafted", "created_at": "2026-06-23 10:05"},
        {"status": "published", "created_at": "2026-06-23 10:10"},
        {"status": "failed", "created_at": "2026-06-23 10:15"},
        {"status": "failed_partial", "created_at": "2026-06-23 10:20"},
    ]
    monkeypatch.setattr("webui_store.history_store", _FakeHistoryStore(rows))

    out = auto_health._pipeline_throughput()

    assert out["total"] == 5
    # Only the genuine failures (failed + failed_partial), NOT the 2 drafts.
    assert out["failed"] == 2, (
        f"drafted rows were miscounted as failed: failed={out['failed']}"
    )
    assert out["successful"] == 1  # the one published row

