"""Tests for the locked ReviewStatus store + ``comment status`` (plan Unit 8).

Covers the lock's reason for existing (no lost updates under concurrency), the
secret-purge-on-delete guarantee, ``0o600`` assert-and-repair, and CONFIG_DIR
re-resolution. The autouse conftest sandboxes CONFIG_DIR; tests that change it mid-run
use ``monkeypatch.setenv``.
"""

from __future__ import annotations

import json
import threading

import pytest

from backlink_publisher._util.errors import PipelineError, UsageError
from backlink_publisher.cli import comment
from backlink_publisher.comment_outreach import schema
from backlink_publisher.comment_outreach import store


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    d.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(d))
    return d


# --- Happy path: transitions reflect the latest state ----------------------
def test_transitions_reflect_latest(config_dir):
    store.set_status("t1", "pending")
    store.set_status("t1", "approved")
    rec = store.set_status("t1", "posted", comment_url="https://b.example/post#c1")
    loaded = store.load_status("t1")
    assert loaded["status"] == "posted"
    assert loaded["comment_url"] == "https://b.example/post#c1"
    # exactly one row for the key
    rows = store._load_all(store._store_path())
    assert [r for r in rows if r["target_id"] == "t1"] == [rec]


def test_review_status_round_trips_schema(config_dir):
    rec = store.set_status("t1", "posted", reviewer="alice", final_comment_text="the text")
    assert schema.validate_review_status(rec) == []
    assert store.load_status("t1")["reviewer"] == "alice"


# --- Concurrency: distinct keys both survive (the lock's real value) -------
def test_concurrent_distinct_keys_both_survive(config_dir):
    barrier = threading.Barrier(2)

    def worker(tid):
        barrier.wait()  # maximize overlap of the read-modify-write
        store.set_status(tid, "pending")

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = {r["target_id"] for r in store._load_all(store._store_path())}
    assert ids == {"t0", "t1"}  # without the flock, one write would be lost


# --- Concurrency: same key serializes to exactly one row -------------------
def test_concurrent_same_key_no_silent_loss(config_dir):
    barrier = threading.Barrier(5)

    def worker(n):
        barrier.wait()
        store.set_status("same", "pending", result_notes=f"writer-{n}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = [r for r in store._load_all(store._store_path()) if r["target_id"] == "same"]
    assert len(rows) == 1  # serialized, exactly one surviving row
    assert rows[0]["result_notes"].startswith("writer-")  # a real write, not corruption


# --- 0o600 assert-and-repair -----------------------------------------------
def test_store_created_0600(config_dir):
    store.set_status("t1", "pending")
    mode = store._store_path().stat().st_mode & 0o777
    assert mode == 0o600


def test_preseeded_0644_store_is_tightened(config_dir):
    path = store._store_path()
    path.write_text(json.dumps({"target_id": "old", "status": "pending"}) + "\n")
    path.chmod(0o644)
    store.set_status("t2", "pending")  # one call must repair the loose mode
    assert (path.stat().st_mode & 0o777) == 0o600


# --- CONFIG_DIR re-resolution ----------------------------------------------
def test_config_dir_reresolved_between_calls(tmp_path, monkeypatch):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(a))
    store.set_status("t1", "pending")
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(b))
    store.set_status("t2", "pending")
    assert (a / store.STORE_FILENAME).exists()
    assert (b / store.STORE_FILENAME).exists()
    assert store.load_status("t1") is None  # t1 lives only under 'a', now resolving 'b'


# --- Delete path purges the row and its secret bytes -----------------------
def test_removed_purges_row_and_secret_bytes(config_dir):
    store.set_status("t1", "posted", final_comment_text="SECRET-PASTE-TEXT")
    assert store.load_status("t1")["status"] == "posted"

    store.set_status("t1", "removed")
    assert store.load_status("t1") is None  # row dropped

    raw = store._store_path().read_text(encoding="utf-8")
    assert "SECRET-PASTE-TEXT" not in raw  # physically absent from the file bytes


def test_rejected_also_purges(config_dir):
    store.set_status("t1", "approved", final_comment_text="draft text")
    store.set_status("t1", "rejected")
    assert store.load_status("t1") is None
    assert "draft text" not in store._store_path().read_text(encoding="utf-8")


# --- CLI: invalid status -> UsageError (exit 1), not argparse exit 2 -------
def test_cli_invalid_status_is_usage_error(config_dir):
    parser_args = ["status", "t1", "--set", "published"]  # not in STATUS_ENUM
    with pytest.raises(UsageError):
        comment._handle_status(comment._build_parser().parse_args(parser_args))


def test_cli_missing_target_id_is_usage_error(config_dir):
    with pytest.raises(UsageError):
        comment._handle_status(comment._build_parser().parse_args(["status", "--set", "pending"]))


# --- Regression: lock-file open failure surfaces as a clean PipelineError --
def test_lock_open_failure_raises_pipeline_error(config_dir, monkeypatch):
    def _boom(*a, **k):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(store.os, "open", _boom)
    with pytest.raises(PipelineError):  # not a raw OSError traceback
        store.set_status("t1", "pending")


# --- CLI end-to-end: status echoes the record, persists, exit 0 ------------
def test_cli_status_end_to_end(config_dir, capsys):
    rc = comment.main(["status", "t9", "--set", "approved", "--reviewer", "bob"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {**out, "target_id": "t9", "status": "approved", "reviewer": "bob"}
    assert store.load_status("t9")["status"] == "approved"


# --- B2: CRM funnel states + last_touch + is_do_not_contact -----------------

def test_crm_statuses_accepted_by_schema(config_dir):
    for s in ("contacted", "replied", "won", "lost"):
        rec = store.set_status("crm-t1", s)
        assert schema.validate_review_status(rec) == [], f"status={s!r} should be valid"


def test_set_status_writes_last_touch(config_dir):
    rec = store.set_status("crm-t2", "contacted")
    assert "last_touch" in rec
    loaded = store.load_status("crm-t2")
    assert loaded["last_touch"] == rec["last_touch"]


def test_last_touch_updates_on_each_transition(config_dir):
    r1 = store.set_status("crm-t3", "contacted")
    import time; time.sleep(0.01)
    r2 = store.set_status("crm-t3", "replied")
    assert r2["last_touch"] >= r1["last_touch"]


def test_is_do_not_contact_false_for_unknown(config_dir):
    assert store.is_do_not_contact("no-such-target") is False


def test_is_do_not_contact_false_for_pending(config_dir):
    store.set_status("crm-t4", "pending")
    assert store.is_do_not_contact("crm-t4") is False


def test_is_do_not_contact_true_for_won(config_dir):
    store.set_status("crm-t5", "won")
    assert store.is_do_not_contact("crm-t5") is True


def test_is_do_not_contact_true_for_lost(config_dir):
    store.set_status("crm-t6", "lost")
    assert store.is_do_not_contact("crm-t6") is True


def test_is_do_not_contact_false_for_rejected(config_dir):
    # "rejected" is in _DELETE_STATUSES — the row is physically purged,
    # so load_status returns None and is_do_not_contact correctly returns False.
    store.set_status("crm-t7", "rejected")
    assert store.is_do_not_contact("crm-t7") is False
