"""Tests for schema.py source-format dispatch + cross-field predicates.

Plan 2026-05-18-006 Unit 1:
- R2: at-least-one cross-field predicate over (content_markdown, content_html)
- R6b: ko added to SUPPORTED_LANGUAGES (canonical source in language_check.py)
- main_domain normalization side effect on validate_input_payload
- MAX_CONTENT_HTML_BYTES schema-time cap (Threat Model DoS row)
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher.language_check import (
    SUPPORTED_LANGUAGES,
    language_matches,
)
from backlink_publisher.schema import (
    MAX_CONTENT_HTML_BYTES,
    OUTPUT_ONE_OF_GROUPS,
    SUPPORTED_LANGUAGES as schema_supported_languages,
    _is_field_present,
    _normalize_main_domain,
    validate_input_payload,
    validate_output_payload,
    validate_publish_payload,
)


# --------------------------------------------------------------------------- #
# R6b: SUPPORTED_LANGUAGES is the canonical source from language_check.       #
# --------------------------------------------------------------------------- #


class TestSupportedLanguagesCanonical:
    """schema.SUPPORTED_LANGUAGES must be the same frozenset object as
    language_check.SUPPORTED_LANGUAGES — proves the de-duplication."""

    def test_schema_imports_canonical_source(self) -> None:
        assert schema_supported_languages is SUPPORTED_LANGUAGES

    def test_ko_is_supported(self) -> None:
        assert "ko" in SUPPORTED_LANGUAGES

    def test_existing_languages_still_supported(self) -> None:
        for lang in ("zh-CN", "en", "ru"):
            assert lang in SUPPORTED_LANGUAGES

    def test_no_extra_languages(self) -> None:
        # ja / zh-TW intentionally out of scope per origin
        for lang in ("ja", "zh-TW", "de", "fr"):
            assert lang not in SUPPORTED_LANGUAGES


# --------------------------------------------------------------------------- #
# R6b: language_matches with ko pairs (Plan 2026-05-14-001 R1 already         #
# generic over the frozenset — extending the set extends behavior).           #
# --------------------------------------------------------------------------- #


class TestLanguageMatchesKo:
    def test_ko_ko_matches(self) -> None:
        assert language_matches("ko", "ko") is True

    def test_ko_does_not_match_zh_cn(self) -> None:
        assert language_matches("ko", "zh-CN") is False

    def test_ko_does_not_match_en(self) -> None:
        assert language_matches("ko", "en") is False

    def test_ko_does_not_match_ru(self) -> None:
        assert language_matches("ko", "ru") is False

    def test_zh_cn_does_not_match_ko(self) -> None:
        assert language_matches("zh-CN", "ko") is False

    def test_en_does_not_match_ko(self) -> None:
        assert language_matches("en", "ko") is False

    def test_unknown_ko_passes_escape_valve(self) -> None:
        # "unknown" escape valve preserved per Plan 2026-05-14-001 R3
        assert language_matches("unknown", "ko") is True
        assert language_matches("ko", "unknown") is True


# --------------------------------------------------------------------------- #
# Field-presence semantics shared with validate-time dispatch.                #
# --------------------------------------------------------------------------- #


class TestIsFieldPresent:
    def test_non_empty_string_is_present(self) -> None:
        assert _is_field_present("hello") is True

    def test_string_with_content_is_present(self) -> None:
        assert _is_field_present("<p>안녕</p>") is True

    def test_empty_string_is_absent(self) -> None:
        assert _is_field_present("") is False

    def test_whitespace_only_is_absent(self) -> None:
        assert _is_field_present("   ") is False
        assert _is_field_present("\t\n") is False

    def test_none_is_absent(self) -> None:
        assert _is_field_present(None) is False

    def test_non_string_is_absent(self) -> None:
        # Defensive: callers may pass arbitrary values
        assert _is_field_present(123) is False
        assert _is_field_present([]) is False
        assert _is_field_present({}) is False


# --------------------------------------------------------------------------- #
# main_domain normalization helper.                                            #
# --------------------------------------------------------------------------- #


class TestNormalizeMainDomain:
    def test_ascii_host_lowercased(self) -> None:
        result = _normalize_main_domain("https://Example.COM/")
        assert result == "https://example.com/"

    def test_trailing_dot_stripped(self) -> None:
        result = _normalize_main_domain("https://example.com./")
        assert result == "https://example.com/"

    def test_idn_unicode_host_punycode(self) -> None:
        # German umlaut → IDN punycode form
        result = _normalize_main_domain("https://löve.de/")
        # IDNA encoding: löve.de → xn--lve-1la.de (encoder-deterministic)
        assert result.startswith("https://xn--")
        assert result.endswith(".de/")

    def test_port_preserved(self) -> None:
        result = _normalize_main_domain("https://Example.com:8080/path")
        assert result == "https://example.com:8080/path"

    def test_path_and_query_preserved(self) -> None:
        result = _normalize_main_domain("https://Example.com/foo?bar=baz")
        assert result == "https://example.com/foo?bar=baz"

    def test_no_hostname_raises(self) -> None:
        with pytest.raises(ValueError, match="no parseable hostname"):
            _normalize_main_domain("https:///path")

    def test_label_too_long_raises(self) -> None:
        # IDN labels are capped at 63 octets — this is what _normalize_main_domain
        # catches via UnicodeError.
        long_label = "a" * 64
        with pytest.raises(ValueError, match="IDN-encode failed"):
            _normalize_main_domain(f"https://{long_label}.com/")


# --------------------------------------------------------------------------- #
# validate_input_payload: side effect of storing main_domain_normalized.      #
# --------------------------------------------------------------------------- #


def _valid_input_row() -> dict[str, Any]:
    """Minimal valid seed input row for validate_input_payload."""
    return {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "blogger",
        "url_mode": "A",
        "publish_mode": "draft",
    }


class TestValidateInputPayloadNormalization:
    def test_main_domain_normalized_stored_on_valid_row(self) -> None:
        row = _valid_input_row()
        errors = validate_input_payload(row, line_num=1)
        assert errors == []
        assert "main_domain_normalized" in row
        assert row["main_domain_normalized"] == "https://example.com"

    def test_main_domain_normalized_lowercases_unicode_host(self) -> None:
        row = _valid_input_row()
        row["main_domain"] = "https://Example.com"
        errors = validate_input_payload(row, line_num=1)
        assert errors == []
        assert row["main_domain_normalized"] == "https://example.com"

    def test_main_domain_raw_preserved(self) -> None:
        """Original main_domain stays operator-supplied for display."""
        row = _valid_input_row()
        row["main_domain"] = "https://Example.com"
        validate_input_payload(row, line_num=1)
        assert row["main_domain"] == "https://Example.com"

    def test_main_domain_idn_failure_is_per_row_error(self) -> None:
        """Bad main_domain does NOT raise (no SystemExit) — per-row error only."""
        row = _valid_input_row()
        row["main_domain"] = "https://" + "a" * 64 + ".com/"
        errors = validate_input_payload(row, line_num=42)
        assert any("could not be normalized" in e for e in errors)
        # No mutation when normalization fails
        assert "main_domain_normalized" not in row

    def test_ko_language_accepted_in_input(self) -> None:
        row = _valid_input_row()
        row["language"] = "ko"
        errors = validate_input_payload(row, line_num=1)
        assert errors == []

    def test_ja_language_still_rejected(self) -> None:
        # ja deferred to follow-up — must still be rejected
        row = _valid_input_row()
        row["language"] = "ja"
        errors = validate_input_payload(row, line_num=1)
        assert any("unsupported language 'ja'" in e for e in errors)


# --------------------------------------------------------------------------- #
# R2: OUTPUT_ONE_OF_GROUPS at-least-one cross-field predicate.                #
# --------------------------------------------------------------------------- #


def _valid_output_row(*, content_markdown: str | None = None, content_html: str | None = None) -> dict[str, Any]:
    """Minimal valid output row. Caller specifies which content fields exist."""
    row: dict[str, Any] = {
        "id": "abc123",
        "platform": "blogger",
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "An excerpt.",
        "tags": ["tag1"],
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description.",
            "canonical_url": "https://example.com/article",
        },
    }
    if content_markdown is not None:
        row["content_markdown"] = content_markdown
    if content_html is not None:
        row["content_html"] = content_html
    return row


class TestOutputOneOfGroups:
    def test_group_declares_content_pair(self) -> None:
        assert OUTPUT_ONE_OF_GROUPS == (("content_markdown", "content_html"),)

    def test_markdown_only_passes(self) -> None:
        row = _valid_output_row(content_markdown="An article mentioning https://example.com inline.")
        errors = validate_output_payload(row)
        assert errors == [], f"unexpected errors: {errors}"

    def test_html_only_passes(self) -> None:
        # main_domain check on HTML-only rows defers to validate_backlinks (Unit 6).
        # Schema-level test just verifies the at-least-one group fires.
        row = _valid_output_row(content_html="<p>An article mentioning <a href='https://example.com'>example</a>.</p>")
        errors = validate_output_payload(row)
        assert errors == [], f"unexpected errors: {errors}"

    def test_both_fields_present_passes(self) -> None:
        row = _valid_output_row(
            content_markdown="Article about https://example.com",
            content_html="<p>Article about <a href='https://example.com'>example</a></p>",
        )
        errors = validate_output_payload(row)
        assert errors == [], f"unexpected errors: {errors}"

    def test_neither_field_present_fails(self) -> None:
        row = _valid_output_row()
        errors = validate_output_payload(row)
        assert any(
            "at least one of ['content_markdown', 'content_html']" in e
            for e in errors
        )

    def test_whitespace_only_markdown_treated_as_absent(self) -> None:
        # _is_field_present semantics symmetric with Unit 6 dispatch
        row = _valid_output_row(content_markdown="   \n\t")
        errors = validate_output_payload(row)
        assert any(
            "at least one of ['content_markdown', 'content_html']" in e
            for e in errors
        )

    def test_whitespace_markdown_html_present_passes(self) -> None:
        row = _valid_output_row(
            content_markdown="   ",
            content_html="<p>example.com</p>",
        )
        # at-least-one passes because content_html is present; but main_domain
        # check for the markdown-substring path is skipped (markdown absent).
        errors = validate_output_payload(row)
        assert errors == [], f"unexpected errors: {errors}"


class TestPublishPayloadWrapsOutput:
    """validate_publish_payload wraps validate_output_payload — must inherit
    the at-least-one group behavior."""

    def test_publish_accepts_html_only_blogger(self) -> None:
        row = _valid_output_row(content_html="<p>example.com</p>")
        errors = validate_publish_payload(row)
        assert errors == []

    def test_publish_rejects_linkedin_platform(self) -> None:
        row = _valid_output_row(content_markdown="About https://example.com")
        row["platform"] = "linkedin"
        errors = validate_publish_payload(row)
        assert any("linkedin" in e for e in errors)


# --------------------------------------------------------------------------- #
# MAX_CONTENT_HTML_BYTES: schema-time DoS cap.                                #
# --------------------------------------------------------------------------- #


class TestContentHtmlSizeCap:
    def test_cap_constant_is_one_mib(self) -> None:
        assert MAX_CONTENT_HTML_BYTES == 1_048_576

    def test_html_below_cap_passes(self) -> None:
        # 100 KB of valid HTML
        row = _valid_output_row(
            content_html="<p>" + "About https://example.com " * 2000 + "</p>"
        )
        errors = validate_output_payload(row)
        # Content here is well under 1 MiB
        size = len(row["content_html"].encode("utf-8"))
        assert size < MAX_CONTENT_HTML_BYTES
        assert not any("exceeds" in e for e in errors)

    def test_html_above_cap_fails(self) -> None:
        # 2 MiB of HTML — must fail with explicit cap message
        oversized = "<p>" + "a" * (MAX_CONTENT_HTML_BYTES + 100) + "</p>"
        row = _valid_output_row(content_html=oversized)
        errors = validate_output_payload(row)
        assert any(
            f"content_html size" in e and f"exceeds {MAX_CONTENT_HTML_BYTES}" in e
            for e in errors
        )

    def test_html_at_cap_boundary_passes(self) -> None:
        # Exactly at cap — should pass (cap is exclusive upper bound semantics:
        # "exceeds" is strict > comparison)
        body_size = MAX_CONTENT_HTML_BYTES - len("<p></p>".encode("utf-8"))
        row = _valid_output_row(content_html="<p>" + "a" * body_size + "</p>")
        size = len(row["content_html"].encode("utf-8"))
        assert size == MAX_CONTENT_HTML_BYTES
        errors = validate_output_payload(row)
        # main_domain substring fails on this dummy body — only assert no cap error
        assert not any("exceeds" in e for e in errors)


# --------------------------------------------------------------------------- #
# Optional content_html type validation.                                       #
# --------------------------------------------------------------------------- #


class TestOptionalFieldTypes:
    def test_content_html_must_be_string(self) -> None:
        row = _valid_output_row(content_markdown="About https://example.com")
        row["content_html"] = 12345
        errors = validate_output_payload(row)
        assert any("content_html" in e and "must be str" in e for e in errors)

    def test_main_domain_normalized_must_be_string(self) -> None:
        row = _valid_output_row(content_markdown="About https://example.com")
        row["main_domain_normalized"] = ["not", "a", "string"]
        errors = validate_output_payload(row)
        assert any("main_domain_normalized" in e and "must be str" in e for e in errors)
