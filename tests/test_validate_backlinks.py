"""Tests for validate-backlinks."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.cli.validate_backlinks import main
from backlink_publisher.linkcheck import ExternalServiceError


def _run_validate(input_data: str, check_urls: bool = True, argv: list[str] | None = None) -> tuple[str, str, int]:
    """Run validate-backlinks with given stdin data. Returns (stdout, stderr, exit_code)."""
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            args = []
            if not check_urls:
                args.append("--no-check-urls")
            main(argv or args)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _make_valid_payload(url_mode: str = "A", platform: str = "medium") -> dict:
    return {
        "id": "abc123",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": url_mode,
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": "This is a test article about https://example.com and some content here.",
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
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }


def test_validate_valid_payload():
    """A valid payload passes validation."""
    payload = _make_valid_payload()
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 0, f"Expected 0, got {code}. stderr: {stderr}"
    assert stderr == ""
    output = json.loads(stdout.strip())
    assert output["validation"]["status"] == "passed"
    assert "checked_at" in output["validation"]


def test_validate_fewer_than_5_links():
    """Payload with fewer than 5 links must fail."""
    payload = _make_valid_payload()
    payload["links"] = payload["links"][:4]
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2
    assert "link count" in stderr.lower() or "5" in stderr
    assert stdout == ""


def test_validate_more_than_8_links():
    """Payload with more than 8 links must fail."""
    payload = _make_valid_payload()
    payload["links"] = [
        {"url": f"https://site{i}.com", "anchor": f"Site {i}", "kind": "supporting", "required": False}
        for i in range(9)
    ]
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2
    assert stdout == ""


def test_validate_missing_main_domain_in_content():
    """Payload where main_domain is missing from content_markdown must fail."""
    payload = _make_valid_payload()
    payload["content_markdown"] = "This article has nothing about the main domain."
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2
    assert stdout == ""


def test_validate_empty_title():
    """Payload with empty title must fail."""
    payload = _make_valid_payload()
    payload["title"] = ""
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2
    assert stdout == ""


def test_validate_missing_seo_fields():
    """Payload missing SEO fields must fail."""
    payload = _make_valid_payload()
    del payload["seo"]["canonical_url"]
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2


def test_validate_missing_required_field():
    """Payload missing required field must fail."""
    payload = _make_valid_payload()
    del payload["title"]
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2


def test_validate_empty_input():
    """Empty input must produce error."""
    stdout, stderr, code = _run_validate("")
    assert code == 2
    assert stdout == ""


def test_validate_malformed_json():
    """Malformed JSON must produce error."""
    stdout, stderr, code = _run_validate("{broken\n")
    assert code == 2
    assert stdout == ""


def test_validate_linkedin_platform():
    """Payload with platform=linkedin must fail."""
    payload = _make_valid_payload(platform="linkedin")
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2
    assert "linkedin" in stderr.lower()
    assert stdout == ""


def test_validate_validates_url_format():
    """Payload with invalid URL format in links must fail."""
    payload = _make_valid_payload()
    payload["links"][0]["url"] = "not-a-url"
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 2


def test_validate_output_contains_validation_block():
    """Valid output must contain the validation block."""
    payload = _make_valid_payload()
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 0
    output = json.loads(stdout.strip())
    assert "validation" in output
    assert output["validation"]["status"] == "passed"
    assert isinstance(output["validation"]["checked_at"], str)
    assert isinstance(output["validation"]["warnings"], list)


def test_validate_preserves_original_payload():
    """Validation output must preserve all original payload fields."""
    payload = _make_valid_payload()
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 0
    output = json.loads(stdout.strip())
    for key in payload:
        if key != "validation":
            assert key in output, f"Missing original field: {key}"


def test_validate_all_url_modes():
    """All URL modes (A, B, C) must pass validation."""
    for mode in ("A", "B", "C"):
        payload = _make_valid_payload(url_mode=mode)
        input_data = json.dumps(payload)
        stdout, stderr, code = _run_validate(input_data, check_urls=False)
        assert code == 0, f"Mode {mode} failed: {stderr}"


def test_validate_no_stderr_on_success():
    """On success, stderr must be empty."""
    payload = _make_valid_payload()
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 0
    assert stderr == "", f"Expected empty stderr, got: {stderr!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])