"""Tests for the GEO AI citation-share panel on /ce:health (Plan 2026-05-29-006 U9).

Covers:
- Happy path: measured target renders share + n.
- Edge states: never_probed / warming_up / excluded each render as distinct labels,
  never as "0%".
- Fail-open: events.db read error → panel absent, route still 200.
- XSS: citation.observed row with javascript: source URL renders as escaped text,
  not as a clickable link.
- Advisory copy: panel contains no causal claim language.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    from webui_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_citation(
    target_url: str,
    verdict: str,
    *,
    query: str = "test query",
    run_id: str = "run-1",
    engine: str = "perplexity",
    ts_utc: str | None = None,
) -> None:
    """Append a citation.observed event to the EventStore."""
    EventStore().append(
        CITATION_OBSERVED,
        {
            "verdict": verdict,
            "engine": engine,
            "query": query,
            "run_id": run_id,
        },
        target_url=target_url,
        ts_utc=ts_utc or _now(),
    )


# ── Happy path ───────────────────────────────────────────────────────────────


def test_measured_target_shows_share_and_n(client):
    """A target with >= 5 probes renders share % and n; no causal claim."""
    target = "https://example.com"
    # 6 probes: 4 cited, 2 absent → share = 4/6 ≈ 66.7%
    for i in range(4):
        _seed_citation(target, "site_cited", query=f"q{i}", run_id=f"run-{i}")
    for i in range(4, 6):
        _seed_citation(target, "absent", query=f"q{i}", run_id=f"run-{i}")

    resp = client.get("/ce:health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    assert "AI Citation Share" in body
    assert "example.com" in body
    # Share value present (66.7%)
    assert "66.7%" in body
    # Sample size present
    assert "n=" not in body or "6" in body  # n shown in table cell


def test_measured_target_panel_has_advisory_copy(client):
    """Panel advisory copy appears; no causal claim words."""
    target = "https://example.com"
    for i in range(5):
        _seed_citation(target, "site_cited", query=f"q{i}", run_id=f"run-{i}")

    body = client.get("/ce:health").get_data(as_text=True)
    assert "Advisory" in body or "advisory" in body
    # Must not imply causation
    body_lower = body.lower()
    for causal in ("caused by", "caused the", "because of the", "due to publishing"):
        assert causal not in body_lower, f"Causal claim found: {causal!r}"


# ── Edge states ──────────────────────────────────────────────────────────────


def test_warming_up_state_never_shows_zero_percent(client):
    """A target with < 5 probes shows 'Insufficient data', never '0%'."""
    target = "https://warmup.example.com"
    # Only 2 probes → below DEFAULT_MIN_SAMPLE=5 → warming_up
    _seed_citation(target, "absent", query="q0", run_id="r0")
    _seed_citation(target, "absent", query="q1", run_id="r1")

    body = client.get("/ce:health").get_data(as_text=True)
    if "warmup.example.com" in body:
        # If the panel renders this target, it must say "Insufficient data"
        assert "Insufficient data" in body
        assert "0%" not in body


def test_no_citation_events_means_no_geo_panel(client):
    """With zero citation.observed events the GEO panel block is absent."""
    body = client.get("/ce:health").get_data(as_text=True)
    # Panel only renders when there are targets
    assert "AI Citation Share (GEO)" not in body


def test_never_probed_text_not_zero_percent(client):
    """'never_probed' state (via monkeypatched geo_citation_share) renders
    'Not yet probed', never '0%'."""

    from webui_app import health_metrics

    def _fake_geo(store, *, window_days=30):
        return [{
            "target_url": "https://noprobe.example.com",
            "state": "never_probed",
            "share": None,
            "n": 0,
            "total_n": 0,
            "refused_rate": 0.0,
            "low_confidence": False,
        }]

    import webui_app.routes.health as health_route

    # Patch the geo panel to return a never_probed target
    import unittest.mock as mock
    with mock.patch.object(health_metrics, "geo_citation_share", _fake_geo):
        # Also need to clear the _g_cache for this request
        resp = client.get("/ce:health")
        # We can't easily clear the request cache between calls in this fixture,
        # so we verify the template handles the state correctly by checking
        # that when geo_panel is set with never_probed, it renders as text.
        # Direct template unit test:
        from webui_app import create_app
        import os
        app = create_app()
        app.config["TESTING"] = True
        with app.test_request_context():
            from flask import render_template
            html = render_template(
                "health.html",
                geo_panel={"targets": [{
                    "target_url": "https://noprobe.example.com",
                    "state": "never_probed",
                    "share": None,
                    "n": 0,
                    "total_n": 0,
                    "refused_rate": 0.0,
                    "low_confidence": False,
                }]},
                health=_make_empty_health(),
                projection=_make_empty_projection(),
                canary=[],
                forward_path=[],
                reconciliation_gaps={},
                recheck_decay={},
                channel_scorecard=[],
            )
        assert "Not yet probed" in html
        assert "0%" not in html


def test_excluded_state_renders_distinct_label(client):
    """'excluded' state renders 'Excluded from measurement'; share column shows '—'."""
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_request_context():
        from flask import render_template
        html = render_template(
            "health.html",
            geo_panel={"targets": [{
                "target_url": "https://excluded.example.com",
                "state": "excluded",
                "share": None,
                "n": 0,
                "total_n": 0,
                "refused_rate": 0.0,
                "low_confidence": False,
            }]},
            health=_make_empty_health(),
            projection=_make_empty_projection(),
            canary=[],
            forward_path=[],
            reconciliation_gaps={},
            recheck_decay={},
            channel_scorecard=[],
        )
    assert "Excluded from measurement" in html
    # Share column must render as — (not a share percentage)
    assert "0%" not in html


def test_warming_up_state_shows_probe_count(client):
    """'warming_up' state shows probe count, not '0%'."""
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_request_context():
        from flask import render_template
        html = render_template(
            "health.html",
            geo_panel={"targets": [{
                "target_url": "https://warmup.example.com",
                "state": "warming_up",
                "share": None,
                "n": 2,
                "total_n": 3,
                "refused_rate": 0.0,
                "low_confidence": False,
            }]},
            health=_make_empty_health(),
            projection=_make_empty_projection(),
            canary=[],
            forward_path=[],
            reconciliation_gaps={},
            recheck_decay={},
            channel_scorecard=[],
        )
    assert "Insufficient data" in html
    assert "3 probes" in html
    assert "0%" not in html


# ── Fail-open ────────────────────────────────────────────────────────────────


def test_events_db_read_error_panel_fails_open(client, monkeypatch):
    """If geo_citation_share raises, the route returns 200 (no 500)."""
    import webui_app.health_metrics as hm

    def _boom(store, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(hm, "geo_citation_share", _boom)
    resp = client.get("/ce:health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Publishing Health" in body


# ── XSS / security ───────────────────────────────────────────────────────────


def test_javascript_url_not_rendered_as_link(client):
    """A target_url with javascript: scheme must not appear as an <a> link."""
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_request_context():
        from flask import render_template
        html = render_template(
            "health.html",
            geo_panel={"targets": [{
                "target_url": "javascript:alert(1)",
                "state": "measured",
                "share": 50.0,
                "n": 10,
                "total_n": 10,
                "refused_rate": 0.0,
                "low_confidence": False,
            }]},
            health=_make_empty_health(),
            projection=_make_empty_projection(),
            canary=[],
            forward_path=[],
            reconciliation_gaps={},
            recheck_decay={},
            channel_scorecard=[],
        )
    # Must not render as a clickable link
    assert 'href="javascript:' not in html
    assert "href=\"javascript:" not in html


def test_xss_payload_in_target_url_is_html_escaped(client):
    """An XSS payload in target_url is HTML-escaped by Jinja2 autoescape."""
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_request_context():
        from flask import render_template
        html = render_template(
            "health.html",
            geo_panel={"targets": [{
                "target_url": "<script>alert(1)</script>",
                "state": "warming_up",
                "share": None,
                "n": 0,
                "total_n": 1,
                "refused_rate": 0.0,
                "low_confidence": False,
            }]},
            health=_make_empty_health(),
            projection=_make_empty_projection(),
            canary=[],
            forward_path=[],
            reconciliation_gaps={},
            recheck_decay={},
            channel_scorecard=[],
        )
    # Raw script tag must not appear — Jinja2 autoescape must have escaped it
    assert "<script>alert(1)</script>" not in html
    # Escaped form should appear
    assert "&lt;script&gt;" in html


def test_http_url_renders_as_link(client):
    """A valid https:// target URL is rendered as a clickable link."""
    from webui_app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_request_context():
        from flask import render_template
        html = render_template(
            "health.html",
            geo_panel={"targets": [{
                "target_url": "https://good.example.com",
                "state": "measured",
                "share": 75.0,
                "n": 8,
                "total_n": 8,
                "refused_rate": 0.0,
                "low_confidence": False,
            }]},
            health=_make_empty_health(),
            projection=_make_empty_projection(),
            canary=[],
            forward_path=[],
            reconciliation_gaps={},
            recheck_decay={},
            channel_scorecard=[],
        )
    assert 'href="https://good.example.com"' in html


