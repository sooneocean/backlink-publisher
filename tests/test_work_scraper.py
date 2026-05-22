"""Tests for backlink_publisher.work_scraper — Plan 2026-05-13-004 Unit 2.

Covers:
- fetch_work_metadata: HTML extraction, CJK encoding, length truncation,
  SSRF (private IP) blocking, body-size guards (Content-Length + streaming),
  5xx no-retry, 429 retry, fail-continue on network/parse error, insecure_tls.
- fetch_work_urls_from_list: sitemap.xml, sitemap_index recursion, HTML
  fallback, blocklist path filtering, max_candidates truncation,
  fail-empty vs fail-abort three-state semantics, custom blocklist.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import requests

from backlink_publisher.content import _http as scraper_http
from backlink_publisher.content import scraper as work_scraper
from backlink_publisher._util.errors import ExternalServiceError, InputValidationError
from backlink_publisher.content.scraper import (
    WorkMetadata,
    fetch_work_metadata,
    fetch_work_urls_from_list,
)


# ── Autouse: silence sleep + default-public DNS ──────────────────────────────


@pytest.fixture(autouse=True)
def _mock_sleep():
    """Mock time.sleep on the retry path (macOS / CI isolation)."""
    with patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _mock_resolve_public(request):
    """DNS defaults to a public IP. Tests can re-patch via the
    `no_autoresolve` marker or via `_mock_resolve_public.return_value=[...]`."""
    if "no_autoresolve" in request.keywords:
        yield None
        return
    with patch.object(
        scraper_http, "_resolve_addresses", return_value=["93.184.216.34"]
    ) as m:
        yield m


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_response(
    *,
    status: int = 200,
    body: bytes = b"",
    content_type: str | None = "text/html; charset=utf-8",
    content_length: int | None = None,
    apparent_encoding: str = "utf-8",
    iter_chunks: list[bytes] | None = None,
) -> Mock:
    """Build a Mock response with the surface area work_scraper relies on."""
    resp = Mock(spec=requests.Response)
    resp.status_code = status
    headers: dict[str, str] = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    resp.headers = headers
    resp.content = body
    resp.encoding = "utf-8"
    resp.apparent_encoding = apparent_encoding
    # iter_content must be a callable returning an iterable
    chunks = iter_chunks if iter_chunks is not None else ([body] if body else [])
    resp.iter_content = lambda chunk_size=8192, _chunks=chunks: iter(_chunks)
    resp.close = Mock()
    # Provide `text` for callers that prefer it (we use .content + apparent_encoding)
    try:
        resp.text = body.decode(apparent_encoding, errors="replace")
    except LookupError:
        resp.text = body.decode("utf-8", errors="replace")
    return resp


_HTML_FULL = (
    b"<html><head><title>Hot Anime Pick</title>"
    b"<meta name='description' content='A great work to watch tonight.'>"
    b"</head><body><h1>Tonight Recommendation</h1></body></html>"
)


# ═════════════════════════════════════════════════════════════════════════════
# fetch_work_metadata
# ═════════════════════════════════════════════════════════════════════════════


class TestFetchWorkMetadataHappyPath:
    def test_extracts_title_description_h1(self):
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=_HTML_FULL)
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert isinstance(meta, WorkMetadata)
        assert meta.title == "Hot Anime Pick"
        assert meta.description == "A great work to watch tonight."
        assert meta.h1 == "Tonight Recommendation"

    def test_title_only_returns_partial_metadata(self):
        html = b"<html><head><title>Solo Title</title></head><body></body></html>"
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=html)
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is not None
        assert meta.title == "Solo Title"
        assert meta.description is None
        assert meta.h1 is None

    def test_strips_whitespace_around_fields(self):
        html = (
            b"<html><head><title>   Padded   </title>"
            b"<meta name='description' content='  spaced out  '>"
            b"</head><body><h1>\n  wrapped\n  </h1></body></html>"
        )
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=html)
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta.title == "Padded"
        assert meta.description == "spaced out"
        assert meta.h1 == "wrapped"

    def test_cjk_apparent_encoding_decodes_correctly(self):
        # GBK-encoded title to verify apparent_encoding override path
        body = "<html><head><title>热门动漫推荐</title></head><body></body></html>".encode(
            "gbk"
        )
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(
                body=body,
                content_type="text/html",  # no charset → apparent_encoding kicks in
                apparent_encoding="gbk",
            )
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is not None
        assert meta.title == "热门动漫推荐"

    def test_length_truncation_caps_fields(self):
        long_title = "x" * 500
        long_desc = "y" * 1000
        long_h1 = "z" * 500
        html = (
            f"<html><head><title>{long_title}</title>"
            f"<meta name='description' content='{long_desc}'>"
            f"</head><body><h1>{long_h1}</h1></body></html>"
        ).encode("utf-8")
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=html)
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is not None
        assert len(meta.title) == 200
        assert len(meta.description) == 500
        assert len(meta.h1) == 200


class TestFetchWorkMetadataEmptySignals:
    def test_no_title_meta_or_h1_returns_none(self):
        html = b"<html><head></head><body><p>nothing useful</p></body></html>"
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=html)
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None


class TestFetchWorkMetadataRetryAndStatus:
    def test_5xx_does_not_retry_and_returns_none(self):
        resp_500 = _make_response(status=500, body=b"oops")
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = resp_500
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None
        assert mock_get.call_count == 1  # no retries on 5xx

    def test_429_retries_until_success(self):
        resp_429 = _make_response(status=429, body=b"slow down")
        resp_200 = _make_response(body=_HTML_FULL)
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.side_effect = [resp_429, resp_429, resp_200]
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is not None
        assert meta.title == "Hot Anime Pick"
        assert mock_get.call_count == 3

    def test_connection_error_retries_then_fail_continue_returns_none(self):
        err = requests.exceptions.ConnectionError("boom")
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.side_effect = [err, err, err]
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None
        assert mock_get.call_count == 3  # exhausted retries

    def test_429_persists_then_returns_none(self):
        resp_429 = _make_response(status=429, body=b"")
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.side_effect = [resp_429, resp_429, resp_429]
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None


class TestFetchWorkMetadataSecuritySSRF:
    """SSRF: any private/loopback/link-local IP MUST block the HTTP call."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",       # loopback
            "10.0.0.5",        # private
            "192.168.1.1",     # private
            "172.16.0.1",      # private
            "169.254.169.254", # AWS metadata
            "::1",             # IPv6 loopback
            "fd00::1",         # IPv6 ULA
        ],
    )
    def test_private_or_loopback_ip_raises_input_validation(self, ip):
        with patch.object(scraper_http, "_resolve_addresses", return_value=[ip]):
            with patch.object(scraper_http.requests, "get") as mock_get:
                with pytest.raises(InputValidationError, match="disallowed"):
                    fetch_work_metadata("https://internal.example.com/work/1")
                mock_get.assert_not_called()

    def test_any_private_address_in_resolved_set_blocks(self):
        with patch.object(
            scraper_http,
            "_resolve_addresses",
            return_value=["93.184.216.34", "10.0.0.5"],  # mixed
        ):
            with patch.object(scraper_http.requests, "get") as mock_get:
                with pytest.raises(InputValidationError):
                    fetch_work_metadata("https://target.example.com/work/1")
                mock_get.assert_not_called()


