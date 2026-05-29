"""Reconciler read-time pass: batched dedup reads + behavior preservation.

``events/reconciler.py``'s cross-reference pass used to open one DedupStore
connection per pending/failed checkpoint item. It now resolves every item to a
key, issues a single ``get_many``, then cross-references. These tests pin both
the unchanged outcomes (auto-fix R2, stale-gap quarantine R3, R10 skip) and the
batching (``get`` called 0x, ``get_many`` once).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import backlink_publisher.events.reconcile as reconcile_mod
import backlink_publisher.events.reconciler as recon
from backlink_publisher.events.reconciler import _reconcile_checkpoints
from backlink_publisher.events.store import EventStore
from backlink_publisher.idempotency.store import DedupKey, DedupStore


@pytest.fixture()
def stores(tmp_path):
    return (
        EventStore(path=tmp_path / "events.db"),
        DedupStore(path=tmp_path / "dedup.db"),
    )


def _item(item_id, target_url, *, status="pending", created_at=None, platform="blogger"):
    return {
        "id": item_id,
        "_run_id": f"run-{item_id}",
        "platform": platform,
        "status": status,
        "payload": {"target_url": target_url},
        "created_at": created_at,
    }


def _spy_update(monkeypatch):
    """Stub the checkpoint writer so auto-fix succeeds without on-disk files."""
    calls = []
    monkeypatch.setattr(
        recon, "_update_checkpoint_item",
        lambda run_id, item_id, status: calls.append((run_id, item_id, status)),
    )
    return calls


def test_auto_fixes_done_quarantines_stale_keeps_recent(stores, monkeypatch):
    event_store, dedup_store = stores
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    # A: has a done dedup record -> auto-fix. B: no record + old -> quarantine.
    # C: no record but recent -> left for next pass.
    dedup_store.seed(
        DedupKey(platform="blogger", target_url="https://money.example/a"),
        "done",
        live_url="https://blogger.com/p/a",
    )
    items = [
        _item("A", "https://money.example/a", created_at=now),
        _item("B", "https://money.example/b", created_at=old),
        _item("C", "https://money.example/c", created_at=now),
    ]
    monkeypatch.setattr(recon, "list_failed_items", lambda: items)
    update_calls = _spy_update(monkeypatch)

    summary = _reconcile_checkpoints(event_store, dedup_store)

    assert summary.total_checkpoints == 3
    assert summary.auto_fixed == 1
    assert summary.cleared == 1
    assert summary.quarantined == 1
    assert update_calls == [("run-A", "A", "done")]


def test_skips_already_quarantined_url(stores, monkeypatch):
    event_store, dedup_store = stores
    canon = recon._canonicalize_url("https://money.example/d")
    reconcile_mod._quarantine(
        event_store, source=f"reconciler:{canon}", reason="seeded", dedup_key=canon
    )

    items = [_item("D", "https://money.example/d")]
    monkeypatch.setattr(recon, "list_failed_items", lambda: items)
    _spy_update(monkeypatch)

    summary = _reconcile_checkpoints(event_store, dedup_store)

    assert summary.skipped_quarantined == 1
    assert summary.auto_fixed == 0
    assert summary.quarantined == 0


def test_unparseable_or_missing_platform_is_skipped_not_counted(stores, monkeypatch):
    event_store, dedup_store = stores
    items = [
        _item("E", "", platform="blogger"),          # empty url -> drop
        _item("F", "https://money.example/f", platform=""),  # empty platform -> drop
    ]
    monkeypatch.setattr(recon, "list_failed_items", lambda: items)
    _spy_update(monkeypatch)

    summary = _reconcile_checkpoints(event_store, dedup_store)

    assert summary.total_checkpoints == 2
    assert summary.auto_fixed == 0
    assert summary.quarantined == 0
    assert summary.skipped_quarantined == 0


def test_dedup_reads_are_batched_not_per_item(stores, monkeypatch):
    """N checkpoint items -> exactly one get_many, zero per-item get()."""
    event_store, dedup_store = stores
    for i in range(4):
        dedup_store.seed(
            DedupKey(platform="blogger", target_url=f"https://money.example/p{i}"),
            "done",
        )
    items = [_item(str(i), f"https://money.example/p{i}") for i in range(4)]
    monkeypatch.setattr(recon, "list_failed_items", lambda: items)
    _spy_update(monkeypatch)

    get_calls = {"n": 0}
    many_calls = {"n": 0}
    orig_get = dedup_store.get
    orig_many = dedup_store.get_many

    def spy_get(key):
        get_calls["n"] += 1
        return orig_get(key)

    def spy_many(keys):
        many_calls["n"] += 1
        return orig_many(keys)

    monkeypatch.setattr(dedup_store, "get", spy_get)
    monkeypatch.setattr(dedup_store, "get_many", spy_many)

    summary = _reconcile_checkpoints(event_store, dedup_store)

    assert summary.auto_fixed == 4
    assert get_calls["n"] == 0       # no per-item connection
    assert many_calls["n"] == 1      # one batch read for the whole pass


def test_batch_read_failure_leaves_items_for_next_pass(stores, monkeypatch):
    """A dedup read failure neither auto-fixes nor quarantines — items are left
    for the next pass (matches the original per-item skip-on-get-failure)."""
    event_store, dedup_store = stores
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    items = [_item("G", "https://money.example/g", created_at=old)]
    monkeypatch.setattr(recon, "list_failed_items", lambda: items)
    _spy_update(monkeypatch)

    def boom(keys):
        raise RuntimeError("dedup db unavailable")

    monkeypatch.setattr(dedup_store, "get_many", boom)

    summary = _reconcile_checkpoints(event_store, dedup_store)

    assert summary.total_checkpoints == 1
    assert summary.auto_fixed == 0
    assert summary.quarantined == 0  # not quarantined despite being stale
