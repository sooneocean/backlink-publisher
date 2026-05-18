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
                # Updated 2026-05-14 per plan 2026-05-14-001 R10:
                # --no-check-urls renamed to --no-validate-url-check (the old
                # name still works but emits a deprecation WARN that would
                # break the stderr-must-be-empty assertions).
                args.append("--no-validate-url-check")
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
    # Stderr contains only the always-on reconciliation line on success.
    assert "validate_reconciliation" in stderr
    assert "error" not in stderr.lower()
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


def test_validate_no_diagnostic_stderr_on_success():
    """On success, stderr must contain only the always-on reconciliation line
    + the operator-orientation config banner from Round-3 #7 — no
    error/warning diagnostics."""
    payload = _make_valid_payload()
    input_data = json.dumps(payload)
    stdout, stderr, code = _run_validate(input_data, check_urls=False)
    assert code == 0
    # Banner adds 5 lines ("[<cli>] effective config:" header + 4 fields).
    # Strip those before asserting only the reconciliation line remains.
    banner_prefixes = (
        "[validate-backlinks] effective config:",
        "  config:",
        "  env:",
        "  platforms:",
        "  sha:",
    )
    lines = [
        line for line in stderr.splitlines()
        if line.strip() and not any(line.startswith(p) for p in banner_prefixes)
    ]
    # Exactly one non-banner stderr line: the reconciliation event.
    assert len(lines) == 1, f"unexpected non-banner stderr: {lines}"
    record = json.loads(lines[0])
    assert record["msg"] == "validate_reconciliation"
    assert record["level"] == "RECON"


# ── Plan 2026-05-18-006 Unit 6: HTML dispatch, R3 host-parse, tier gate ────