class TestFetchWorkMetadataSecuritySize:
    """Body size guards: header pre-check AND streamed total."""

    def test_content_length_header_oversize_aborts_early(self):
        oversize = scraper_http._MAX_RESPONSE_BYTES + 1
        resp = _make_response(content_length=oversize, body=b"")
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = resp
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None  # fail-continue on oversize
        # Body never read
        resp.close.assert_called()

    def test_streamed_body_exceeding_limit_aborts(self):
        # No Content-Length header but chunks add up past the cap
        chunk = b"x" * 512_000
        chunks = [chunk] * 6  # ~3MB > 2MB cap
        resp = _make_response(
            content_length=None, body=b"", iter_chunks=chunks
        )
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = resp
            meta = fetch_work_metadata("https://target.example.com/work/1")
        assert meta is None
        resp.close.assert_called()


class TestFetchWorkMetadataInsecureTLS:
    def test_default_verify_true(self):
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=_HTML_FULL)
            fetch_work_metadata("https://target.example.com/work/1")
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("verify") is True

    def test_insecure_tls_opt_in_sets_verify_false(self):
        with patch.object(scraper_http.requests, "get") as mock_get:
            mock_get.return_value = _make_response(body=_HTML_FULL)
            fetch_work_metadata(
                "https://target.example.com/work/1", insecure_tls=True
            )
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("verify") is False


