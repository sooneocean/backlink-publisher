"""Unit tests for events._project_helpers.

Covers the pure / near-pure helpers shared by all three reducers:
detect_source, split_iso_with_offset, split_local_naive, read_json,
extract_anchors, host_of, article_payload, checkpoint_event_timestamp.

cursor_load / cursor_save / write_quarantines require a live EventStore
connection and are exercised via projector integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backlink_publisher.events._project_helpers import (
    ProjectionError,
    article_payload,
    checkpoint_event_timestamp,
    detect_source,
    extract_anchors,
    host_of,
    read_json,
    split_iso_with_offset,
    split_local_naive,
)


# ── detect_source ──────────────────────────────────────────────────────────────

class TestDetectSource:
    def test_history_filename(self, tmp_path: Path) -> None:
        p = tmp_path / "publish-history.json"
        assert detect_source(p) == "history"

    def test_drafts_filename(self, tmp_path: Path) -> None:
        p = tmp_path / "draft-queue.json"
        assert detect_source(p) == "drafts"

    def test_checkpoint_run_id_stem(self, tmp_path: Path) -> None:
        p = tmp_path / "20260529T120000-abcd1234.json"
        assert detect_source(p) == "checkpoint"

    def test_unknown_filename_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "some-random-file.json"
        with pytest.raises(ProjectionError, match="cannot detect source"):
            detect_source(p)

    def test_checkpoint_stem_lowercase_hex(self, tmp_path: Path) -> None:
        p = tmp_path / "20260101T000000-00000000.json"
        assert detect_source(p) == "checkpoint"

    def test_checkpoint_stem_must_match_exactly(self, tmp_path: Path) -> None:
        p = tmp_path / "20260529T120000-ABCD1234.json"  # uppercase hex
        with pytest.raises(ProjectionError):
            detect_source(p)


# ── split_iso_with_offset ──────────────────────────────────────────────────────

class TestSplitIsoWithOffset:
    def test_utc_string_round_trips(self) -> None:
        raw, utc = split_iso_with_offset("2026-05-29T12:00:00+00:00")
        assert raw == "2026-05-29T12:00:00+00:00"
        assert "2026-05-29" in utc
        assert "12:00:00" in utc

    def test_offset_converted_to_utc(self) -> None:
        _raw, utc = split_iso_with_offset("2026-05-29T20:00:00+08:00")
        assert "12:00:00" in utc  # +08:00 → UTC = 12:00

    def test_naive_datetime_treated_as_utc(self) -> None:
        raw, utc = split_iso_with_offset("2026-05-29T12:00:00")
        assert raw == "2026-05-29T12:00:00"
        assert "12:00:00" in utc

    def test_returns_raw_unchanged(self) -> None:
        raw_in = "2026-01-01T00:00:00+00:00"
        raw_out, _ = split_iso_with_offset(raw_in)
        assert raw_out == raw_in

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            split_iso_with_offset("not-a-date")


# ── split_local_naive ──────────────────────────────────────────────────────────

class TestSplitLocalNaive:
    def test_returns_raw_unchanged(self) -> None:
        raw, _ = split_local_naive("2026-05-29 12:00")
        assert raw == "2026-05-29 12:00"

    def test_utc_output_is_iso(self) -> None:
        _, utc = split_local_naive("2026-05-29 12:00")
        # Just assert it's a parseable ISO string with T separator
        assert "T" in utc

    def test_wrong_format_raises(self) -> None:
        with pytest.raises(ValueError):
            split_local_naive("2026-05-29")  # missing HH:MM

    def test_wrong_format_slash_raises(self) -> None:
        with pytest.raises(ValueError):
            split_local_naive("29/05/2026 12:00")


# ── read_json ──────────────────────────────────────────────────────────────────

class TestReadJson:
    def test_valid_json_list(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text('[{"id": "x"}]', encoding="utf-8")
        result = read_json(p)
        assert result == [{"id": "x"}]

    def test_valid_json_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "data.json"
        p.write_text('{"key": 1}', encoding="utf-8")
        assert read_json(p) == {"key": 1}

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert read_json(p) is None

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.json"
        with pytest.raises(Exception):  # FileNotFoundError
            read_json(p)


# ── extract_anchors ────────────────────────────────────────────────────────────

class TestExtractAnchors:
    def test_filters_to_main_domain_and_target(self) -> None:
        payload = {
            "links": [
                {"kind": "main_domain", "anchor": "text"},
                {"kind": "target", "anchor": "text2"},
                {"kind": "other", "anchor": "text3"},
            ]
        }
        result = extract_anchors(payload)
        assert len(result) == 2
        assert all(r["kind"] in ("main_domain", "target") for r in result)

    def test_drops_links_without_anchor(self) -> None:
        payload = {
            "links": [
                {"kind": "main_domain"},           # no anchor
                {"kind": "main_domain", "anchor": ""},   # empty anchor
                {"kind": "target", "anchor": "ok"},
            ]
        }
        result = extract_anchors(payload)
        assert len(result) == 1
        assert result[0]["anchor"] == "ok"

    def test_non_dict_payload_returns_empty(self) -> None:
        assert extract_anchors([]) == []
        assert extract_anchors("string") == []
        assert extract_anchors(None) == []

    def test_missing_links_key_returns_empty(self) -> None:
        assert extract_anchors({}) == []

    def test_non_list_links_returns_empty(self) -> None:
        assert extract_anchors({"links": "not a list"}) == []

    def test_non_dict_link_entries_skipped(self) -> None:
        payload = {"links": ["string", 42, None, {"kind": "target", "anchor": "ok"}]}
        result = extract_anchors(payload)
        assert len(result) == 1


# ── host_of ────────────────────────────────────────────────────────────────────

class TestHostOf:
    def test_extracts_host(self) -> None:
        assert host_of("https://example.com/path") == "example.com"

    def test_with_port(self) -> None:
        assert host_of("https://example.com:8080/path") == "example.com:8080"

    def test_none_returns_none(self) -> None:
        assert host_of(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert host_of("") is None

    def test_no_netloc_returns_none(self) -> None:
        assert host_of("not-a-url") is None


# ── article_payload ────────────────────────────────────────────────────────────

class TestArticlePayload:
    def test_required_fields_present(self) -> None:
        p = article_payload(
            live_url="https://example.com/article",
            target_url="https://target.com",
            host="example.com",
        )
        assert "anchors_json" in p
        assert "target_urls_json" in p
        assert "host" in p
        assert "live_url" in p

    def test_target_url_serialized_as_json_list(self) -> None:
        p = article_payload(
            live_url=None, target_url="https://target.com", host=None
        )
        parsed = json.loads(p["target_urls_json"])
        assert parsed == ["https://target.com"]

    def test_none_target_url_gives_empty_list(self) -> None:
        p = article_payload(live_url=None, target_url=None, host=None)
        assert json.loads(p["target_urls_json"]) == []

    def test_live_url_canonicalized(self) -> None:
        p = article_payload(
            live_url="https://example.com/article?utm_source=x",
            target_url=None,
            host=None,
        )
        assert "utm_source" not in (p["live_url"] or "")

    def test_none_live_url_stays_none(self) -> None:
        p = article_payload(live_url=None, target_url=None, host=None)
        assert p["live_url"] is None

    def test_optional_fields_included_when_provided(self) -> None:
        p = article_payload(
            live_url=None, target_url=None, host=None,
            body="body text", run_id="run-1", lang="en",
            published_at_raw="2026-05-29 12:00",
            published_at_utc="2026-05-29T12:00:00+00:00",
        )
        assert p["body"] == "body text"
        assert p["run_id"] == "run-1"
        assert p["lang"] == "en"
        assert p["published_at_raw"] == "2026-05-29 12:00"
        assert "published_at_utc" in p

    def test_optional_fields_absent_when_not_provided(self) -> None:
        p = article_payload(live_url=None, target_url=None, host=None)
        assert "body" not in p
        assert "run_id" not in p
        assert "lang" not in p


# ── checkpoint_event_timestamp ─────────────────────────────────────────────────

class TestCheckpointEventTimestamp:
    def test_uses_completed_at_when_present(self) -> None:
        item = {"completed_at": "2026-05-29T12:00:00+00:00"}
        raw, utc = checkpoint_event_timestamp(item, "2026-05-29T10:00:00+00:00")
        assert raw == "2026-05-29T12:00:00+00:00"

    def test_falls_back_to_started_at(self) -> None:
        item = {}
        raw, utc = checkpoint_event_timestamp(item, "2026-05-29T10:00:00+00:00")
        assert raw == "2026-05-29T10:00:00+00:00"

    def test_bad_completed_at_falls_back_to_started_at(self) -> None:
        item = {"completed_at": "not-a-date"}
        raw, utc = checkpoint_event_timestamp(item, "2026-05-29T10:00:00+00:00")
        assert raw == "2026-05-29T10:00:00+00:00"

    def test_both_missing_returns_none_none(self) -> None:
        raw, utc = checkpoint_event_timestamp({}, "")
        assert raw is None
        assert utc is None

    def test_empty_completed_at_falls_back(self) -> None:
        item = {"completed_at": ""}
        raw, utc = checkpoint_event_timestamp(item, "2026-05-29T10:00:00+00:00")
        assert raw == "2026-05-29T10:00:00+00:00"
