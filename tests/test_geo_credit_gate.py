"""Tests for the GEO credit gate (Plan 2026-05-29-006 Unit 5, D4).

The credit gate decides which source URLs from a ProbeResult are credited,
uncredited, or placed in the possibly_cited_unresolved bucket.  All matching
is pure string (no network I/O).

Scenarios:
- Valid http(s) host-match → credited.
- Hallucinated/garbled URL that merely string-contains the domain → uncredited
  (inflation prevention: canonicalize_url must produce a real matching host).
- Redirect-wrapper URL (host in REDIRECTOR_HOSTS) → possibly_cited_unresolved,
  NOT dropped and NOT in uncredited.
- Redirector with extractable destination that matches target → credited
  (after redirect resolution).
- Non-http(s) scheme → uncredited.
- Blank / empty URL → silently skipped (not in any list).
- Article-URL path match via canonicalization (utm stripped, trailing slash,
  http→https scheme differences).
"""

from __future__ import annotations

import pytest

from backlink_publisher.geo.engines import ProbeResult
from backlink_publisher.geo.verdict import (
    REDIRECTOR_HOSTS,
    classify_verdict,
)


def _probe(
    *,
    answer: str = "Some answer with sources.",
    urls: list[str],
    outcome: str = "ok",
) -> ProbeResult:
    return ProbeResult(
        answer_text=answer,
        source_urls=urls,
        raw_response={},
        outcome=outcome,
    )


_TARGET = "https://example.com"
_ARTICLES: frozenset[str] = frozenset(
    {
        "https://dev.to/user/my-example-post",
    }
)


def _classify(urls: list[str], *, target: str = _TARGET, articles: frozenset[str] = _ARTICLES):
    return classify_verdict(
        _probe(urls=urls),
        target_url=target,
        published_article_urls=articles,
        query="test",
        engine="perplexity",
    )


# ---------------------------------------------------------------------------
# Credited URLs (host match)
# ---------------------------------------------------------------------------

class TestCreditedUrls:
    def test_exact_host_match_is_credited(self):
        v = _classify(["https://example.com/page"])
        assert v.tier == "site_cited"
        assert any("example.com" in u for u in v.credited_urls)
        assert v.uncredited_urls == []

    def test_subdomain_is_not_credited_as_site(self):
        """sub.example.com != example.com — host match is exact."""
        v = _classify(["https://sub.example.com/page"])
        # sub.example.com host does not equal example.com
        # (Not in articles either, so absent)
        assert v.tier == "absent"
        assert v.uncredited_urls  # URL is uncredited, not dropped

    def test_http_host_match_is_credited(self):
        v = _classify(["http://example.com/page"])
        assert v.tier == "site_cited"

    def test_multiple_urls_one_match(self):
        v = _classify([
            "https://unrelated.org/page",
            "https://example.com/about",
        ])
        assert v.tier == "site_cited"
        assert any("example.com" in u for u in v.credited_urls)
        assert any("unrelated.org" in u for u in v.uncredited_urls)

    def test_article_url_match_is_credited(self):
        article = "https://dev.to/user/my-example-post"
        v = _classify([article])
        assert v.tier == "article_cited"
        assert article in v.credited_urls


# ---------------------------------------------------------------------------
# Inflation prevention: hallucinated / garbled URLs
# ---------------------------------------------------------------------------

