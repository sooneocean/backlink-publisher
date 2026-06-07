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