class TestFetchWorkMetadataBadInputs:
    def test_non_https_url_raises_input_validation(self):
        with patch.object(scraper_http.requests, "get") as mock_get:
            with pytest.raises(InputValidationError):
                fetch_work_metadata("http://target.example.com/work/1")
            mock_get.assert_not_called()

    def test_empty_url_raises(self):
        with pytest.raises(InputValidationError):
            fetch_work_metadata("")


# ═════════════════════════════════════════════════════════════════════════════
# fetch_work_urls_from_list
# ═════════════════════════════════════════════════════════════════════════════


_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _sitemap_urlset(urls: list[str]) -> bytes:
    body = (
        f"<?xml version='1.0' encoding='UTF-8'?>"
        f"<urlset xmlns='{_NS}'>"
        + "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        + "</urlset>"
    )
    return body.encode("utf-8")


def _sitemap_index(sub_sitemaps: list[str]) -> bytes:
    body = (
        f"<?xml version='1.0' encoding='UTF-8'?>"
        f"<sitemapindex xmlns='{_NS}'>"
        + "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in sub_sitemaps)
        + "</sitemapindex>"
    )
    return body.encode("utf-8")


def _xml_resp(body: bytes) -> Mock:
    return _make_response(body=body, content_type="application/xml")


def _html_resp(body: bytes) -> Mock:
    return _make_response(body=body, content_type="text/html")


class TestFetchUrlsSitemap:
    def test_sitemap_xml_returns_same_host_urls(self):
        urls = [
            "https://target.example.com/work/1",
            "https://target.example.com/work/2",
            "https://target.example.com/work/3",
        ]
        sitemap_body = _sitemap_urlset(urls)

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml"):
                return _xml_resp(sitemap_body)
            return _make_response(status=404, body=b"")

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == urls

    def test_sitemap_filters_off_host_urls(self):
        urls = [
            "https://target.example.com/work/1",
            "https://other.example.com/work/x",  # off-host: dropped
            "https://target.example.com/work/2",
        ]
        sitemap_body = _sitemap_urlset(urls)

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml"):
                return _xml_resp(sitemap_body)
            return _make_response(status=404, body=b"")

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == [
            "https://target.example.com/work/1",
            "https://target.example.com/work/2",
        ]

    def test_sitemap_index_recurses_one_level(self):
        sub_a = "https://target.example.com/sitemap-works-a.xml"
        sub_b = "https://target.example.com/sitemap-works-b.xml"
        index_body = _sitemap_index([sub_a, sub_b])
        urls_a = ["https://target.example.com/work/1", "https://target.example.com/work/2"]
        urls_b = ["https://target.example.com/work/3"]

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml"):
                return _xml_resp(index_body)
            if url == sub_a:
                return _xml_resp(_sitemap_urlset(urls_a))
            if url == sub_b:
                return _xml_resp(_sitemap_urlset(urls_b))
            return _make_response(status=404, body=b"")

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert sorted(result) == sorted(urls_a + urls_b)

    def test_sitemap_404_falls_through_to_html_fallback(self):
        html = (
            b"<html><body>"
            b"<a href='/work/100'>w1</a>"
            b"<a href='/work/200'>w2</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert "https://target.example.com/work/100" in result
        assert "https://target.example.com/work/200" in result

    def test_sitemap_max_candidates_truncates(self):
        many = [f"https://target.example.com/work/{i}" for i in range(80)]
        body = _sitemap_urlset(many)

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml"):
                return _xml_resp(body)
            return _make_response(status=404, body=b"")

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
                max_candidates=10,
            )
        assert len(result) == 10


