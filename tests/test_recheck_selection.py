"""Unit 3: age-based candidate selection from the publish.confirmed stream.

Verifies the confirmed-event source (not the articles projection), the
event-time-series age cursor (probe_error never advances last_definitive_at),
oldest-first ordering, the cap, filters, the NULL-url exclusion, and the stdin
trust boundary.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED, PUBLISH_CONFIRMED
from backlink_publisher.recheck import verdicts
from backlink_publisher.recheck.selection import (
    read_stdin_candidates,
    select_candidates,
)

NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return EventStore(path=tmp_path / "events.db")


def _confirm(store, aid, url, *, tgt="https://my.site/", days_ago=30,
            host="medium.com", platform="medium"):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    store.append(
        PUBLISH_CONFIRMED,
        {"live_url": url, "target_url": tgt, "platform": platform},
        target_url=tgt, host=host, article_id=aid, ts_utc=ts,
    )


def _recheck(store, aid, verdict, *, days_ago):
    ts = (NOW - timedelta(days=days_ago)).isoformat()
    store.append(LINK_RECHECKED, {"verdict": verdict}, article_id=aid, ts_utc=ts)


def test_never_checked_all_selected_oldest_publish_first(store):
    _confirm(store, 1, "https://medium.com/a", days_ago=10)
    _confirm(store, 2, "https://medium.com/b", days_ago=40)
    _confirm(store, 3, "https://medium.com/c", days_ago=25)
    got = select_candidates(store, now=NOW)
    assert [c["article_id"] for c in got] == [2, 3, 1]  # oldest publish first
    assert all(c["source"] == "events" for c in got)


def test_age_threshold(store):
    _confirm(store, 1, "https://medium.com/a", days_ago=60)
    _confirm(store, 2, "https://medium.com/b", days_ago=60)
    _recheck(store, 1, verdicts.ALIVE, days_ago=20)  # last definitive 20d > 14 → due
    _recheck(store, 2, verdicts.ALIVE, days_ago=5)   # 5d < 14 → not due
    got = {c["article_id"] for c in select_candidates(store, now=NOW)}
    assert got == {1}


def test_probe_error_does_not_advance_cursor_but_min_retry_gates(store):
    # last definitive 30d ago (due), but a probe_error today.
    _confirm(store, 1, "https://medium.com/a", days_ago=60)
    _recheck(store, 1, verdicts.ALIVE, days_ago=30)
    _recheck(store, 1, verdicts.PROBE_ERROR, days_ago=0)  # today
    # min-retry floor (1 day) blocks a same-day re-probe even though the
    # definitive cursor is stale.
    assert select_candidates(store, now=NOW) == []
    # ...but once the probe_error attempt is older than min-retry, it is due
    # again (probe_error never advanced last_definitive_at).
    got = select_candidates(store, now=NOW + timedelta(days=2))
    assert [c["article_id"] for c in got] == [1]


def test_oldest_definitive_first_ordering(store):
    _confirm(store, 1, "https://medium.com/a", days_ago=60)
    _confirm(store, 2, "https://medium.com/b", days_ago=60)
    _recheck(store, 1, verdicts.ALIVE, days_ago=20)
    _recheck(store, 2, verdicts.ALIVE, days_ago=40)  # 2 checked longer ago → first
    got = [c["article_id"] for c in select_candidates(store, now=NOW)]
    assert got == [2, 1]


def test_cap_keeps_oldest(store):
    for i in range(1, 11):
        _confirm(store, i, f"https://medium.com/{i}", days_ago=i)  # 1=newest..10=oldest
    got = select_candidates(store, now=NOW, cap=3)
    assert len(got) == 3
    assert [c["article_id"] for c in got] == [10, 9, 8]  # oldest publish kept


def test_filters_host_since_runid_limit(store):
    _confirm(store, 1, "https://medium.com/a", host="medium.com", days_ago=30)
    _confirm(store, 2, "https://devto.com/b", host="devto.com", days_ago=30)
    only_medium = select_candidates(store, now=NOW, host="medium.com")
    assert [c["article_id"] for c in only_medium] == [1]
    limited = select_candidates(store, now=NOW, limit=1)
    assert len(limited) == 1


def test_since_excludes_older_publishes(store):
    _confirm(store, 1, "https://medium.com/a", days_ago=5)
    _confirm(store, 2, "https://medium.com/b", days_ago=40)
    got = {c["article_id"]
           for c in select_candidates(store, now=NOW, since=NOW - timedelta(days=10))}
    assert got == {1}


def test_null_live_url_confirm_excluded(store):
    store.append(PUBLISH_CONFIRMED, {"live_url": None, "platform": "x"},
                 target_url="https://t/", host="h", article_id=1,
                 ts_utc=(NOW - timedelta(days=30)).isoformat())
    assert select_candidates(store, now=NOW) == []


def test_empty_store_returns_empty(store):
    assert select_candidates(store, now=NOW) == []


# ── stdin trust boundary (R11 / SEC3) ────────────────────────────────────────

def test_stdin_none_when_tty():
    class _TTY(io.StringIO):
        def isatty(self):
            return True
    assert read_stdin_candidates(_TTY()) is None


def test_stdin_none_when_empty():
    assert read_stdin_candidates(io.StringIO("")) is None


def test_stdin_reads_and_tags_source():
    fh = io.StringIO(
        '{"live_url": "https://medium.com/a", "target_url": "https://t/", "platform": "medium"}\n'
    )
    rows = read_stdin_candidates(fh)
    assert len(rows) == 1
    assert rows[0]["live_url"] == "https://medium.com/a"
    assert rows[0]["source"] == "stdin"


def test_stdin_rejects_non_http_scheme():
    fh = io.StringIO(
        '{"live_url": "file:///etc/passwd"}\n'
        '{"live_url": "ftp://x/y"}\n'
        '{"live_url": "https://ok.com/p"}\n'
    )
    rows = read_stdin_candidates(fh)
    assert [r["live_url"] for r in rows] == ["https://ok.com/p"]