class TestInflationPrevention:
    def test_garbled_url_string_containing_domain_not_credited(self):
        """A garbled URL that merely string-contains 'example.com' is NOT credited.

        The credit gate uses canonicalize_url + host comparison, not substring
        matching.  'https://evil.org/redirect?site=example.com' has host
        evil.org, not example.com.
        """
        garbled = "https://evil.org/redirect?site=example.com"
        v = _classify([garbled])
        assert v.tier == "absent"
        # The URL should appear in uncredited or unresolved, NOT in credited.
        assert all("evil.org" not in u or u != garbled for u in v.credited_urls)
        assert garbled not in v.credited_urls

    def test_url_with_domain_in_path_not_credited(self):
        """Domain in path component does not credit the URL."""
        tricky = "https://aggregator.net/sites/example.com/article"
        v = _classify([tricky])
        assert v.tier == "absent"
        assert tricky not in v.credited_urls

    def test_domain_in_query_param_not_credited(self):
        """Domain as a query parameter value does not credit the URL."""
        tricky = "https://tracker.io/click?target=example.com&id=123"
        v = _classify([tricky])
        assert v.tier == "absent"
        assert tricky not in v.credited_urls

    def test_non_https_scheme_not_credited(self):
        v = _classify(["ftp://example.com/file"])
        assert v.tier == "absent"
        # ftp URL lands in uncredited (non-http(s)).
        assert any("ftp://" in u for u in v.uncredited_urls)

    def test_empty_url_silently_ignored(self):
        v = _classify(["", "https://example.com/page"])
        assert v.tier == "site_cited"
        # Empty string must not appear in any output list.
        all_urls = v.credited_urls + v.uncredited_urls + v.possibly_cited_unresolved
        assert "" not in all_urls


# ---------------------------------------------------------------------------
# Redirector / aggregator URLs (asymmetry-aware, A5)
# ---------------------------------------------------------------------------

class TestRedirectorUrls:
    def test_known_redirector_host_goes_to_unresolved(self):
        """A URL whose host is in REDIRECTOR_HOSTS → possibly_cited_unresolved."""
        r_url = "https://t.co/SomeShortCode"
        v = _classify([r_url])
        assert r_url in v.possibly_cited_unresolved
        assert r_url not in v.credited_urls
        assert r_url not in v.uncredited_urls

    def test_bitly_url_goes_to_unresolved(self):
        v = _classify(["https://bit.ly/3xyzAbc"])
        assert v.possibly_cited_unresolved
        assert not v.credited_urls

    @pytest.mark.parametrize("host", sorted(REDIRECTOR_HOSTS))
    def test_all_known_redirectors_go_to_unresolved(self, host):
        url = f"https://{host}/somelink"
        v = _classify([url])
        assert url in v.possibly_cited_unresolved

    def test_redirector_with_extractable_destination_matching_target(self):
        """Redirector with ?url=<target> → destination is credited."""
        dest = "https://example.com/landing-page"
        r_url = f"https://bit.ly/click?url={dest}"
        v = _classify([r_url])
        # After extraction, the destination should be credited as site_cited.
        assert v.tier == "site_cited"
        # The destination (canonicalized) should be in credited_urls.
        assert any("example.com" in u for u in v.credited_urls)

    def test_redirector_without_extractable_destination_stays_unresolved(self):
        """Redirector with no parseable destination → unresolved, NOT dropped."""
        r_url = "https://t.co/UnknownCode"
        v = _classify([r_url])
        assert r_url in v.possibly_cited_unresolved
        assert r_url not in v.uncredited_urls  # not silently dropped

    def test_unresolved_does_not_give_site_cited(self):
        """A redirector URL alone is not enough to trigger site_cited."""
        v = _classify(["https://buff.ly/NoResolve"])
        assert v.tier == "absent"


# ---------------------------------------------------------------------------
# Canonicalization: utm / trailing-slash / http→https
# ---------------------------------------------------------------------------

class TestCanonicalization:
    def test_utm_stripped_for_article_match(self):
        stored = "https://dev.to/user/my-example-post"
        cited = stored + "?utm_source=perplexity"
        articles = frozenset({stored})
        v = _classify([cited], articles=articles)
        assert v.tier == "article_cited"

    def test_trailing_slash_stripped_for_article_match(self):
        stored = "https://dev.to/user/my-example-post"
        cited = stored + "/"
        articles = frozenset({stored})
        v = _classify([cited], articles=articles)
        assert v.tier == "article_cited"

    def test_utm_stripped_for_site_match(self):
        url = "https://example.com/page?utm_campaign=ai&utm_medium=cpc"
        v = _classify([url])
        assert v.tier == "site_cited"

    def test_scheme_case_insensitive(self):
        url = "HTTPS://EXAMPLE.COM/page"
        v = _classify([url])
        assert v.tier == "site_cited"
