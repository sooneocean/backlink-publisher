"""Unit 6: /ce:health backlink-decay banner + decay_counts helper.

The helper is tested directly against a seeded events.db; the banner rendering
(severity tiering, advisory wording, empty/all-clear states, never-500) is
tested by injecting decay values through the route's _g_cache, mirroring the
reconciliation-gap banner tests.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import verdicts


# ── decay_counts helper (direct) ─────────────────────────────────────────────

class TestDecayCountsHelper:
    def test_groups_latest_verdict_per_link(self, tmp_path):
        from webui_app.health_metrics import decay_counts

        store = EventStore(path=tmp_path / "events.db")
        store.append(LINK_RECHECKED, {"verdict": verdicts.HOST_GONE}, article_id=1)
        store.append(LINK_RECHECKED, {"verdict": verdicts.LINK_STRIPPED}, article_id=2)
        store.append(LINK_RECHECKED, {"verdict": verdicts.DOFOLLOW_LOST}, article_id=3)
        counts = decay_counts(store)
        assert counts[verdicts.HOST_GONE] == 1
        assert counts[verdicts.LINK_STRIPPED] == 1
        assert counts[verdicts.DOFOLLOW_LOST] == 1

    def test_empty_store_all_zero(self, tmp_path):
        from webui_app.health_metrics import decay_counts

        counts = decay_counts(EventStore(path=tmp_path / "events.db"))
        assert all(v == 0 for v in counts.values())


# ── banner rendering via the route ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake = tmp_path / "config"
    with patch("backlink_publisher.config._config_dir", return_value=fake):
        yield fake


@pytest.fixture
def app():
    from webui_app import create_app

    a = create_app(start_scheduler=False)
    a.config["TESTING"] = True
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _with_decay(decay: dict):
    def _controlled(key, fn):
        if key == "recheck_decay":
            return decay
        return fn()
    return _controlled


class TestDecayBanner:
    def test_deterministic_dead_shows_primary_banner_with_cta(self, client):
        with patch("webui_app.routes.health._g_cache") as cache:
            cache.side_effect = _with_decay({"host_gone": 2, "link_stripped": 1,
                                            "dofollow_lost": 0})
            resp = client.get("/ce:health")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "Backlink decay detected" in html
        assert "2 host gone" in html
        assert "1 link stripped" in html  # singular
        assert "recheck-backlinks --probe" in html  # actionable CTA (design D1)

    def test_all_clear_no_banner(self, client):
        with patch("webui_app.routes.health._g_cache") as cache:
            cache.side_effect = _with_decay({"host_gone": 0, "link_stripped": 0,
                                            "dofollow_lost": 0})
            resp = client.get("/ce:health")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "Backlink decay detected" not in html

    def test_dofollow_only_is_advisory_not_primary(self, client):
        # design D2/D3: dofollow_lost alone must NOT render the deterministic-dead
        # primary line, only the subordinate advisory wording.
        with patch("webui_app.routes.health._g_cache") as cache:
            cache.side_effect = _with_decay({"host_gone": 0, "link_stripped": 0,
                                            "dofollow_lost": 3})
            resp = client.get("/ce:health")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8", errors="replace")
        assert "Backlink decay detected" not in html
        assert "may have lost dofollow" in html
        assert "needs manual confirmation" in html

    def test_empty_dict_no_crash(self, client):
        with patch("webui_app.routes.health._g_cache") as cache:
            cache.side_effect = _with_decay({})
            resp = client.get("/ce:health")
        assert resp.status_code == 200

    def test_route_never_500_on_decay_read_failure(self, client):
        with patch("webui_app.health_metrics.decay_counts",
                   side_effect=RuntimeError("events.db locked")):
            resp = client.get("/ce:health")
        assert resp.status_code == 200
