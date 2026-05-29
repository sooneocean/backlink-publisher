"""Unit 4: link.rechecked emission (WAL-safe) + decay-count derivation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import verdicts
from backlink_publisher.recheck.events_io import derive_decay_counts, emit_recheck

NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return EventStore(path=tmp_path / "events.db")


def _result(aid, verdict, **extra):
    base = {
        "live_url": f"https://medium.com/{aid}",
        "target_url": "https://my.site/",
        "host": "medium.com",
        "article_id": aid,
        "platform": "medium",
        "verdict": verdict,
        "reason": None,
        "source": "events",
    }
    base.update(extra)
    return base


def test_emit_writes_events_with_verdict_and_columns(store):
    written = emit_recheck(store, [_result(1, verdicts.HOST_GONE),
                                  _result(2, verdicts.ALIVE)])
    assert written == 2
    rows = store.query(
        "SELECT article_id, target_url, payload_json FROM events WHERE kind = ?",
        (LINK_RECHECKED,),
    )
    assert len(rows) == 2
    # Positive present-assertion: verdict in payload, target_url in the column.
    by_aid = {r["article_id"]: r for r in rows}
    assert json.loads(by_aid[1]["payload_json"])["verdict"] == verdicts.HOST_GONE
    assert by_aid[1]["target_url"] == "https://my.site/"


def test_dry_preview_rows_without_verdict_are_skipped(store):
    written = emit_recheck(store, [{"live_url": "x", "will_probe": True}])
    assert written == 0
    assert store.query("SELECT 1 FROM events WHERE kind = ?", (LINK_RECHECKED,)) == []


def test_decay_counts_group_by_latest_verdict(store):
    emit_recheck(store, [
        _result(1, verdicts.HOST_GONE),
        _result(2, verdicts.LINK_STRIPPED),
        _result(3, verdicts.DOFOLLOW_LOST),
        _result(4, verdicts.ALIVE),
        _result(5, verdicts.PROBE_ERROR),
    ])
    counts = derive_decay_counts(store)
    assert counts[verdicts.HOST_GONE] == 1
    assert counts[verdicts.LINK_STRIPPED] == 1
    assert counts[verdicts.DOFOLLOW_LOST] == 1
    assert counts[verdicts.ALIVE] == 1
    assert counts[verdicts.PROBE_ERROR] == 1


def test_latest_verdict_wins_recovery(store):
    # Link 1 was host_gone, then re-probed alive later → counts as alive.
    store.append(LINK_RECHECKED, {"verdict": verdicts.HOST_GONE}, article_id=1,
                 ts_utc=(NOW - timedelta(days=10)).isoformat())
    store.append(LINK_RECHECKED, {"verdict": verdicts.ALIVE}, article_id=1,
                 ts_utc=(NOW - timedelta(days=1)).isoformat())
    counts = derive_decay_counts(store)
    assert counts[verdicts.ALIVE] == 1
    assert counts[verdicts.HOST_GONE] == 0


def test_old_unrechecked_dead_still_counts_no_recovery_illusion(store):
    # host_gone 40 days ago, never re-probed → must still count as decayed
    # (no age window dropping it, which would look like recovery).
    store.append(LINK_RECHECKED, {"verdict": verdicts.HOST_GONE}, article_id=1,
                 ts_utc=(NOW - timedelta(days=40)).isoformat())
    counts = derive_decay_counts(store)
    assert counts[verdicts.HOST_GONE] == 1


def test_empty_store_all_zero(store):
    counts = derive_decay_counts(store)
    assert set(counts) == verdicts.VERDICTS
    assert all(v == 0 for v in counts.values())


def test_wal_safe_batch_emit_no_lock_error(store):
    # A 50-row batch shares one transaction and flushes quarantines post-commit;
    # must not raise sqlite "database is locked".
    results = [_result(i, verdicts.ALIVE) for i in range(1, 51)]
    written = emit_recheck(store, results)
    assert written == 50
    assert derive_decay_counts(store)[verdicts.ALIVE] == 50
