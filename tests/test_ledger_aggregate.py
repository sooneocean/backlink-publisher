"""Unit 3 — dimension computation (build_ledger).

Real registry values used: medium=dofollow, devto=nofollow(high),
livejournal=nofollow(high), an unregistered platform → unknown.
"""

import json
from datetime import datetime, timedelta

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.ledger.aggregate import (
    _classify,
    _link_liveness,
    build_ledger,
)
from backlink_publisher.ledger.sources import LinkRecord

T = "https://site.com/page"


def _article(store, live_url, target=T):
    store.add_article({"target_urls_json": json.dumps([target]), "live_url": live_url})


@pytest.fixture
def store(tmp_path):
    s = EventStore(path=tmp_path / "events.db")
    _article(s, "https://medium.com/l1")
    _article(s, "https://devto.example/l2")
    _article(s, "https://unreg.example/l3")
    return s


def _hist(stat_live=None, error=None):
    return [
        {"id": "h1", "platform": "medium", "target_url": T,
         "article_urls": ["https://medium.com/l1"], "status": "published",
         **({"verified_at": stat_live} if stat_live else {})},
        {"id": "h2", "platform": "devto", "target_url": T,
         "article_urls": ["https://devto.example/l2"], "status": "published"},
        {"id": "h3", "platform": "fakeplat", "target_url": T,
         "article_urls": ["https://unreg.example/l3"], "status": "published",
         **({"verify_error": error} if error else {})},
    ]


# ── classification ────────────────────────────────────────────────────────

def test_classify_real_platforms():
    assert _classify("medium") == ("dofollow", None)
    assert _classify("devto") == ("nofollow", "high")
    assert _classify("livejournal") == ("nofollow", "high")


def test_classify_unknown_distinct_from_nofollow():
    # Unregistered platform and missing platform → unknown, never nofollow.
    assert _classify("totally-unregistered") == ("unknown", None)
    assert _classify(None) == ("unknown", None)


def test_registered_dofollow_platform_never_unknown():
    # referral_value(medium) is None, but medium must NOT fall into unknown.
    cls, _ = _classify("medium")
    assert cls == "dofollow"


# ── liveness ─────────────────────────────────────────────────────────────

def test_link_liveness_states():
    now = datetime(2026, 5, 25)
    fresh = (now - timedelta(days=5)).isoformat(timespec="seconds")
    old = (now - timedelta(days=40)).isoformat(timespec="seconds")
    assert _link_liveness(LinkRecord("u", verified_at=fresh), now, 30) == "live"
    assert _link_liveness(LinkRecord("u", verified_at=old), now, 30) == "stale"
    assert _link_liveness(LinkRecord("u"), now, 30) == "unverified"
    assert _link_liveness(
        LinkRecord("u", verified_at=fresh, verify_error="404"), now, 30
    ) == "failed"  # failed wins over a present verified_at


def test_link_liveness_tz_aware_does_not_crash():
    # Regression: some writers emit tz-aware UTC; naive `now` minus aware must
    # not raise. It folds to naive local before the subtraction.
    now = datetime(2026, 5, 25)
    assert _link_liveness(
        LinkRecord("u", verified_at="2026-05-24T00:00:00+00:00"), now, 30
    ) == "live"
    assert _link_liveness(
        LinkRecord("u", verified_at="2026-01-01T00:00:00+00:00"), now, 30
    ) == "stale"


def test_link_liveness_unparseable_is_unverified():
    now = datetime(2026, 5, 25)
    assert _link_liveness(LinkRecord("u", verified_at="not-a-date"), now, 30) == "unverified"
    assert _link_liveness(LinkRecord("u", verified_at=""), now, 30) == "unverified"


def test_liveness_row_level_flag(tmp_path):
    # A single history row bundling two links → row-level evidence (R7a).
    s = EventStore(path=tmp_path / "e.db")
    _article(s, "https://medium.com/a")
    _article(s, "https://medium.com/b")
    hist = [{"id": "h", "platform": "medium", "target_url": T,
             "article_urls": ["https://medium.com/a", "https://medium.com/b"]}]
    rows = build_ledger(store=s, history=hist)
    assert rows[0].liveness_row_level is True


def test_single_link_rows_not_row_level(store):
    rows = build_ledger(store=store, history=_hist())  # 3 single-url items
    assert rows[0].liveness_row_level is False


def test_stale_boundary(store):
    now = datetime.now()
    just_stale = (now - timedelta(days=31)).isoformat(timespec="seconds")
    just_live = (now - timedelta(days=29)).isoformat(timespec="seconds")
    assert _link_liveness(LinkRecord("u", verified_at=just_stale), now, 30) == "stale"
    assert _link_liveness(LinkRecord("u", verified_at=just_live), now, 30) == "live"


# ── build_ledger ───────────────────────────────────────────────────────────

def test_build_ledger_dimensions(store):
    fresh = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    rows = build_ledger(store=store, history=_hist(stat_live=fresh))
    assert len(rows) == 1
    row = rows[0]
    assert row.target_url == T
    assert row.total_links == 3
    # medium link is fresh-verified dofollow → live + live_dofollow.
    assert row.live_links == 1
    assert row.live_dofollow == 1
    assert row.dofollow.dofollow == 1
    assert row.dofollow.nofollow == 1
    assert row.dofollow.nofollow_high == 1
    assert row.dofollow.unknown == 1  # fakeplat
    assert row.platform_count == 3
    assert row.history_item_ids == ["h1", "h2", "h3"]


def test_failed_link_makes_target_failed_worst_status(store):
    rows = build_ledger(store=store, history=_hist(error="410 gone"))
    row = rows[0]
    # worst-status-wins: one failed link → target liveness failed.
    assert row.liveness == "failed"


def test_never_verified_target_is_unverified(store):
    rows = build_ledger(store=store, history=_hist())
    row = rows[0]
    assert row.liveness == "unverified"
    assert row.live_links == 0
    assert row.liveness_verified_at is None


def test_default_sort_weak_targets_first(tmp_path):
    s = EventStore(path=tmp_path / "e.db")
    _article(s, "https://medium.com/strong", target="https://t.com/strong")
    _article(s, "https://devto.example/weak", target="https://t.com/weak")
    fresh = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    hist = [
        {"id": "a", "platform": "medium", "target_url": "https://t.com/strong",
         "article_urls": ["https://medium.com/strong"], "verified_at": fresh},
        {"id": "b", "platform": "devto", "target_url": "https://t.com/weak",
         "article_urls": ["https://devto.example/weak"], "verified_at": fresh},
    ]
    rows = build_ledger(store=s, history=hist)
    # weak target (0 live-dofollow, nofollow) sorts before strong (1 live-dofollow).
    assert rows[0].target_url == "https://t.com/weak"
    assert rows[1].target_url == "https://t.com/strong"
