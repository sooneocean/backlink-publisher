"""Characterization tests for ``validate_input_payload``.

Locks the observable behaviour (exact error strings incl. the ``line N:`` prefix,
append ordering, and the ``main_domain_normalized`` side effect) BEFORE the
decomposition refactor (sibling of the N2 ``validate_output_payload`` work).

Asserts against the current implementation. The decomposition must keep every
one of these green — it is the behaviour-identity net for a pure structural
extraction.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher.schema import validate_input_payload


def _valid_input(**overrides: Any) -> dict[str, Any]:
    """A valid input seed row (errors == []). Override one aspect per test."""
    row: dict[str, Any] = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "blogger",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    row.update(overrides)
    return row


class TestBaselineValid:
    def test_valid_row_has_no_errors(self) -> None:
        assert validate_input_payload(_valid_input(), 1) == []

    def test_valid_row_sets_main_domain_normalized_side_effect(self) -> None:
        # Load-bearing side effect: a valid main_domain stores its normalized
        # punycode form on the row for downstream Unit-6 host-parse.
        row = _valid_input(main_domain="https://Example.COM")
        validate_input_payload(row, 1)
        assert "main_domain_normalized" in row
        assert row["main_domain"] == "https://Example.COM"  # original preserved


class TestRequiredFields:
    def test_missing_required_field_uses_line_num(self) -> None:
        row = _valid_input()
        del row["platform"]
        assert "line 7: missing required field 'platform'" in validate_input_payload(row, 7)

    def test_wrong_type_required_field(self) -> None:
        row = _valid_input(language=123)
        assert "line 3: field 'language' must be str" in validate_input_payload(row, 3)


class TestOptionalFieldTypes:
    def test_optional_field_wrong_type(self) -> None:
        row = _valid_input(seed_keywords="not-a-list")
        assert "line 2: field 'seed_keywords' must be list" in validate_input_payload(row, 2)


class TestEnumeratedValues:
    def test_unsupported_language(self) -> None:
        row = _valid_input(language="ja")
        assert any(
            "line 1: unsupported language 'ja'" in e for e in validate_input_payload(row, 1)
        )

    def test_unsupported_platform(self) -> None:
        row = _valid_input(platform="xyznonexistent")
        assert any(
            "line 1: unsupported platform 'xyznonexistent'" in e
            for e in validate_input_payload(row, 1)
        )

    def test_invalid_url_mode(self) -> None:
        row = _valid_input(url_mode="Z")
        assert any("line 1: invalid url_mode 'Z'" in e for e in validate_input_payload(row, 1))

    def test_invalid_publish_mode(self) -> None:
        row = _valid_input(publish_mode="schedule")
        assert any(
            "line 1: invalid publish_mode 'schedule'" in e
            for e in validate_input_payload(row, 1)
        )


class TestUrlValidationAndNormalization:
    def test_non_http_target_url(self) -> None:
        row = _valid_input(target_url="ftp://example.com")
        assert "line 4: field 'target_url' is not a valid URL: ftp://example.com" in validate_input_payload(row, 4)

    def test_main_domain_normalization_failure_is_per_row_error(self) -> None:
        # Matches ^https?:// but has no host → _normalize_main_domain raises
        # ValueError, which must become a per-row error (not a SystemExit).
        row = _valid_input(main_domain="https://")
        errors = validate_input_payload(row, 9)
        assert any(
            "line 9: field 'main_domain' could not be normalized" in e for e in errors
        )


class TestSeedKeywords:
    def test_non_string_seed_keyword_item(self) -> None:
        row = _valid_input(seed_keywords=["ok", 123])
        assert "line 5: 'seed_keywords' items must be strings" in validate_input_payload(row, 5)


class TestErrorOrdering:
    """Lock the exact ordered error list for a row failing several blocks."""

    def test_multi_error_row_exact_ordered_output(self) -> None:
        row = _valid_input()
        del row["url_mode"]               # required-field block
        row["language"] = "ja"            # enumerated block
        row["target_url"] = "ftp://x"     # url block
        errors = validate_input_payload(row, 2)
        # Characterized against the current implementation (block order:
        # required → optional → enumerated(language/platform/url_mode/publish_mode)
        # → urls → seed_keywords).
        assert errors == [
            "line 2: missing required field 'url_mode'",
            "line 2: unsupported language 'ja'. Supported: en, ko, ru, zh-CN",
            "line 2: field 'target_url' is not a valid URL: ftp://x",
        ], errors