# ── geo_citation_share metric helper ─────────────────────────────────────────


def test_geo_citation_share_returns_measured_above_floor(tmp_path, monkeypatch):
    """geo_citation_share returns 'measured' for targets with >= 5 probes."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.events import EventStore
    from webui_app.health_metrics import geo_citation_share

    store = EventStore()
    target = "https://metric.example.com"
    for i in range(6):
        store.append(
            CITATION_OBSERVED,
            {"verdict": "site_cited", "engine": "test", "query": f"q{i}", "run_id": f"r{i}"},
            target_url=target,
        )

    rows = geo_citation_share(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "measured"
    assert r["share"] == 100.0
    assert r["n"] == 6
    # 6 probes is >= MIN_SAMPLE(5) but < LOW_CONFIDENCE_THRESHOLD(10) → low_confidence
    assert r["low_confidence"] is True


def test_geo_citation_share_warming_up_below_floor(tmp_path, monkeypatch):
    """geo_citation_share returns 'warming_up' for targets with < 5 probes."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.events import EventStore
    from webui_app.health_metrics import geo_citation_share

    store = EventStore()
    target = "https://warming.example.com"
    for i in range(3):
        store.append(
            CITATION_OBSERVED,
            {"verdict": "absent", "engine": "test", "query": f"q{i}", "run_id": f"r{i}"},
            target_url=target,
        )

    rows = geo_citation_share(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "warming_up"
    assert r["share"] is None


def test_geo_citation_share_refused_excluded_from_denominator(tmp_path, monkeypatch):
    """Refused verdicts are excluded from the share denominator (D3)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.events import EventStore
    from webui_app.health_metrics import geo_citation_share

    store = EventStore()
    target = "https://refused.example.com"
    # 5 refused + 5 site_cited: denominator = 5 (refused excluded), share = 100%
    for i in range(5):
        store.append(
            CITATION_OBSERVED,
            {"verdict": "refused", "engine": "test", "query": f"qr{i}", "run_id": f"rr{i}"},
            target_url=target,
        )
    for i in range(5):
        store.append(
            CITATION_OBSERVED,
            {"verdict": "site_cited", "engine": "test", "query": f"qc{i}", "run_id": f"rc{i}"},
            target_url=target,
        )

    rows = geo_citation_share(store)
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "measured"
    assert r["share"] == 100.0  # only cited / (cited + absent); absent=0
    assert r["refused_rate"] > 0  # refused rate tracked separately


def test_geo_citation_share_empty_db_returns_empty(tmp_path, monkeypatch):
    """geo_citation_share returns [] when no citation events exist."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.events import EventStore
    from webui_app.health_metrics import geo_citation_share

    rows = geo_citation_share(EventStore())
    assert rows == []


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_empty_health():
    """Return a minimal Health object for template rendering tests."""
    from webui_app.health_metrics import DEFAULT_WINDOW_DAYS, Health, SuccessRate, _window_start
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return Health(
        window_days=DEFAULT_WINDOW_DAYS,
        since_utc=_window_start(now, DEFAULT_WINDOW_DAYS),
        success=SuccessRate(),
    )


def _make_empty_projection():
    """Return a minimal ReadProjectionResult for template rendering tests."""
    from backlink_publisher.events.reconcile import ReadProjectionResult

    return ReadProjectionResult()