class TestFetchUrlsHtmlFallback:
    def _patch_sitemap_404(self):
        """All sitemap variants return 404 — HTML fallback is forced."""
        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return None  # caller overrides
        return _side_effect

    def test_default_blocklist_excludes_nav_paths(self):
        html = (
            b"<html><body>"
            b"<a href='/tag/foo'>tag</a>"
            b"<a href='/category/bar'>cat</a>"
            b"<a href='/page/2'>p2</a>"
            b"<a href='/author/jane'>author</a>"
            b"<a href='/about'>about</a>"
            b"<a href='/contact'>contact</a>"
            b"<a href='/search?q=x'>search</a>"
            b"<a href='/feed'>feed</a>"
            b"<a href='/work/42'>keeper</a>"
            b"<a href='#top'>frag</a>"
            b"<a href='mailto:x@y.com'>mail</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == ["https://target.example.com/work/42"]

    def test_excludes_main_url_root_and_list_url_itself(self):
        html = (
            b"<html><body>"
            b"<a href='/'>home</a>"           # main_url root → excluded
            b"<a href='/list'>self</a>"        # list_url itself → excluded
            b"<a href='/work/1'>keep</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == ["https://target.example.com/work/1"]

    def test_deduplicates(self):
        html = (
            b"<html><body>"
            b"<a href='/work/1'>a</a>"
            b"<a href='/work/1'>b</a>"
            b"<a href='/work/1#anchor'>c</a>"  # fragment stripped → dup
            b"<a href='/work/2'>d</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        # /work/1#anchor is a hash-only link from same path; depending on
        # ordering we expect 2 unique URLs.
        assert sorted(result) == [
            "https://target.example.com/work/1",
            "https://target.example.com/work/2",
        ]

    def test_off_host_links_excluded_in_html_fallback(self):
        html = (
            b"<html><body>"
            b"<a href='https://target.example.com/work/1'>same</a>"
            b"<a href='https://other.example.com/work/x'>other</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == ["https://target.example.com/work/1"]

    def test_custom_blocklist_overrides_default(self):
        html = (
            b"<html><body>"
            b"<a href='/tag/foo'>tag</a>"        # default would exclude
            b"<a href='/work/1'>keep</a>"
            b"<a href='/banned/1'>banned</a>"     # custom blocks this
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
                list_path_blocklist=["/banned/"],  # override → /tag now allowed
            )
        assert "https://target.example.com/tag/foo" in result
        assert "https://target.example.com/work/1" in result
        assert "https://target.example.com/banned/1" not in result


class TestFetchUrlsFailureSemantics:
    def test_fail_empty_html_with_zero_candidates_returns_empty_list(self):
        """HTTP 200 but list page has 0 work-URL candidates → return [] + warn."""
        html = (
            b"<html><body>"
            b"<a href='/tag/foo'>tag</a>"   # all filtered out
            b"<a href='/about'>about</a>"
            b"</body></html>"
        )

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == []  # no raise

    def test_fail_empty_sitemap_with_zero_entries_returns_empty(self):
        body = _sitemap_urlset([])  # well-formed but empty
        html = b"<html><body></body></html>"  # fallback also empty

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml"):
                return _xml_resp(body)
            if url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _html_resp(html)

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            result = fetch_work_urls_from_list(
                "https://target.example.com/list",
                main_url="https://target.example.com/",
            )
        assert result == []

    def test_fail_abort_network_error_on_list_page(self):
        """All sitemap + list HTML calls fail with ConnectionError → ExternalServiceError."""

        err = requests.exceptions.ConnectionError("network down")
        with patch.object(scraper_http.requests, "get", side_effect=err):
            with pytest.raises(ExternalServiceError):
                fetch_work_urls_from_list(
                    "https://target.example.com/list",
                    main_url="https://target.example.com/",
                )

    def test_fail_abort_5xx_on_list_page(self):
        """5xx on the list_url fallback is treated as fail-abort (no useful body)."""

        def _side_effect(url, **_kw):
            if url.endswith("/sitemap.xml") or url.endswith("/sitemap_index.xml"):
                return _make_response(status=404, body=b"")
            return _make_response(status=503, body=b"")

        with patch.object(scraper_http.requests, "get", side_effect=_side_effect):
            with pytest.raises(ExternalServiceError):
                fetch_work_urls_from_list(
                    "https://target.example.com/list",
                    main_url="https://target.example.com/",
                )


class TestFetchUrlsSecurity:
    def test_private_ip_on_list_url_blocks_http(self):
        with patch.object(scraper_http, "_resolve_addresses", return_value=["10.0.0.5"]):
            with patch.object(scraper_http.requests, "get") as mock_get:
                with pytest.raises(InputValidationError):
                    fetch_work_urls_from_list(
                        "https://internal.example.com/list",
                        main_url="https://internal.example.com/",
                    )
                mock_get.assert_not_called()
