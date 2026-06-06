"""Unit tests for _schema_input and _schema_output validators.

Both modules are extracted from schema.py and have no direct test coverage.
Tests exercise the individual _check_* helpers and the aggregate validators.
"""

from __future__ import annotations

import pytest

from backlink_publisher._schema_input import (
    _check_input_enumerated_values,
    _check_input_optional_field_types,
    _check_input_required_fields,
    _check_input_seed_keywords,
    _check_input_urls_and_normalize,
    validate_input_payload,
)
from backlink_publisher._schema_output import (
    _check_content_html_size,
    _check_links_structure,
    _check_link_count,
    _check_nonempty_text_fields,
    _check_output_one_of_groups,
    _check_output_optional_field_types,
    _check_output_required_fields,
    _check_seo_structure,
    validate_output_payload,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _valid_input_row() -> dict:
    return {
        "target_url": "https://target.example.com/page",
        "main_domain": "https://main.example.com",
        "language": "en",
        "platform": "telegraph",
        "url_mode": "A",
        "publish_mode": "publish",
    }


def _make_link(url="https://example.com", anchor="text", kind="target", required=True):
    return {"url": url, "anchor": anchor, "kind": kind, "required": required}


def _valid_output_row() -> dict:
    links = [_make_link() for _ in range(6)]
    return {
        "id": "row-1",
        "platform": "telegraph",
        "language": "en",
        "publish_mode": "publish",
        "target_url": "https://target.example.com",
        "main_domain": "https://main.example.com",
        "url_mode": "A",
        "title": "Test Title",
        "slug": "test-title",
        "excerpt": "Short excerpt.",
        "tags": ["tag1"],
        "links": links,
        "seo": {
            "title": "SEO Title",
            "description": "SEO description.",
            "canonical_url": "",
        },
        "content_markdown": "Content with https://main.example.com linked.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# _schema_input
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckInputRequiredFields:
    def test_valid_row_no_errors(self):
        assert _check_input_required_fields(_valid_input_row(), 1) == []

    def test_missing_field_reported(self):
        row = _valid_input_row()
        del row["platform"]
        errs = _check_input_required_fields(row, 3)
        assert any("platform" in e for e in errs)
        assert all("line 3:" in e for e in errs)

    def test_wrong_type_reported(self):
        row = _valid_input_row()
        row["language"] = 42
        errs = _check_input_required_fields(row, 1)
        assert any("language" in e for e in errs)

    def test_all_fields_missing_gives_multiple_errors(self):
        errs = _check_input_required_fields({}, 1)
        assert len(errs) >= 6


class TestCheckInputEnumeratedValues:
    def test_valid_row_no_errors(self):
        assert _check_input_enumerated_values(_valid_input_row(), 1) == []

    def test_unsupported_language(self):
        row = {**_valid_input_row(), "language": "klingon"}
        errs = _check_input_enumerated_values(row, 1)
        assert any("language" in e and "klingon" in e for e in errs)

    def test_unsupported_platform(self):
        row = {**_valid_input_row(), "platform": "tiktok"}
        errs = _check_input_enumerated_values(row, 1)
        assert any("platform" in e and "tiktok" in e for e in errs)

    def test_invalid_url_mode(self):
        row = {**_valid_input_row(), "url_mode": "X"}
        errs = _check_input_enumerated_values(row, 1)
        assert any("url_mode" in e for e in errs)

    def test_invalid_publish_mode(self):
        row = {**_valid_input_row(), "publish_mode": "auto"}
        errs = _check_input_enumerated_values(row, 1)
        assert any("publish_mode" in e for e in errs)

    def test_all_valid_url_modes(self):
        for mode in ("A", "B", "C"):
            row = {**_valid_input_row(), "url_mode": mode}
            assert _check_input_enumerated_values(row, 1) == []

    def test_both_publish_modes_valid(self):
        for mode in ("draft", "publish"):
            row = {**_valid_input_row(), "publish_mode": mode}
            assert _check_input_enumerated_values(row, 1) == []


class TestCheckInputUrlsAndNormalize:
    def test_valid_urls_no_errors(self):
        row = _valid_input_row()
        errs = _check_input_urls_and_normalize(row, 1)
        assert errs == []

    def test_non_http_target_url_rejected(self):
        row = {**_valid_input_row(), "target_url": "ftp://example.com"}
        errs = _check_input_urls_and_normalize(row, 1)
        assert any("target_url" in e for e in errs)

    def test_non_http_main_domain_rejected(self):
        row = {**_valid_input_row(), "main_domain": "example.com"}
        errs = _check_input_urls_and_normalize(row, 1)
        assert any("main_domain" in e for e in errs)

    def test_valid_url_stores_normalized(self):
        row = _valid_input_row()
        _check_input_urls_and_normalize(row, 1)
        assert "main_domain_normalized" in row

    def test_http_scheme_also_accepted(self):
        row = {**_valid_input_row(), "target_url": "http://example.com"}
        assert _check_input_urls_and_normalize(row, 1) == []


class TestCheckInputSeedKeywords:
    def test_valid_list_no_errors(self):
        row = {**_valid_input_row(), "seed_keywords": ["kw1", "kw2"]}
        assert _check_input_seed_keywords(row, 1) == []

    def test_non_string_item_error(self):
        row = {**_valid_input_row(), "seed_keywords": ["ok", 42]}
        errs = _check_input_seed_keywords(row, 1)
        assert any("seed_keywords" in e for e in errs)

    def test_missing_seed_keywords_no_error(self):
        assert _check_input_seed_keywords(_valid_input_row(), 1) == []

    def test_empty_list_no_error(self):
        row = {**_valid_input_row(), "seed_keywords": []}
        assert _check_input_seed_keywords(row, 1) == []


class TestValidateInputPayload:
    def test_fully_valid_row_returns_empty(self):
        assert validate_input_payload(_valid_input_row(), 1) == []

    def test_multiple_errors_collected(self):
        errs = validate_input_payload({}, 1)
        assert len(errs) >= 6  # all required fields missing

    def test_line_num_embedded_in_errors(self):
        row = _valid_input_row()
        del row["platform"]
        errs = validate_input_payload(row, 99)
        assert all("line 99:" in e for e in errs)


# ══════════════════════════════════════════════════════════════════════════════
# _schema_output
# ══════════════════════════════════════════════════════════════════════════════

class TestCheckOutputRequiredFields:
    def test_valid_row_no_errors(self):
        assert _check_output_required_fields(_valid_output_row()) == []

    def test_missing_field_reported(self):
        row = _valid_output_row()
        del row["title"]
        errs = _check_output_required_fields(row)
        assert any("title" in e for e in errs)

    def test_wrong_type_reported(self):
        row = _valid_output_row()
        row["tags"] = "not-a-list"
        errs = _check_output_required_fields(row)
        assert any("tags" in e for e in errs)


class TestCheckOutputOneOfGroups:
    def test_markdown_present_ok(self):
        row = {**_valid_output_row(), "content_markdown": "text"}
        row.pop("content_html", None)
        assert _check_output_one_of_groups(row) == []

    def test_html_present_ok(self):
        row = {**_valid_output_row(), "content_html": "<p>text</p>"}
        row.pop("content_markdown", None)
        assert _check_output_one_of_groups(row) == []

    def test_both_absent_error(self):
        row = _valid_output_row()
        row.pop("content_markdown", None)
        row.pop("content_html", None)
        errs = _check_output_one_of_groups(row)
        assert len(errs) == 1
        assert "content_markdown" in errs[0]


class TestCheckContentHtmlSize:
    def test_small_content_ok(self):
        row = {"content_html": "<p>short</p>"}
        assert _check_content_html_size(row) == []

    def test_oversized_content_rejected(self):
        row = {"content_html": "x" * (1_048_576 + 1)}
        errs = _check_content_html_size(row)
        assert len(errs) == 1
        assert "byte cap" in errs[0]

    def test_exactly_at_cap_ok(self):
        row = {"content_html": "x" * 1_048_576}
        assert _check_content_html_size(row) == []

    def test_no_content_html_ok(self):
        assert _check_content_html_size({}) == []


class TestCheckLinksStructure:
    def test_valid_links_no_errors(self):
        row = {"links": [_make_link() for _ in range(6)]}
        assert _check_links_structure(row) == []

    def test_non_dict_link_entry(self):
        row = {"links": ["not-a-dict"]}
        errs = _check_links_structure(row)
        assert any("links[0]" in e for e in errs)

    def test_missing_required_link_field(self):
        link = {"url": "https://example.com", "anchor": "text", "kind": "target"}
        # missing "required"
        errs = _check_links_structure({"links": [link]})
        assert any("required" in e for e in errs)

    def test_invalid_link_url_rejected(self):
        link = _make_link(url="ftp://example.com")
        errs = _check_links_structure({"links": [link]})
        assert any("invalid URL" in e for e in errs)

    def test_invalid_link_kind_rejected(self):
        link = _make_link(kind="bogus")
        errs = _check_links_structure({"links": [link]})
        assert any("kind" in e and "bogus" in e for e in errs)

    def test_all_valid_kinds_accepted(self):
        from backlink_publisher.schema import LINK_KINDS
        for kind in LINK_KINDS:
            link = _make_link(kind=kind)
            assert _check_links_structure({"links": [link]}) == []

    def test_no_links_key_ok(self):
        assert _check_links_structure({}) == []


class TestCheckLinkCount:
    def test_six_links_ok(self):
        row = {"links": [_make_link() for _ in range(6)]}
        assert _check_link_count(row) == []

    def test_eight_links_ok(self):
        row = {"links": [_make_link() for _ in range(8)]}
        assert _check_link_count(row) == []

    def test_five_links_rejected(self):
        row = {"links": [_make_link() for _ in range(5)]}
        errs = _check_link_count(row)
        assert any("5" in e for e in errs)

    def test_nine_links_rejected(self):
        row = {"links": [_make_link() for _ in range(9)]}
        errs = _check_link_count(row)
        assert any("9" in e for e in errs)

    def test_no_links_key_defaults_to_zero(self):
        errs = _check_link_count({})
        assert len(errs) == 1  # 0 not in [6,8]


class TestCheckSeoStructure:
    def _row_with_seo(self, **seo_kwargs):
        base = {"title": "T", "description": "D", "canonical_url": ""}
        return {"seo": {**base, **seo_kwargs}}

    def test_valid_seo_no_errors(self):
        assert _check_seo_structure(self._row_with_seo()) == []

    def test_missing_seo_field(self):
        errs = _check_seo_structure({"seo": {"title": "T", "description": "D"}})
        assert any("canonical_url" in e for e in errs)

    def test_valid_canonical_url_accepted(self):
        row = self._row_with_seo(canonical_url="https://example.com/page")
        assert _check_seo_structure(row) == []

    def test_empty_canonical_url_accepted(self):
        row = self._row_with_seo(canonical_url="")
        assert _check_seo_structure(row) == []

    def test_javascript_scheme_rejected(self):
        row = self._row_with_seo(canonical_url="javascript:alert(1)")
        errs = _check_seo_structure(row)
        assert any("canonical_url" in e for e in errs)

    def test_canonical_with_quotes_rejected(self):
        row = self._row_with_seo(canonical_url='https://example.com/"evil"')
        errs = _check_seo_structure(row)
        assert any("canonical_url" in e for e in errs)

    def test_canonical_with_angle_brackets_rejected(self):
        row = self._row_with_seo(canonical_url="https://example.com/<script>")
        errs = _check_seo_structure(row)
        assert any("canonical_url" in e for e in errs)

    def test_canonical_with_whitespace_rejected(self):
        row = self._row_with_seo(canonical_url="https://example.com/pa ge")
        errs = _check_seo_structure(row)
        assert any("canonical_url" in e for e in errs)

    def test_no_seo_key_ok(self):
        assert _check_seo_structure({}) == []


class TestCheckNonemptyTextFields:
    def test_non_empty_fields_ok(self):
        row = {"title": "T", "excerpt": "E", "slug": "s"}
        assert _check_nonempty_text_fields(row) == []

    def test_whitespace_only_title_rejected(self):
        errs = _check_nonempty_text_fields({"title": "   "})
        assert any("title" in e for e in errs)

    def test_whitespace_only_excerpt_rejected(self):
        errs = _check_nonempty_text_fields({"excerpt": "\t\n"})
        assert any("excerpt" in e for e in errs)

    def test_missing_fields_not_checked(self):
        assert _check_nonempty_text_fields({}) == []


class TestValidateOutputPayload:
    def test_fully_valid_row_returns_empty(self):
        assert validate_output_payload(_valid_output_row()) == []

    def test_invalid_row_collects_multiple_errors(self):
        errs = validate_output_payload({})
        assert len(errs) >= 5

    def test_seo_xss_error_bubbles_up(self):
        row = _valid_output_row()
        row["seo"]["canonical_url"] = "javascript:evil()"
        errs = validate_output_payload(row)
        assert any("canonical_url" in e for e in errs)

    def test_link_count_error_bubbles_up(self):
        row = _valid_output_row()
        row["links"] = row["links"][:3]  # only 3 links
        errs = validate_output_payload(row)
        assert any("link count" in e for e in errs)
