"""Tests for ``backlink_publisher.events.store.EventStore``.

Follows the plan's U1 execution note: lead with a single round-trip test
that locks in column types. Positive-shape assertions throughout.
"""

from __future__ import annotations

import multiprocessing
import sqlite3
import subprocess
import sys

import pytest

from backlink_publisher.events import store as store_module
from backlink_publisher.events.store import EventStore


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    """Redirect ``_config_dir()`` so events.db lives in tmp_path."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def test_append_event_round_trips_every_column(tmp_path):
    store = EventStore()
    event_id = store.append(
        kind="publish.intent",
        payload={"target_url": "https://x.com/a", "host": "x.com"},
        run_id="20260518T120000-abcd1234",
        target_url="https://x.com/a",
        host="x.com",
        article_id=None,
        ts_raw="2026-05-18T12:00:00+00:00",
        ts_utc="2026-05-18T12:00:00+00:00",
    )
    assert isinstance(event_id, int) and event_id > 0

    with store.connect() as conn:
        row = conn.execute(
            "SELECT id, ts_raw, ts_utc, run_id, kind, target_url, host, "
            "article_id, payload_json FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == event_id
    assert row[1] == "2026-05-18T12:00:00+00:00"
    assert row[2] == "2026-05-18T12:00:00+00:00"
    assert row[3] == "20260518T120000-abcd1234"
    assert row[4] == "publish.intent"
    assert row[5] == "https://x.com/a"
    assert row[6] == "x.com"
    assert row[7] is None
    # payload_json is stable JSON (sort_keys=True) so the test asserts the
    # exact serialisation rather than just "contains the URL".
    assert row[8] == '{"host": "x.com", "target_url": "https://x.com/a"}'


def test_add_article_round_trips_every_column(tmp_path):
    store = EventStore()
    article_id = store.add_article({
        "body": "# Hello\nLink to [target](https://x.com/a)",
        "anchors_json": '[{"url": "https://x.com/a", "anchor": "target"}]',
        "target_urls_json": '["https://x.com/a"]',
        "lang": "en",
        "host": "blogger.com",
        "live_url": "https://op.blogspot.com/2026/05/post-1.html",
        "published_at_raw": "2026-05-18T12:00:00Z",
        "published_at_utc": "2026-05-18T12:00:00+00:00",
        "run_id": "20260518T120000-abcd1234",
    })
    assert article_id > 0

    rows = store.query(
        "SELECT body, anchors_json, lang, host, live_url, run_id "
        "FROM articles WHERE article_id = ?",
        (article_id,),
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["body"].startswith("# Hello")
    assert row["anchors_json"].startswith("[")
    assert row["lang"] == "en"
    assert row["host"] == "blogger.com"
    assert row["live_url"] == "https://op.blogspot.com/2026/05/post-1.html"
    assert row["run_id"] == "20260518T120000-abcd1234"


def test_add_article_uses_schema_default_for_anchors_json(tmp_path):
    store = EventStore()
    article_id = store.add_article({"live_url": "https://op.blogspot.com/x"})
    rows = store.query(
        "SELECT anchors_json, target_urls_json FROM articles WHERE article_id = ?",
        (article_id,),
    )
    assert rows[0]["anchors_json"] == "[]"
    assert rows[0]["target_urls_json"] == "[]"


def test_add_article_rejects_unknown_columns(tmp_path):
    store = EventStore()
    with pytest.raises(KeyError, match="unknown article columns"):
        store.add_article({"live_url": "https://x.com/a", "evil": "bobby"})


def test_duplicate_live_url_raises_integrity_error(tmp_path):
    store = EventStore()
    store.add_article({"live_url": "https://x.com/a"})
    with pytest.raises(sqlite3.IntegrityError):
        store.add_article({"live_url": "https://x.com/a"})


def test_payload_size_is_not_capped_at_store_layer(tmp_path):
    # 1.5 MB payload — exceeds plan §R16 1MB cap but the store layer
    # itself imposes no limit; the projector enforces it at U4. Test
    # name is the contract: do not "helpfully" add a cap here.
    big_blob = "x" * (1_500_000)
    store = EventStore()
    event_id = store.append(
        kind="publish.intent",
        payload={"big": big_blob, "target_url": "https://x.com/a"},
    )
    rows = store.query(
        "SELECT payload_json FROM events WHERE id = ?", (event_id,)
    )
    assert len(rows) == 1
    assert big_blob in rows[0]["payload_json"]


def test_connect_rebuilds_schema_after_db_deletion(tmp_path):
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    assert store.path.exists()

    # Delete the database file and any WAL sidecars.
    for p in [store.path,
              store.path.with_name(store.path.name + "-wal"),
              store.path.with_name(store.path.name + "-shm")]:
        if p.exists():
            p.unlink()
    assert not store.path.exists()

    # Next connect should recreate the schema cleanly.
    event_id = store.append(
        kind="publish.intent",
        payload={"after": "reset", "target_url": "https://x.com/a"},
    )
    assert event_id == 1, "fresh DB should restart AUTOINCREMENT at 1"
    rows = store.query("SELECT version FROM schema_version")
    assert rows[0]["version"] == 3


def test_schema_version_zero_upgrades_to_two(tmp_path):
    # Simulate an operator who provisioned the file with the table but
    # without the version row.
    store = EventStore()
    with store.connect() as conn:
        conn.execute("DELETE FROM schema_version")

    # Next connect should bring it to the current version.
    with store.connect() as conn:
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == 3


def test_wal_mode_is_enabled(tmp_path):
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    with store.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_db_file_mode_is_0600_on_first_create(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX file mode is not meaningful on Windows")
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    mode = store.path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_parent_dir_mode_is_0700_on_first_create(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("POSIX dir mode is not meaningful on Windows")
    nested = tmp_path / "fresh" / "config"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(nested))
    assert not nested.exists()
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    assert nested.exists()
    mode = nested.stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_parent_dir_tightened_when_pre_existing_with_wider_mode(
    tmp_path, monkeypatch,
):
    # Regression for SEC-1: mkdir(exist_ok=True) does NOT apply mode= to
    # an already-existing directory, so without the explicit chmod the
    # parent stays 0o755 next to persona.salt / token/.
    if sys.platform == "win32":
        pytest.skip("POSIX dir mode is not meaningful on Windows")
    pre = tmp_path / "preexists"
    pre.mkdir(mode=0o755)
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(pre))
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    mode = pre.stat().st_mode & 0o777
    assert mode == 0o700, f"expected 0o700, got {oct(mode)}"


def test_wal_and_shm_side_files_are_0600(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX file mode is not meaningful on Windows")
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    # Open a second connect to force WAL/SHM materialisation if not
    # already present after a single commit.
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/b"})
    for suffix in ("-wal", "-shm"):
        side = store.path.with_name(store.path.name + suffix)
        if side.exists():
            mode = side.stat().st_mode & 0o777
            assert mode == 0o600, (
                f"{side.name} mode {oct(mode)} leaks event payloads"
            )


def test_macos_xattr_attempted_on_first_create(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("xattr is macOS-only")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    assert any(
        "com.apple.metadata:com_apple_backup_excludeItem" in cmd
        for cmd in calls
    ), f"expected xattr write, got {calls}"


def test_macos_xattr_failure_is_silent(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("xattr is macOS-only")

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("xattr not in PATH")

    monkeypatch.setattr(subprocess, "run", fake_run)
    store = EventStore()
    # Must not propagate the FileNotFoundError to the caller.
    event_id = store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    assert event_id == 1


def test_retry_runs_three_times_then_succeeds_on_transient_error():
    # Unit test the retry layer directly — sqlite3.Connection.execute is
    # immutable so we can't monkeypatch it at the type level; we exercise
    # the same code path by handing _retry_sqlite a synthetic op.
    sleeps: list[float] = []
    call_n = {"n": 0}

    def op() -> str:
        call_n["n"] += 1
        if call_n["n"] <= 2:
            raise sqlite3.OperationalError("disk I/O error")
        return "ok"

    result = store_module._retry_sqlite(
        op, max_retries=3, sleep_fn=lambda s: sleeps.append(s)
    )
    assert result == "ok"
    assert call_n["n"] == 3, "expected 2 fails + 1 success = 3 attempts"
    assert sleeps == [
        store_module._BASE_BACKOFF_S * 1,
        store_module._BASE_BACKOFF_S * 2,
    ], "backoff should grow linearly between retries"


def test_retry_raises_after_max_attempts():
    call_n = {"n": 0}

    def op() -> None:
        call_n["n"] += 1
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        store_module._retry_sqlite(op, max_retries=3, sleep_fn=lambda _s: None)
    assert call_n["n"] == 3, "must stop after max_retries attempts"


def test_append_actually_routes_through_retry_layer(tmp_path, monkeypatch):
    # Adversarial concern: the unit tests for _retry_sqlite prove the
    # helper works in isolation, but a refactor that omits the wrapper
    # from append() would still pass them. This test patches
    # sqlite3.connect (which IS patchable, unlike Connection.execute)
    # so a real call to append fails twice then succeeds, exercising
    # the wired-in retry path end-to-end.
    real_connect = sqlite3.connect
    call_n = {"n": 0}

    def flaky_connect(*args, **kwargs):
        call_n["n"] += 1
        if call_n["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", flaky_connect)
    store = EventStore(sleep_fn=lambda _s: None)
    event_id = store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    assert event_id > 0
    assert call_n["n"] >= 3, "append() should route through _retry_sqlite"


def test_query_rejects_non_select_statement(tmp_path):
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    with pytest.raises(ValueError, match="SELECT-only"):
        store.query("DROP TABLE events")
    with pytest.raises(ValueError, match="SELECT-only"):
        store.query("INSERT INTO events (ts_raw, ts_utc, kind, payload_json) "
                    "VALUES ('x', 'x', 'k', '{}')")
    with pytest.raises(ValueError, match="SELECT-only"):
        store.query("ATTACH DATABASE '/tmp/evil.db' AS evil")
    # Multi-statement attempt also rejected — even when starting with SELECT.
    with pytest.raises(ValueError, match="SELECT-only"):
        store.query("SELECT 1; DROP TABLE events")


def test_query_accepts_select_and_with_select(tmp_path):
    store = EventStore()
    store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})
    rows = store.query("SELECT COUNT(*) AS n FROM events")
    assert rows[0]["n"] == 1
    rows = store.query(
        "WITH recent AS (SELECT * FROM events) SELECT COUNT(*) AS n FROM recent"
    )
    assert rows[0]["n"] == 1


def test_schema_too_new_raises_on_open(tmp_path):
    # Adversarial concern ADV-1: a v(N+1) binary writes version=2; a v1
    # binary opening that DB must refuse rather than silently project
    # incompatible rows.
    from backlink_publisher.events import schema as schema_module

    store = EventStore()
    with store.connect() as conn:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (99,))
    with pytest.raises(schema_module.SchemaTooNewError):
        store.append(kind="publish.intent", payload={"target_url": "https://x.com/a"})


def test_retry_does_not_retry_non_transient_error():
    call_n = {"n": 0}

    def op() -> None:
        call_n["n"] += 1
        raise sqlite3.OperationalError("no such table: events")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store_module._retry_sqlite(op, max_retries=3, sleep_fn=lambda _s: None)
    assert call_n["n"] == 1, "non-transient errors must surface immediately"


def test_append_and_add_article_share_transaction(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        article_id = store.add_article(
            {"live_url": "https://op.blogspot.com/y"}, conn=conn
        )
        event_id = store.append(
            kind="publish.confirmed",
            payload={"live_url": "https://op.blogspot.com/y"},
            article_id=article_id,
            conn=conn,
        )
    # Both visible after commit.
    rows = store.query("SELECT COUNT(*) AS n FROM events WHERE id = ?", (event_id,))
    assert rows[0]["n"] == 1
    rows = store.query(
        "SELECT COUNT(*) AS n FROM articles WHERE article_id = ?", (article_id,)
    )
    assert rows[0]["n"] == 1


def test_transaction_rolls_back_on_exception(tmp_path):
    store = EventStore()
    store.add_article({"live_url": "https://op.blogspot.com/z"})  # baseline

    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as conn:
            store.append(
                kind="publish.intent",
                payload={"step": "before failure", "target_url": "https://x.com/a"},
                conn=conn,
            )
            # This will violate the live_url UNIQUE constraint and abort
            # the transaction; the event INSERT above must NOT persist.
            store.add_article(
                {"live_url": "https://op.blogspot.com/z"}, conn=conn
            )

    rows = store.query(
        "SELECT COUNT(*) AS n FROM events WHERE payload_json LIKE ?",
        ('%before failure%',),
    )
    assert rows[0]["n"] == 0


# --- concurrency -----------------------------------------------------------


def _child_writer(db_path: str, payload_marker: str) -> None:
    """Helper for the cross-process concurrency test (top-level so it can
    be pickled by multiprocessing on macOS spawn start method)."""
    from backlink_publisher.events.store import EventStore as _ES
    from pathlib import Path as _P

    s = _ES(path=_P(db_path))
    s.append(
        kind="publish.intent",
        payload={"marker": payload_marker, "target_url": "https://x.com/a"},
    )


def test_two_processes_can_append_serially(tmp_path):
    # Plan §U1: "並发两个进程同时 append——busy_timeout 起作用". This is
    # a softer check: spawn two child processes that each write one
    # event; both must succeed without crashing. Real lock contention
    # requires WAL + busy_timeout, both enabled.
    store = EventStore()
    store.append(
        kind="publish.intent",
        payload={"warmup": True, "target_url": "https://x.com/a"},
    )  # create schema

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_child_writer, args=(str(store.path), f"p{i}"))
        for i in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
        assert p.exitcode == 0, f"child exited with {p.exitcode}"

    rows = store.query("SELECT COUNT(*) AS n FROM events")
    # 1 warmup + 2 from children
    assert rows[0]["n"] == 3
