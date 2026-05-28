"""Schema-shape tests for ``events.db``.

Asserts the structural commitments of plan §U1: table set, index set,
schema_version contract. Failures here mean a future migration changed
shape without updating the test — the fix is to confirm intent and
update the asserted set.
"""

from __future__ import annotations

import sqlite3

import pytest

from backlink_publisher.events.store import EventStore


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _index_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_all_business_tables_created(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        tables = _table_names(conn)
    assert tables == {
        "events",
        "articles",
        "projection_cursor",
        "quarantine_log",
        "schema_version",
        "publish_leases",
    }


def test_all_indices_created(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        indices = _index_names(conn)
    # The named indices declared in schema.py + the auto-created
    # sqlite_autoindex_* indices for UNIQUE constraints (articles.live_url).
    explicit = {
        "idx_events_kind_ts",
        "idx_events_host_kind",
        "idx_events_article_kind",
        "idx_articles_host_pub",
        "idx_articles_run",
    }
    assert explicit <= indices, f"missing: {explicit - indices}"


def test_events_columns_match_plan(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        cols = _column_names(conn, "events")
    assert cols == [
        "id",
        "ts_raw",
        "ts_utc",
        "run_id",
        "kind",
        "target_url",
        "host",
        "article_id",
        "payload_json",
    ]


def test_articles_columns_match_plan(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        cols = _column_names(conn, "articles")
    assert cols == [
        "article_id",
        "body",
        "anchors_json",
        "target_urls_json",
        "lang",
        "host",
        "live_url",
        "published_at_raw",
        "published_at_utc",
        "run_id",
    ]


def test_projection_cursor_columns_match_plan(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        cols = _column_names(conn, "projection_cursor")
    assert cols == [
        "source",
        "last_mtime",
        "last_checksum",
        "last_seen_state_json",
    ]


def test_quarantine_log_columns_match_plan(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        cols = _column_names(conn, "quarantine_log")
    # dedup_key added for the idempotent quarantine write path
    # (Plan 2026-05-26-001 Unit 2).
    assert cols == [
        "id",
        "ts_utc",
        "source",
        "run_id",
        "reason",
        "raw_payload_json",
        "dedup_key",
        "row_id",
    ]


def test_schema_version_initialized_to_two(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        rows = list(conn.execute("SELECT version FROM schema_version"))
    # Bumped to 3 when quarantine_log.row_id was added
    # (Plan 2026-05-28-004).
    assert rows == [(3,)]


def test_events_kind_is_not_null(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        # The plan declares ``kind`` NOT NULL; attempting to insert NULL
        # must fail. (We bypass EventStore.append which never passes NULL.)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO events (ts_raw, ts_utc, kind, payload_json) "
                "VALUES (?, ?, ?, ?)",
                ("2026", "2026", None, "{}"),
            )


def test_articles_live_url_unique(tmp_path):
    store = EventStore()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO articles (live_url) VALUES (?)",
            ("https://x.com/dup",),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO articles (live_url) VALUES (?)",
                ("https://x.com/dup",),
            )
