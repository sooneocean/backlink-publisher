"""Characterization tests for ``validate_output_payload``.

Locks the observable behaviour (exact error strings + append ordering) of the
thinly-covered validation blocks BEFORE the N2 decomposition refactor
(``docs/plans/2026-05-26-005-refactor-decompose-validate-output-payload-plan.md``).

These assert against the *current* implementation. The decomposition (Unit 2)
must keep every one of them green — they are the behaviour-identity net for an
otherwise pure structural extraction.

Well-covered blocks (seo.canonical_url regex, one-of groups, content_html cap,
publish wrapper) already have dedicated coverage in
``test_schema_seo_canonical_contract.py`` / ``test_schema_source_format.py`` and
are not duplicated here except where needed for ordering.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher.schema import (
    MAX_CONTENT_HTML_BYTES,
    validate_output_payload,
)


def _valid_row(**overrides: Any) -> dict[str, Any]:
    """A fully valid output row (errors == []). Override one aspect per test."""
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
        "content_markdown": "An article mentioning https://example.com inline.",
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
    row.update(overrides)
    return row


class TestBaselineValid:
    def test_valid_row_has_no_errors(self) -> None:
        assert validate_output_payload(_valid_row()) == []


class TestRequiredFields:
    def test_missing_required_field(self) -> None:
        row = _valid_row()
        del row["platform"]
        assert "missing required output field 'platform'" in validate_output_payload(row)

    def test_wrong_type_required_field(self) -> None:
        row = _valid_row(title=123)
        errors = validate_output_payload(row)
        assert "field 'title' must be str, got int" in errors

    def test_missing_field_does_not_also_trigger_nonempty_check(self) -> None:
        row = _valid_row()
        del row["title"]
        errors = validate_output_payload(row)
        assert "missing required output field 'title'" in errors
        assert "title must not be empty" not in errors


class TestOptionalFieldTypes:
    def test_optional_field_wrong_type(self) -> None:
        # main_domain_normalized is optional; wrong type should be flagged.
        row = _valid_row(main_domain_normalized=123)
        assert "field 'main_domain_normalized' must be str, got int" in validate_output_payload(row)


class TestLinksStructure:
    def test_link_not_a_dict(self) -> None:
        row = _valid_row()
        row["links"][2] = "not-a-dict"
        assert "links[2] must be a dict" in validate_output_payload(row)

    def test_link_missing_each_required_field(self) -> None:
        for req in ("url", "anchor", "kind", "required"):
            row = _valid_row()
            del row["links"][0][req]
            assert f"links[0]: missing field '{req}'" in validate_output_payload(row)

    def test_link_invalid_url_format(self) -> None:
        row = _valid_row()
        row["links"][1]["url"] = "ftp://example.com"
        assert "links[1]: invalid URL format: ftp://example.com" in validate_output_payload(row)

    def test_link_invalid_kind(self) -> None:
        row = _valid_row()
        row["links"][1]["kind"] = "bogus"
        assert "links[1]: invalid kind 'bogus'" in validate_output_payload(row)


class TestLinkCount:
    def test_too_few_links(self) -> None:
        row = _valid_row()
        row["links"] = row["links"][:5]
        assert "link count 5 is not between 6 and 8" in validate_output_payload(row)

    def test_too_many_links(self) -> None:
        row = _valid_row()
        extra = {"url": "https://extra.com", "anchor": "X", "kind": "extra", "required": False}
        row["links"] = row["links"] + [extra, extra, extra]
        assert "link count 9 is not between 6 and 8" in validate_output_payload(row)

    def test_boundary_counts_pass(self) -> None:
        extra = {"url": "https://extra.com", "anchor": "X", "kind": "extra", "required": False}
        for n in (6, 8):
            row = _valid_row()
            base = row["links"][:6]
            row["links"] = base + [extra] * (n - 6)
            errors = validate_output_payload(row)
            assert not any("link count" in e for e in errors), f"n={n}: {errors}"


class TestNonEmptyTextFields:
    def test_whitespace_only_text_fields_rejected(self) -> None:
        for field, msg in (
            ("title", "title must not be empty"),
            ("excerpt", "excerpt must not be empty"),
            ("slug", "slug must not be empty"),
        ):
            row = _valid_row(**{field: "   \n\t"})
            assert msg in validate_output_payload(row), field


class TestSeoStructure:
    def test_seo_missing_field(self) -> None:
        row = _valid_row()
        del row["seo"]["description"]
        assert "seo: missing field 'description'" in validate_output_payload(row)

    def test_seo_field_wrong_type(self) -> None:
        row = _valid_row()
        row["seo"]["title"] = 123
        assert "seo.title must be a string" in validate_output_payload(row)


class TestContentHtmlSizeCap:
    def test_oversized_content_html_rejected(self) -> None:
        oversized = "<p>" + ("a" * (MAX_CONTENT_HTML_BYTES + 100)) + "</p>"
        # html-only row: one-of passes via content_html; main_domain markdown
        # path is skipped for html-only rows (see test_schema_source_format).
        row = _valid_row()
        del row["content_markdown"]
        row["content_html"] = oversized
        size = len(oversized.encode("utf-8"))
        errors = validate_output_payload(row)
        assert f"content_html size {size} bytes exceeds {MAX_CONTENT_HTML_BYTES} byte cap" in errors


class TestErrorOrdering:
    """Lock the exact ordered error list for a row failing multiple blocks.

    The decomposition extends `errors` with each helper's output in the original
    block sequence; this snapshot guarantees that order is preserved.
    """

    def test_multi_error_row_exact_ordered_output(self) -> None:
        row = _valid_row()
        row["title"] = "   "                       # non-empty block
        row["links"][0]["kind"] = "bogus"          # links block
        row["links"] = row["links"][:5]            # link-count block (now 5)
        del row["seo"]["canonical_url"]            # seo block
        errors = validate_output_payload(row)
        # Characterized against the current implementation (block order:
        # required → optional → one-of → html-size → links → seo → link-count →
        # title → excerpt → slug → main_domain).
        assert errors == [
            "links[0]: invalid kind 'bogus'",
            "seo: missing field 'canonical_url'",
            "link count 5 is not between 6 and 8",
            "title must not be empty",
        ], errors