def _make_ko_payload(
    *,
    content_markdown: str | None = None,
    content_html: str | None = None,
    platform: str = "blogger",
    main_domain: str = "https://example.com",
    main_domain_normalized: str = "example.com",
) -> dict:
    payload: dict = {
        "id": "ko-001",
        "platform": platform,
        "language": "ko",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": main_domain,
        "main_domain_normalized": main_domain_normalized,
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "An excerpt.",
        "tags": ["tag1"],
        "links": [
            {"url": "https://example.com", "anchor": "자세히 보기", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "한국어 학습", "kind": "target", "required": True},
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
    if content_markdown is not None:
        payload["content_markdown"] = content_markdown
    if content_html is not None:
        payload["content_html"] = content_html
    return payload


class TestKoMarkdownSource:
    """R15 dispatch: ko row with content_markdown only."""

    def test_ko_markdown_passes(self) -> None:
        payload = _make_ko_payload(
            content_markdown=(
                "안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다. "
                "참고: https://example.com"
            ),
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"
        output = json.loads(stdout.strip())
        assert output["validation"]["status"] == "passed"

    def test_ko_markdown_with_en_body_fails_language_gate(self) -> None:
        payload = _make_ko_payload(
            content_markdown=(
                "This is an English body that does not match ko language. "
                "Reference: https://example.com"
            ),
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "body language" in stderr
        assert "does not match" in stderr


class TestKoHtmlSource:
    """R15 dispatch: ko row with content_html only (blogger = tier a)."""

    def test_ko_html_passes_on_blogger(self) -> None:
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다.</p>"
                "<p>흥미로운 주제를 다룹니다. "
                '<a href="https://example.com">자세히 보기</a></p>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"
        output = json.loads(stdout.strip())
        assert output["validation"]["status"] == "passed"

    def test_ko_html_only_rejected_on_medium_tier_b(self) -> None:
        """Tier (b) gate: medium platform rejects content_html-only rows."""
        payload = _make_ko_payload(
            content_html=(
                '<p>안녕하세요.</p><a href="https://example.com">link</a>'
            ),
            platform="medium",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "does not yet accept content_html" in stderr

    def test_ko_html_with_script_body_english_stopwords_still_passes(self) -> None:
        """R4 4-step pipeline: <script> body containing English stopwords
        does not poison ko detection."""
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제입니다.</p>"
                "<script>const w = 'the of and to in that an at it';</script>"
                '<p>자세히 보기: <a href="https://example.com">링크</a></p>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"


class TestR3HostParseHtml:
    """R3: HTML main_domain host-parse rejects spoof / injection vectors."""

    def test_html_main_domain_in_href_passes(self) -> None:
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="https://example.com/article">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"

    def test_html_subdomain_spoof_fails(self) -> None:
        """`main-domain.com.evil.com` must not match `main-domain.com`."""
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="https://example.com.evil.com/article">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "main_domain" in stderr and "is not the host" in stderr

    def test_html_userinfo_injection_fails(self) -> None:
        """`https://example.com@evil.com/` host is evil.com (userinfo reject)."""
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="https://example.com@evil.com/article">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr

    def test_html_javascript_href_fails(self) -> None:
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="javascript:alert(1)">example.com</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr

    def test_html_main_domain_in_data_attribute_only_fails(self) -> None:
        """main_domain inside data-* attribute (no real <a href>) must fail."""
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<div data-source="example.com">자세히 보기</div>'
                '<a href="https://elsewhere.com/article">link</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr

    def test_html_subdomain_passes(self) -> None:
        """A real subdomain (`blog.example.com`) matches via suffix rule."""
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="https://blog.example.com/post">블로그 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"


class TestBothFieldsLanguageMismatch:
    """R3 + R15 strict mode: both-present rows surface explicit mismatch."""

    def test_both_fields_matching_language_passes(self) -> None:
        payload = _make_ko_payload(
            content_markdown=(
                "안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다. "
                "참고: https://example.com"
            ),
            content_html=(
                "<p>안녕하세요 한국어 기사입니다.</p>"
                '<a href="https://example.com">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 0, f"stderr: {stderr}"

    def test_both_fields_disagreeing_language_fails(self) -> None:
        """MD says ko, HTML says en — explicit error with both detections."""
        payload = _make_ko_payload(
            content_markdown=(
                "안녕하세요 한국어 기사입니다. 흥미로운 주제입니다. "
                "참고: https://example.com"
            ),
            content_html=(
                "<p>This is an English body but the row claims ko. "
                'Reference: <a href="https://example.com">link</a></p>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "mismatch" in stderr
        assert "md=" in stderr or "ko" in stderr


class TestR3AdversarialSchemes:
    """Plan 2026-05-18-006 Unit 8: scheme allowlist parametrized over the
    full set of non-http(s) schemes. Locks the contract that R3 host-parse
    rejects every scheme outside {http, https} regardless of how the URL
    is constructed."""

    @pytest.mark.parametrize(
        "scheme_prefix",
        [
            "javascript:alert(1)",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "data:text/html,<script>alert(2)</script>",
            "JAVASCRIPT:alert(3)",  # case-insensitive
            "//evil.com/",           # scheme-relative (no scheme)
            "mailto:attacker@evil.com",
        ],
    )
    def test_non_http_scheme_in_href_rejected(self, scheme_prefix: str) -> None:
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                f'<a href="{scheme_prefix}">example.kr</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr


class TestR3IdnHomographAndPunycode:
    """Plan 2026-05-18-006 Unit 8: IDN homograph + Punycode spoof rejection.

    Pass-2 security P1 — these were called out as missing adversarial
    fixtures. The byte-compare against the operator-normalized main_domain
    (Unit 1 _normalize_main_domain) is what catches both classes:
    Cyrillic-а vs Latin-a normalize to different punycode forms; an
    attacker-registered xn--main-domain-... that visually matches the
    operator's domain has different bytes after IDN-encode.
    """

    def test_cyrillic_homograph_in_href_fails(self) -> None:
        """Cyrillic 'а' (U+0430) inside an otherwise main-domain-shaped host:
        the IDN-encode produces a different punycode form than the
        operator's main_domain_normalized → byte-compare fails → reject."""
        # mаin-domain.com where 'а' is U+0430 CYRILLIC SMALL LETTER A
        # Operator's main_domain in payload is example.com (ASCII).
        homograph_host = "exаmple.com"  # 'а' = Cyrillic; visually "example.com"
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                f'<a href="https://{homograph_host}/post">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr

    def test_punycode_spoof_in_href_fails(self) -> None:
        """A pre-encoded xn--... host that decodes to something visually
        similar to the operator's main_domain but is byte-different after
        normalization → reject."""
        # xn--exmple-cua.com decodes to "exàmple.com" (with à) — different from
        # operator's "example.com" → byte-compare fails.
        payload = _make_ko_payload(
            content_html=(
                "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다.</p>"
                '<a href="https://xn--exmple-cua.com/post">자세히 보기</a>'
            ),
            platform="blogger",
        )
        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        assert code == 2
        assert "is not the host" in stderr


class TestNfcNormalization:
    """Plan 2026-05-18-006 Unit 6: NFC normalize row-resident strings at
    validate-time entry. Defends against macOS-NFD-decomposed Hangul that
    would otherwise fail the codepoint-range check."""

    def test_nfd_ko_anchor_normalized_and_passes(self) -> None:
        """An NFD-decomposed Hangul anchor (e.g. 자 split into ㅈ + ㅏ) gets
        NFC-normalized before the anchor_lang ko codepoint check."""
        import unicodedata

        payload = _make_ko_payload(
            content_markdown=(
                "안녕하세요 한국어 기사입니다. 흥미로운 주제를 다룹니다. "
                "참고: https://example.com"
            ),
        )
        # Replace the main_domain anchor with NFD-decomposed Hangul
        nfd_anchor = unicodedata.normalize("NFD", "자세히 보기")
        payload["links"][0]["anchor"] = nfd_anchor
        # Sanity: NFD form has zero Hangul Syllable codepoints
        nfd_syllables = sum(
            1 for c in nfd_anchor if 0xAC00 <= ord(c) <= 0xD7AF
        )
        assert nfd_syllables == 0

        stdout, stderr, code = _run_validate(json.dumps(payload), check_urls=False)
        # NFC normalize at _enhance_payload entry recomposes → ko anchor
        # codepoint check passes
        assert code == 0, f"stderr: {stderr}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])