"""Unit tests for _util/url.py.

Stdlib-only module; all functions are pure URL transforms.
No mocking needed — every test is deterministic from input alone.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.url import (
    absolutize,
    canonicalize_url,
    is_same_host,
    normalize_url_for_fetch,
    safe_hostname,
    safe_urlparse,
    strip_fragment_query,
    validate_https_url,
    validate_main_domain_url,
)


# ── safe_urlparse ─────────────────────────────────────────────────────────────


class TestSafeUrlparse:
    def test_valid_url_returns_parse_result(self) -> None:
        r = safe_urlparse("https://example.com/path")
        assert r is not None
        assert r.scheme == "https"
        assert r.netloc == "example.com"

    def test_non_string_returns_none(self) -> None:
        assert safe_urlparse(123) is None  # type: ignore[arg-type]
        assert safe_urlparse(None) is None  # type: ignore[arg-type]

    def test_empty_string_returns_none(self) -> None:
        assert safe_urlparse("") is None

    def test_malformed_ipv6_returns_none(self) -> None:
        assert safe_urlparse("http://[invalid") is None


# ── safe_hostname ─────────────────────────────────────────────────────────────


class TestSafeHostname:
    def test_returns_hostname(self) -> None:
        assert safe_hostname("https://example.com/path") == "example.com"

    def test_none_input_returns_none(self) -> None:
        assert safe_hostname(None) is None  # type: ignore[arg-type]

    def test_malformed_returns_none(self) -> None:
        assert safe_hostname("http://[invalid") is None

    def test_url_with_port_strips_port(self) -> None:
        assert safe_hostname("https://example.com:8443/") == "example.com"


# ── validate_main_domain_url ──────────────────────────────────────────────────


class TestValidateMainDomainUrl:
    def test_none_returns_none(self) -> None:
        assert validate_main_domain_url(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert validate_main_domain_url("") is None

    def test_http_scheme_rejected(self) -> None:
        assert validate_main_domain_url("http://example.com/") is None

    def test_no_host_returns_none(self) -> None:
        assert validate_main_domain_url("https:///path") is None

    def test_non_root_path_rejected(self) -> None:
        assert validate_main_domain_url("https://example.com/blog") is None

    def test_deep_path_rejected(self) -> None:
        assert validate_main_domain_url("https://example.com/a/b/") is None

    def test_query_string_rejected(self) -> None:
        assert validate_main_domain_url("https://example.com/?foo=bar") is None

    def test_fragment_rejected(self) -> None:
        assert validate_main_domain_url("https://example.com/#anchor") is None

    def test_adds_trailing_slash(self) -> None:
        result = validate_main_domain_url("https://example.com")
        assert result == "https://example.com/"

    def test_preserves_existing_trailing_slash(self) -> None:
        result = validate_main_domain_url("https://example.com/")
        assert result == "https://example.com/"

    def test_strips_leading_whitespace(self) -> None:
        result = validate_main_domain_url("  https://example.com/  ")
        assert result == "https://example.com/"


# ── validate_https_url ────────────────────────────────────────────────────────


class TestValidateHttpsUrl:
    def test_none_returns_none(self) -> None:
        assert validate_https_url(None) is None

    def test_empty_returns_none(self) -> None:
        assert validate_https_url("") is None

    def test_http_rejected(self) -> None:
        assert validate_https_url("http://example.com/page") is None

    def test_no_host_returns_none(self) -> None:
        assert validate_https_url("https:///path") is None

    def test_valid_deep_path_preserved(self) -> None:
        result = validate_https_url("https://example.com/blog/post-1")
        assert result == "https://example.com/blog/post-1"

    def test_fragment_dropped(self) -> None:
        result = validate_https_url("https://example.com/page#section")
        assert result == "https://example.com/page"

    def test_query_string_preserved(self) -> None:
        result = validate_https_url("https://example.com/search?q=foo")
        assert result == "https://example.com/search?q=foo"

    def test_empty_path_gets_slash(self) -> None:
        result = validate_https_url("https://example.com")
        assert result is not None
        assert result.startswith("https://example.com")


# ── is_same_host ──────────────────────────────────────────────────────────────


class TestIsSameHost:
    def test_identical_hosts(self) -> None:
        assert is_same_host("https://example.com/a", "https://example.com/b")

    def test_case_insensitive(self) -> None:
        assert is_same_host("https://Example.COM/", "https://example.com/")

    def test_www_prefix_stripped(self) -> None:
        assert is_same_host("https://www.example.com/", "https://example.com/")

    def test_different_hosts(self) -> None:
        assert not is_same_host("https://foo.com/", "https://bar.com/")

    def test_different_ports_not_same(self) -> None:
        assert not is_same_host("https://example.com:8443/", "https://example.com/")

    def test_empty_a_returns_false(self) -> None:
        assert not is_same_host("", "https://example.com/")

    def test_empty_b_returns_false(self) -> None:
        assert not is_same_host("https://example.com/", "")

    def test_malformed_url_returns_false(self) -> None:
        assert not is_same_host("http://[invalid", "https://example.com/")


# ── absolutize ────────────────────────────────────────────────────────────────


class TestAbsolutize:
    def test_empty_href_returns_empty(self) -> None:
        assert absolutize("https://example.com/", "") == ""

    def test_absolute_href_returned_unchanged(self) -> None:
        assert absolutize("https://base.com/", "https://other.com/page") == "https://other.com/page"

    def test_relative_href_resolved(self) -> None:
        result = absolutize("https://example.com/a/b/", "../c")
        assert result == "https://example.com/a/c"

    def test_malformed_ipv6_in_base_returns_empty(self) -> None:
        assert absolutize("http://[invalid", "/path") == ""

    def test_root_relative_href(self) -> None:
        result = absolutize("https://example.com/deep/path/", "/top")
        assert result == "https://example.com/top"


# ── strip_fragment_query ──────────────────────────────────────────────────────


class TestStripFragmentQuery:
    def test_empty_returns_empty(self) -> None:
        assert strip_fragment_query("") == ""

    def test_query_stripped(self) -> None:
        result = strip_fragment_query("https://example.com/page?q=1&r=2")
        assert result == "https://example.com/page"

    def test_fragment_stripped(self) -> None:
        result = strip_fragment_query("https://example.com/page#section")
        assert result == "https://example.com/page"

    def test_both_stripped(self) -> None:
        result = strip_fragment_query("https://example.com/page?q=1#frag")
        assert result == "https://example.com/page"

    def test_path_preserved(self) -> None:
        result = strip_fragment_query("https://example.com/a/b/c")
        assert result == "https://example.com/a/b/c"

    def test_malformed_ipv6_returns_empty(self) -> None:
        assert strip_fragment_query("http://[invalid/path") == ""


# ── canonicalize_url ──────────────────────────────────────────────────────────


class TestCanonicalizeUrl:
    def test_empty_returns_empty(self) -> None:
        assert canonicalize_url("") == ""

    def test_non_http_scheme_unchanged(self) -> None:
        url = "mailto:user@example.com"
        assert canonicalize_url(url) == url

    def test_ftp_scheme_unchanged(self) -> None:
        url = "ftp://files.example.com/pub"
        assert canonicalize_url(url) == url

    def test_scheme_lowercased(self) -> None:
        result = canonicalize_url("HTTPS://Example.COM/path")
        assert result.startswith("https://")

    def test_host_lowercased(self) -> None:
        result = canonicalize_url("https://EXAMPLE.COM/path")
        assert "example.com" in result

    def test_default_https_port_443_stripped(self) -> None:
        result = canonicalize_url("https://example.com:443/page")
        assert ":443" not in result
        assert "example.com/page" in result

    def test_default_http_port_80_stripped(self) -> None:
        result = canonicalize_url("http://example.com:80/page")
        assert ":80" not in result

    def test_non_default_port_preserved(self) -> None:
        result = canonicalize_url("https://example.com:8443/page")
        assert ":8443" in result

    def test_trailing_slash_stripped_from_non_root_path(self) -> None:
        result = canonicalize_url("https://example.com/path/to/page/")
        assert result == "https://example.com/path/to/page"

    def test_root_slash_preserved(self) -> None:
        result = canonicalize_url("https://example.com/")
        assert result == "https://example.com/"

    def test_empty_path_unchanged(self) -> None:
        result = canonicalize_url("https://example.com")
        assert result == "https://example.com"

    def test_utm_source_dropped(self) -> None:
        result = canonicalize_url("https://example.com/page?utm_source=twitter&id=42")
        assert "utm_source" not in result
        assert "id=42" in result

    def test_utm_medium_dropped(self) -> None:
        result = canonicalize_url("https://example.com/page?utm_medium=cpc")
        assert "utm_medium" not in result

    def test_all_utm_params_dropped(self) -> None:
        url = "https://example.com/page?utm_source=a&utm_medium=b&utm_campaign=c&utm_term=d&utm_content=e"
        result = canonicalize_url(url)
        assert "utm_" not in result

    def test_non_utm_query_params_sorted_by_key(self) -> None:
        result = canonicalize_url("https://example.com/page?z=1&a=2")
        assert result == "https://example.com/page?a=2&z=1"

    def test_fragment_dropped(self) -> None:
        result = canonicalize_url("https://example.com/page#section")
        assert "#" not in result

    def test_idempotent(self) -> None:
        url = "https://example.com/path/to/page?b=2&a=1&utm_source=x#frag"
        once = canonicalize_url(url)
        twice = canonicalize_url(once)
        assert once == twice


# ── normalize_url_for_fetch ───────────────────────────────────────────────────


class TestNormalizeUrlForFetch:
    def test_empty_returns_empty(self) -> None:
        assert normalize_url_for_fetch("") == ""

    def test_ascii_url_unchanged(self) -> None:
        url = "https://example.com/path?q=foo"
        assert normalize_url_for_fetch(url) == url

    def test_non_http_scheme_unchanged(self) -> None:
        url = "ftp://example.com/file"
        assert normalize_url_for_fetch(url) == url

    def test_unicode_path_percent_encoded(self) -> None:
        result = normalize_url_for_fetch("https://example.com/한국어/post")
        assert "%" in result
        assert "한국어" not in result

    def test_fragment_dropped_for_non_ascii_url(self) -> None:
        # Fragment is dropped only in the non-ASCII code path.
        result = normalize_url_for_fetch("https://example.com/한국어#section")
        assert "#" not in result

    def test_idempotent_on_ascii(self) -> None:
        url = "https://example.com/path?q=foo"
        assert normalize_url_for_fetch(normalize_url_for_fetch(url)) == normalize_url_for_fetch(url)

    def test_already_percent_encoded_not_double_encoded(self) -> None:
        url = "https://example.com/%ED%95%9C%EA%B5%AD%EC%96%B4"
        result = normalize_url_for_fetch(url)
        assert "%25" not in result
