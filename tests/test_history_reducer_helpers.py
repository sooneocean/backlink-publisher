"""Unit tests for helpers extracted from the history reducer.

Covers _parse_row_timestamps and _emit_confirmed_history_row.
All tests run without I/O — helpers are injected with mock store/conn.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, call

import pytest

from backlink_publisher.events._project_reducers import (
    _emit_confirmed_history_row,
    _parse_row_timestamps,
)
from backlink_publisher.events import kinds


# ── _parse_row_timestamps ──────────────────────────────────────────────────────


class TestParseRowTimestamps:
    def test_empty_string_returns_none_pair(self):
        assert _parse_row_timestamps("") == (None, None)

    def test_valid_datetime_returns_raw_and_utc(self):
        ts_raw, ts_utc = _parse_row_timestamps("2026-05-18 12:30")
        assert ts_raw == "2026-05-18 12:30"
        assert ts_utc is not None
        assert "+00:00" in ts_utc

    def test_garbage_string_returns_original_as_raw_none_utc(self):
        ts_raw, ts_utc = _parse_row_timestamps("not-a-date")
        assert ts_raw == "not-a-date"
        assert ts_utc is None

    def test_date_only_no_time_raises_falls_back(self):
        ts_raw, ts_utc = _parse_row_timestamps("bad-ts-xyz")
        assert ts_raw == "bad-ts-xyz"
        assert ts_utc is None

    def test_iso_datetime_roundtrip(self):
        ts_raw, ts_utc = _parse_row_timestamps("2025-01-01 00:00")
        assert ts_raw == "2025-01-01 00:00"
        assert ts_utc is not None


# ── _emit_confirmed_history_row ───────────────────────────────────────────────


def _make_store_conn():
    """Return (mock_store, mock_conn) pair with add_article returning a fake ID."""
    store = MagicMock()
    store.add_article.return_value = 42
    conn = MagicMock()
    return store, conn


class TestEmitConfirmedHistoryRowNoArticles:
    """Paths where article_urls is absent, empty, or non-list."""

    def _call(self, article_urls, row=None):
        store, conn = _make_store_conn()
        row = row or {"platform": "medium"}
        pq: list = []
        result = _emit_confirmed_history_row(
            row, article_urls,
            target_url="https://t.example.com",
            host="t.example.com",
            language="en",
            ts_raw="2026-05-18 12:30",
            ts_utc="2026-05-18T04:30:00+00:00",
            store=store,
            conn=conn,
            pending_quarantines=pq,
        )
        return result, store, conn, pq

    def test_none_article_urls_returns_always_mark_true(self):
        result, store, *_ = self._call(None)
        assert result == (1, 0, 0, True)

    def test_empty_list_returns_always_mark_true(self):
        result, store, *_ = self._call([])
        assert result == (1, 0, 0, True)

    def test_non_list_string_returns_always_mark_true(self):
        result, store, *_ = self._call("not-a-list")
        assert result == (1, 0, 0, True)

    def test_no_articles_calls_append_with_live_url_none(self):
        _, store, conn, pq = self._call([])
        store.append.assert_called_once_with(
            kinds.PUBLISH_CONFIRMED,
            {"live_url": None, "target_url": "https://t.example.com", "platform": "medium"},
            target_url="https://t.example.com",
            host="t.example.com",
            ts_raw="2026-05-18 12:30",
            ts_utc="2026-05-18T04:30:00+00:00",
            conn=conn,
            pending_quarantines=pq,
        )

    def test_no_articles_add_article_not_called(self):
        _, store, *_ = self._call([])
        store.add_article.assert_not_called()


class TestEmitConfirmedHistoryRowWithArticles:
    """Paths where article_urls contains valid URLs."""

    def _call(self, article_urls, *, add_article_side_effect=None):
        store, conn = _make_store_conn()
        if add_article_side_effect:
            store.add_article.side_effect = add_article_side_effect
        row = {"platform": "medium"}
        pq: list = []
        result = _emit_confirmed_history_row(
            row, article_urls,
            target_url="https://t.example.com",
            host="t.example.com",
            language="zh-CN",
            ts_raw="2026-05-18 12:30",
            ts_utc="2026-05-18T04:30:00+00:00",
            store=store,
            conn=conn,
            pending_quarantines=pq,
        )
        return result, store, conn, pq

    def test_single_valid_url_returns_one_event_one_article(self):
        result, *_ = self._call(["https://medium.com/@op/post-1"])
        ev, art, sk, always_mark = result
        assert ev == 1
        assert art == 1
        assert sk == 0
        assert always_mark is False

    def test_two_valid_urls_returns_two_events(self):
        result, *_ = self._call([
            "https://medium.com/@op/post-1",
            "https://medium.com/@op/post-2",
        ])
        assert result[:2] == (2, 2)

    def test_invalid_entry_skipped_not_counted(self):
        result, store, *_ = self._call([42, None, "https://medium.com/@op/post"])
        ev, art, sk, _ = result
        assert ev == 1
        assert art == 1
        assert sk == 0
        assert store.add_article.call_count == 1

    def test_empty_string_url_skipped(self):
        result, store, *_ = self._call([""])
        assert result[:3] == (0, 0, 0)
        store.add_article.assert_not_called()

    def test_integrity_error_counts_as_skip(self):
        result, *_ = self._call(
            ["https://medium.com/@op/post"],
            add_article_side_effect=sqlite3.IntegrityError("dup"),
        )
        ev, art, sk, always_mark = result
        assert ev == 0
        assert art == 0
        assert sk == 1
        assert always_mark is False

    def test_integrity_error_does_not_call_append(self):
        _, store, *_ = self._call(
            ["https://medium.com/@op/post"],
            add_article_side_effect=sqlite3.IntegrityError("dup"),
        )
        store.append.assert_not_called()

    def test_mixed_valid_and_dedup_urls(self):
        """First URL is a dup (IntegrityError), second emits normally."""
        store, conn = _make_store_conn()
        store.add_article.side_effect = [sqlite3.IntegrityError("dup"), 99]
        row = {"platform": "medium"}
        pq: list = []
        result = _emit_confirmed_history_row(
            row,
            ["https://medium.com/@op/dup", "https://medium.com/@op/new"],
            target_url="https://t.example.com",
            host="t.example.com",
            language="en",
            ts_raw=None,
            ts_utc=None,
            store=store,
            conn=conn,
            pending_quarantines=pq,
        )
        ev, art, sk, _ = result
        assert ev == 1
        assert art == 1
        assert sk == 1
