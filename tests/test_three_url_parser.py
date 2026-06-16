"""Unit tests for config/parsers/three_url.py.

Covers the three extracted helpers (_parse_work_urls, _parse_work_templates,
_parse_blocklist) and the main orchestrator _parse_target_three_url.
All tested without I/O — inputs are plain dicts, outputs are pure values.
"""

from __future__ import annotations

import pytest

from backlink_publisher.config.parsers.three_url import (
    _parse_blocklist,
    _parse_target_three_url,
    _parse_work_templates,
    _parse_work_urls,
)
from backlink_publisher.config.types import DEFAULT_WORK_TEMPLATES


# ── _parse_work_urls ───────────────────────────────────────────────────────────


class TestParseWorkUrls:
    def test_empty_list_returns_empty(self) -> None:
        assert _parse_work_urls({}, "dom") == []

    def test_none_value_treated_as_empty(self) -> None:
        assert _parse_work_urls({"work_urls": None}, "dom") == []

    def test_non_list_treated_as_empty(self) -> None:
        assert _parse_work_urls({"work_urls": "https://x.com/"}, "dom") == []

    def test_valid_https_urls_returned(self) -> None:
        result = _parse_work_urls(
            {"work_urls": ["https://a.com/post", "https://b.com/page"]}, "dom"
        )
        assert result == ["https://a.com/post", "https://b.com/page"]

    def test_http_url_dropped(self) -> None:
        result = _parse_work_urls({"work_urls": ["http://a.com/post"]}, "dom")
        assert result == []

    def test_non_string_element_dropped(self) -> None:
        result = _parse_work_urls({"work_urls": [42, "https://a.com/p"]}, "dom")
        assert result == ["https://a.com/p"]

    def test_mixed_valid_invalid(self) -> None:
        urls = ["https://good.com/p", "http://bad.com/p", "https://also-good.com/q"]
        result = _parse_work_urls({"work_urls": urls}, "dom")
        assert result == ["https://good.com/p", "https://also-good.com/q"]


# ── _parse_work_templates ──────────────────────────────────────────────────────


class TestParseWorkTemplates:
    def test_none_returns_defaults(self) -> None:
        assert _parse_work_templates({}, "dom") == list(DEFAULT_WORK_TEMPLATES)

    def test_explicit_none_returns_defaults(self) -> None:
        assert _parse_work_templates({"work_anchor_templates": None}, "dom") == list(DEFAULT_WORK_TEMPLATES)

    def test_valid_list_returned(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": ["tmpl A", "tmpl B"]}, "dom")
        assert result == ["tmpl A", "tmpl B"]

    def test_strips_whitespace_from_templates(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": ["  tmpl  "]}, "dom")
        assert result == ["tmpl"]

    def test_empty_strings_filtered_out(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": ["", "  ", "real"]}, "dom")
        assert result == ["real"]

    def test_all_empty_strings_falls_back_to_defaults(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": ["", "  "]}, "dom")
        assert result == list(DEFAULT_WORK_TEMPLATES)

    def test_non_list_falls_back_to_defaults(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": "not a list"}, "dom")
        assert result == list(DEFAULT_WORK_TEMPLATES)

    def test_list_with_non_string_falls_back_to_defaults(self) -> None:
        result = _parse_work_templates({"work_anchor_templates": ["ok", 42]}, "dom")
        assert result == list(DEFAULT_WORK_TEMPLATES)


# ── _parse_blocklist ───────────────────────────────────────────────────────────


class TestParseBlocklist:
    def test_absent_key_returns_none(self) -> None:
        assert _parse_blocklist({}, "dom") is None

    def test_explicit_none_returns_none(self) -> None:
        assert _parse_blocklist({"list_path_blocklist": None}, "dom") is None

    def test_valid_list_returned(self) -> None:
        result = _parse_blocklist({"list_path_blocklist": ["/tag", "/category"]}, "dom")
        assert result == ["/tag", "/category"]

    def test_empty_strings_filtered_out(self) -> None:
        result = _parse_blocklist({"list_path_blocklist": ["/ok", ""]}, "dom")
        assert result == ["/ok"]

    def test_non_list_returns_none(self) -> None:
        assert _parse_blocklist({"list_path_blocklist": "/tag"}, "dom") is None

    def test_list_with_non_string_returns_none(self) -> None:
        assert _parse_blocklist({"list_path_blocklist": ["/ok", 42]}, "dom") is None


# ── _parse_target_three_url ────────────────────────────────────────────────────


def _minimal_entry() -> dict:
    return {
        "main_url": "https://example.com/",
        "list_url": "https://example.com/blog",
        "branded_pool": ["brand"],
        "partial_pool": ["partial kw"],
        "exact_pool": ["exact kw"],
    }


class TestParseTargetThreeUrl:
    def test_non_dict_section_returns_empty(self) -> None:
        assert _parse_target_three_url(None) == {}
        assert _parse_target_three_url([]) == {}

    def test_entry_without_three_url_keys_ignored(self) -> None:
        section = {"example.com": {"anchor_keywords": ["kw"]}}
        assert _parse_target_three_url(section) == {}

    def test_entry_with_non_dict_value_skipped(self) -> None:
        section = {"example.com": "not a dict"}
        assert _parse_target_three_url(section) == {}

    def test_missing_main_url_skipped(self) -> None:
        entry = _minimal_entry()
        del entry["main_url"]
        entry["list_url"] = "https://example.com/blog"
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_invalid_main_url_skipped(self) -> None:
        entry = _minimal_entry()
        entry["main_url"] = "http://example.com/"  # http not allowed
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_missing_list_url_skipped(self) -> None:
        entry = _minimal_entry()
        del entry["list_url"]
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_empty_branded_pool_skipped(self) -> None:
        entry = _minimal_entry()
        entry["branded_pool"] = []
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_empty_partial_pool_skipped(self) -> None:
        entry = _minimal_entry()
        entry["partial_pool"] = []
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_empty_exact_pool_skipped(self) -> None:
        entry = _minimal_entry()
        entry["exact_pool"] = []
        assert _parse_target_three_url({"example.com": entry}) == {}

    def test_valid_entry_parsed(self) -> None:
        result = _parse_target_three_url({"example.com": _minimal_entry()})
        assert "https://example.com" in result
        cfg = result["https://example.com"]
        assert cfg.main_url == "https://example.com/"
        assert cfg.list_url == "https://example.com/blog"
        assert cfg.branded_pool == ["brand"]

    def test_work_urls_passed_through(self) -> None:
        entry = {**_minimal_entry(), "work_urls": ["https://example.com/post1"]}
        result = _parse_target_three_url({"example.com": entry})
        cfg = result["https://example.com"]
        assert cfg.work_urls == ["https://example.com/post1"]

    def test_insecure_tls_flag(self) -> None:
        entry = {**_minimal_entry(), "insecure_tls": True}
        cfg = _parse_target_three_url({"example.com": entry})["https://example.com"]
        assert cfg.insecure_tls is True

    def test_key_strips_trailing_slash(self) -> None:
        result = _parse_target_three_url({"example.com": _minimal_entry()})
        assert "https://example.com" in result
        assert "https://example.com/" not in result

    def test_multiple_entries_all_parsed(self) -> None:
        entry_a = _minimal_entry()
        entry_b = {
            "main_url": "https://other.com/",
            "list_url": "https://other.com/posts",
            "branded_pool": ["other brand"],
            "partial_pool": ["kw"],
            "exact_pool": ["ex"],
        }
        result = _parse_target_three_url({"a.com": entry_a, "b.com": entry_b})
        assert "https://example.com" in result
        assert "https://other.com" in result
