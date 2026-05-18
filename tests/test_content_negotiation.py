"""Tests for backlink_publisher.publishing.content_negotiation.

Plan 2026-05-18-006 Unit 5 R9 + R10 — verifies:
- ROUTE_TIER_MATRIX covers SUPPORTED_PLATFORMS (drift assertion at import)
- route_tier_for normalizes input and default-denies unknown platforms
- extract_publish_html returns content_html for tier (a) platforms,
  renders content_markdown otherwise
- Tier sealing: adapters never need to know the tier vocabulary
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher.publishing.content_negotiation import (
    ROUTE_TIER_MATRIX,
    extract_publish_html,
    route_tier_for,
)
from backlink_publisher.schema import SUPPORTED_PLATFORMS


class TestRouteTierMatrixShape:
    """Drift assertion + matrix content per the R10 spike."""

    def test_blogger_is_tier_a(self) -> None:
        assert ROUTE_TIER_MATRIX["blogger"] == "a"

    def test_medium_is_tier_b(self) -> None:
        # MediumAPI alone is tier (a) but the fallback chain
        # (MediumBrave/MediumBrowser) drops it to tier (b)
        # under most-restrictive-tier rollup.
        assert ROUTE_TIER_MATRIX["medium"] == "b"

    def test_matrix_covers_all_supported_platforms(self) -> None:
        """Every SUPPORTED_PLATFORMS entry must have an explicit tier."""
        for platform in SUPPORTED_PLATFORMS:
            assert platform in ROUTE_TIER_MATRIX, (
                f"SUPPORTED_PLATFORMS has '{platform}' but ROUTE_TIER_MATRIX "
                f"does not classify it. Add a tier (a/b/c) entry."
            )


class TestRouteTierFor:
    """route_tier_for input normalization + default-deny."""

    def test_known_platform_lookup(self) -> None:
        assert route_tier_for("blogger") == "a"
        assert route_tier_for("medium") == "b"

    def test_case_insensitive_lookup(self) -> None:
        assert route_tier_for("BLOGGER") == "a"
        assert route_tier_for("Blogger") == "a"

    def test_whitespace_stripped(self) -> None:
        assert route_tier_for(" blogger ") == "a"
        assert route_tier_for("\tmedium\n") == "b"

    def test_unknown_platform_defaults_to_c(self) -> None:
        """Fail-closed: unknown platforms (telegraph not yet integrated,
        future wordpress/substack, typos like 'bloggerr') get tier (c)
        so content_html is rejected at validate-time rather than silently
        forwarded to an unverified path."""
        assert route_tier_for("wordpress") == "c"
        assert route_tier_for("telegraph") == "c"
        assert route_tier_for("bloggerr") == "c"  # typo
        assert route_tier_for("") == "c"

    def test_non_string_defaults_to_c(self) -> None:
        """Defensive: non-string input (None, int, dict) returns tier (c)."""
        assert route_tier_for(None) == "c"  # type: ignore[arg-type]
        assert route_tier_for(123) == "c"  # type: ignore[arg-type]


class TestExtractPublishHtmlTierA:
    """Tier (a) routes (blogger): content_html forwarded verbatim when present."""

    def test_content_html_returned_verbatim(self) -> None:
        payload = {
            "content_markdown": "Markdown source",
            "content_html": "<p>HTML source</p>",
        }
        assert extract_publish_html(payload, "blogger") == "<p>HTML source</p>"

    def test_content_html_only_returned(self) -> None:
        payload = {"content_html": "<p>HTML only</p>"}
        assert extract_publish_html(payload, "blogger") == "<p>HTML only</p>"

    def test_content_html_xss_payload_forwarded_verbatim(self) -> None:
        """Forwarder-role contract: adapter does NOT sanitize. Platform
        sanitize is the actual defense. This test locks the invariant —
        if a future PR adds adapter-side sanitize, this test fails."""
        payload = {"content_html": "<p>safe</p><script>alert(1)</script>"}
        result = extract_publish_html(payload, "blogger")
        assert "<script>" in result
        assert "alert(1)" in result

    def test_markdown_rendered_when_content_html_absent(self) -> None:
        """Tier (a) row with no content_html → renders content_markdown
        (legacy path bit-exact)."""
        payload = {"content_markdown": "**bold** text"}
        result = extract_publish_html(payload, "blogger")
        assert "<strong>" in result or "<b>" in result

    def test_empty_content_html_falls_through_to_markdown(self) -> None:
        """Empty string content_html is treated as absent (matches
        _is_field_present semantics from Unit 1)."""
        payload = {
            "content_html": "",
            "content_markdown": "fallback markdown",
        }
        result = extract_publish_html(payload, "blogger")
        # Empty content_html doesn't satisfy _is_field_present;
        # markdown gets rendered to HTML
        assert "<p>fallback markdown</p>" in result

    def test_whitespace_only_content_html_falls_through(self) -> None:
        """Whitespace-only content_html treated as absent."""
        payload = {
            "content_html": "   \n\t",
            "content_markdown": "fallback markdown",
        }
        result = extract_publish_html(payload, "blogger")
        assert "<p>fallback markdown</p>" in result


class TestExtractPublishHtmlTierB:
    """Tier (b) routes (medium): always render markdown, content_html ignored.

    Defense in depth — validate-time gate (Unit 6) rejects content_html-only
    medium rows. This helper's behavior is the second line of defense in case
    a row reaches publish-time with content_html anyway.
    """

    def test_content_html_ignored_markdown_rendered(self) -> None:
        payload = {
            "content_markdown": "Markdown source",
            "content_html": "<p>HTML source — ignored</p>",
        }
        result = extract_publish_html(payload, "medium")
        # Markdown gets rendered, NOT content_html returned verbatim
        assert "Markdown source" in result
        assert "HTML source — ignored" not in result

    def test_empty_payload_returns_empty_html(self) -> None:
        """Edge: no content fields → renders empty markdown → empty HTML."""
        assert extract_publish_html({}, "medium") == ""

    def test_markdown_only_renders(self) -> None:
        payload = {"content_markdown": "**bold**"}
        result = extract_publish_html(payload, "medium")
        assert "<strong>" in result or "<b>" in result


class TestExtractPublishHtmlTierC:
    """Tier (c) routes (unknown/future platforms): default-deny — content_html
    ignored, markdown rendered defensively."""

    def test_unknown_platform_ignores_content_html(self) -> None:
        payload = {
            "content_markdown": "fallback",
            "content_html": "<p>ignored</p>",
        }
        result = extract_publish_html(payload, "wordpress")
        assert "fallback" in result
        assert "ignored" not in result


class TestPlatformInputNormalization:
    """Adapter call sites are trusted to pass canonical platform strings,
    but the helper defensively normalizes anyway (matches route_tier_for)."""

    def test_case_variations(self) -> None:
        payload = {"content_html": "<p>html</p>"}
        # All variants resolve to tier (a) → content_html returned
        assert extract_publish_html(payload, "BLOGGER") == "<p>html</p>"
        assert extract_publish_html(payload, "Blogger") == "<p>html</p>"
        assert extract_publish_html(payload, " blogger ") == "<p>html</p>"
