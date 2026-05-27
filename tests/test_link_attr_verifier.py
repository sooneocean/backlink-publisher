"""Tests for the link-attribute verifier helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from backlink_publisher.publishing.adapters.link_attr_verifier import (
    required_link_urls,
    verify_link_attributes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_resp(text: str = "", status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.text = text
    return resp


def _html(*a_tags: str) -> str:
    body = "\n".join(a_tags)
    return f"<html><body>{body}</body></html>"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_all_anchors_have_blank_target():
    html = _html(
        '<a href="https://a.com" target="_blank" rel="noopener">link</a>',
        '<a href="https://b.com" target="_blank">link2</a>',
        '<a href="https://c.com" target="_blank">link3</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")

    assert result["verification"] == "ok"
    assert result["total_anchors"] == 3
    assert result["blank_anchors"] == 3
    assert result["blank_ratio"] == 1.0


def test_half_anchors_have_blank_target():
    html = _html(
        '<a href="https://a.com" target="_blank">link</a>',
        '<a href="https://b.com" target="_blank">link2</a>',
        '<a href="https://c.com">link3</a>',
        '<a href="https://d.com">link4</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")

    assert result["verification"] == "ok"
    assert result["blank_ratio"] == 0.5


def test_no_anchors_in_html():
    with patch("backlink_publisher.http.get", return_value=_mock_resp("<html><body>no links</body></html>")):
        result = verify_link_attributes("https://example.com")

    assert result["verification"] == "ok"
    assert result["total_anchors"] == 0
    assert result["blank_ratio"] == 0.0


def test_single_quote_target_matches():
    html = _html("<a href='https://a.com' target='_blank'>link</a>")
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["blank_anchors"] == 1


def test_uppercase_target_matches():
    html = _html('<a href="https://a.com" TARGET="_BLANK">link</a>')
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["blank_anchors"] == 1


# ---------------------------------------------------------------------------
# Error / skip scenarios
# ---------------------------------------------------------------------------

def test_connection_error_returns_skipped():
    import requests as req_lib
    with patch("backlink_publisher.http.get", side_effect=req_lib.ConnectionError("refused")):
        result = verify_link_attributes("http://127.0.0.1:19999/nonexistent", timeout=0.1)
    assert result["verification"] == "skipped"
    assert "reason" in result


def test_http_5xx_returns_skipped():
    with patch("backlink_publisher.http.get", return_value=_mock_resp("", status_code=503)):
        result = verify_link_attributes("https://example.com")
    assert result["verification"] == "skipped"
    assert "503" in result["reason"]


def test_http_4xx_returns_skipped():
    with patch("backlink_publisher.http.get", return_value=_mock_resp("", status_code=404)):
        result = verify_link_attributes("https://example.com")
    assert result["verification"] == "skipped"


def test_timeout_returns_skipped():
    import requests as req_lib
    with patch("backlink_publisher.http.get", side_effect=req_lib.Timeout("timed out")):
        result = verify_link_attributes("https://example.com", timeout=0.001)
    assert result["verification"] == "skipped"


def test_non_html_response_does_not_crash():
    with patch("backlink_publisher.http.get", return_value=_mock_resp('{"not": "html"}')):
        result = verify_link_attributes("https://example.com")
    assert result["verification"] == "ok"
    assert result["total_anchors"] == 0


# ---------------------------------------------------------------------------
# nofollow detection — Plan 2026-05-13-004 Unit 6
# Critical: backlinks must be dofollow. Medium and similar platforms can
# silently inject rel="nofollow" — when that happens, the article's
# weight-passing value collapses to zero. The verifier surfaces this so
# operators see the trend in publish reports.
# ---------------------------------------------------------------------------


def test_nofollow_clean_html_flags_nothing():
    """Happy path — only noopener present, no nofollow detected."""
    html = _html(
        '<a href="https://a.com" target="_blank" rel="noopener">link</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["verification"] == "ok"
    assert result["nofollow_detected"] is False
    assert result["nofollow_anchors"] == 0


def test_nofollow_injected_by_platform_is_detected():
    """Medium-style injection: rel="nofollow noopener" → flagged."""
    html = _html(
        '<a href="https://a.com" target="_blank" rel="nofollow noopener">link</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["verification"] == "ok"
    assert result["nofollow_detected"] is True
    assert result["nofollow_anchors"] == 1
    assert "nofollow" in result["nofollow_reason"].lower()


def test_sponsored_rel_is_not_misclassified_as_nofollow():
    """rel="sponsored noopener" must NOT trigger nofollow detection."""
    html = _html(
        '<a href="https://a.com" target="_blank" rel="sponsored noopener">link</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["nofollow_detected"] is False
    assert result["nofollow_anchors"] == 0


def test_any_single_nofollow_anchor_flips_detection():
    """If even one of many anchors has nofollow, the whole publish is flagged."""
    html = _html(
        '<a href="https://a.com" target="_blank" rel="noopener">link a</a>',
        '<a href="https://b.com" target="_blank" rel="noopener">link b</a>',
        '<a href="https://c.com" target="_blank" rel="nofollow noopener">link c</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["nofollow_detected"] is True
    assert result["nofollow_anchors"] == 1
    assert result["total_anchors"] == 3


def test_nofollow_substring_match_does_not_falsely_trigger():
    """rel="nofollows" / rel="not-nofollow" / rel="ugc" — no true match."""
    html = _html(
        # bogus token; not the real "nofollow"
        '<a href="https://a.com" rel="ugc">link a</a>',
        # rel value contains "follow" but not "nofollow"
        '<a href="https://b.com" rel="follow">link b</a>',
        # word boundary — a token with "nofollow" as a prefix-only match
        # is NOT the real keyword. Most parsers wouldn't see this; we still
        # use word-boundary so a stray "nofollowed" token doesn't trip the
        # alert.
        '<a href="https://c.com" rel="nofollowed">link c</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["nofollow_detected"] is False
    assert result["nofollow_anchors"] == 0


def test_uppercase_rel_attribute_still_matches():
    """REL="NOFOLLOW" should be detected (HTML is case-insensitive)."""
    html = _html(
        '<a href="https://a.com" REL="NOFOLLOW NOOPENER">link</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["nofollow_detected"] is True
    assert result["nofollow_anchors"] == 1


def test_multiple_nofollow_anchors_count_correctly():
    html = _html(
        '<a href="https://a.com" rel="nofollow">a</a>',
        '<a href="https://b.com" rel="nofollow noopener">b</a>',
        '<a href="https://c.com" rel="noopener">c</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://example.com")
    assert result["nofollow_detected"] is True
    assert result["nofollow_anchors"] == 2


def test_skipped_result_has_no_nofollow_keys():
    """When verification is skipped (network error), nofollow keys must NOT
    be present — callers reading meta['nofollow_detected'] must check the
    verification status first."""
    import requests as req_lib
    with patch("backlink_publisher.http.get", side_effect=req_lib.ConnectionError("refused")):
        result = verify_link_attributes("http://127.0.0.1:1/x", timeout=0.1)
    assert result["verification"] == "skipped"
    assert "nofollow_detected" not in result


# ---------------------------------------------------------------------------
# medium_api integration: hook fires on publish mode only
# ---------------------------------------------------------------------------

def _make_payload(mode: str = "publish", article_id: str = "test01") -> dict:
    return {
        "id": article_id,
        "platform": "medium",
        "title": "Test",
        "slug": "test",
        "content_markdown": "# Test\n\nHello.",
        "tags": ["test"],
        "publish_mode": mode,
        "language": "en",
        "source_language": "en",
        "target_url": "https://x.com/",
        "main_domain": "https://x.com/",
        "url_mode": "A",
        "excerpt": "Hello.",
        "links": [],
        "seo": {"title": "Test", "description": "Test", "canonical_url": "https://x.com/"},
    }


def test_medium_api_publish_hook_wires_meta():
    """publish mode → verifier result stored in AdapterResult._provider_meta."""
    from backlink_publisher.publishing.adapters.medium_api import MediumAPIAdapter
    from backlink_publisher.config import Config

    html = _html(
        '<a href="https://x.com" target="_blank">link</a>',
        '<a href="https://y.com" target="_blank">link2</a>',
    )
    api_resp = MagicMock()
    api_resp.ok = True
    api_resp.status_code = 200
    api_resp.json.return_value = {"data": {"url": "https://medium.com/p/abc123", "id": "p1"}}
    me_resp = MagicMock()
    me_resp.ok = True
    me_resp.status_code = 200
    me_resp.json.return_value = {"data": {"id": "me123"}}
    page_resp = _mock_resp(html)

    def _requests_get(url, **kw):
        if "v1/me" in url:
            return me_resp
        return page_resp

    cfg = Config(medium_integration_token="dummy-token")
    adapter = MediumAPIAdapter()

    with patch("backlink_publisher.publishing.adapters.medium_api.http_get", side_effect=_requests_get), \
         patch("backlink_publisher.http.get", side_effect=_requests_get), \
         patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=api_resp):
        result = adapter.publish(_make_payload("publish"), mode="publish", config=cfg)

    assert result.status == "published"
    assert result._provider_meta is not None
    meta = result._provider_meta["link_attr_verification"]
    assert meta["verification"] == "ok"
    assert meta["blank_anchors"] == 2


def test_medium_api_draft_mode_skips_verifier():
    """draft mode → verify_link_attributes must NOT be called."""
    from backlink_publisher.publishing.adapters.medium_api import MediumAPIAdapter
    from backlink_publisher.config import Config

    api_resp = MagicMock()
    api_resp.ok = True
    api_resp.status_code = 200
    api_resp.json.return_value = {"data": {"url": "https://medium.com/p/draft/edit", "id": "d1"}}
    me_resp = MagicMock()
    me_resp.ok = True
    me_resp.status_code = 200
    me_resp.json.return_value = {"data": {"id": "me456"}}

    cfg = Config(medium_integration_token="dummy-token")
    adapter = MediumAPIAdapter()

    with patch("backlink_publisher.publishing.adapters.medium_api.http_get", return_value=me_resp), \
         patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=api_resp), \
         patch(
             "backlink_publisher.publishing.adapters.medium_api.verify_link_attributes"
         ) as mock_verify:
        result = adapter.publish(_make_payload("draft", "draft01"), mode="draft", config=cfg)

    assert result.status == "drafted"
    mock_verify.assert_not_called()


def test_verifier_skipped_result_no_warn(caplog):
    """When verifier returns skipped, no WARN about stripping should fire."""
    from backlink_publisher.publishing.adapters.medium_api import MediumAPIAdapter
    from backlink_publisher.config import Config

    api_resp = MagicMock()
    api_resp.ok = True
    api_resp.status_code = 200
    api_resp.json.return_value = {"data": {"url": "https://medium.com/p/abc", "id": "p2"}}
    me_resp = MagicMock()
    me_resp.ok = True
    me_resp.status_code = 200
    me_resp.json.return_value = {"data": {"id": "me789"}}

    skipped = {"verification": "skipped", "reason": "timeout"}

    cfg = Config(medium_integration_token="dummy-token")
    adapter = MediumAPIAdapter()

    with patch("backlink_publisher.publishing.adapters.medium_api.http_get", return_value=me_resp), \
         patch("backlink_publisher.publishing.adapters.medium_api.http_post", return_value=api_resp), \
         patch(
             "backlink_publisher.publishing.adapters.medium_api.verify_link_attributes",
             return_value=skipped,
         ):
        result = adapter.publish(_make_payload("publish", "p2"), mode="publish", config=cfg)

    assert result.status == "published"
    meta = result._provider_meta["link_attr_verification"]
    assert meta["verification"] == "skipped"
    assert "stripped" not in caplog.text


# ---------------------------------------------------------------------------
# Unit 1 — target-specific verdict: verify_link_attributes(target_urls=...)
# Plan 2026-05-27-006 Unit 1: isolate the operator's own required backlink(s)
# from page-wide nofollow noise (footer / nav / share links).
# ---------------------------------------------------------------------------

_TARGET = "https://myblog.example.com/post/abc"


def test_target_found_dofollow():
    """Happy path: required target present as a dofollow anchor."""
    html = _html(
        f'<a href="{_TARGET}" target="_blank" rel="noopener">myblog</a>',
        '<a href="https://other.com" rel="noopener">other</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["verification"] == "ok"
    assert result["target_found"] is True
    assert result["target_nofollow"] is False
    assert result["target_rewritten"] is False
    assert result["target_nofollow_urls"] == []
    assert result["target_missing_urls"] == []
    assert result["target_rewritten_urls"] == []


def test_target_nofollow_drift_detected():
    """Required target present but nofollow-injected → target_nofollow=True."""
    html = _html(
        f'<a href="{_TARGET}" rel="nofollow noopener">myblog</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["target_found"] is True
    assert result["target_nofollow"] is True
    assert _TARGET in result["target_nofollow_urls"]
    assert result["target_rewritten"] is False
    assert result["target_missing_urls"] == []


def test_target_missing_drift_detected():
    """Required target URL absent from page → target_found=False, appears in missing list."""
    html = _html(
        '<a href="https://unrelated.com" target="_blank">something</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["target_found"] is False
    assert _TARGET in result["target_missing_urls"]
    assert result["target_nofollow"] is False
    assert result["target_rewritten"] is False


def test_target_rewritten_via_interstitial():
    """Required target reachable only via redirect-shim → target_rewritten=True.

    The anchor href uses a redirect shim
    (e.g., ``https://shim.example.com/?url=<encoded-target>``).  Our unwrap
    logic resolves to the effective destination, and the *direct* href does not
    match, so the target is flagged as rewritten.
    """
    shim = f"https://shim.example.com/?url={_TARGET}"
    html = _html(
        f'<a href="{shim}" rel="noopener">myblog via shim</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["target_found"] is True
    assert result["target_rewritten"] is True
    assert _TARGET in result["target_rewritten_urls"]
    assert result["target_nofollow"] is False


def test_unrelated_page_nofollow_does_not_taint_target():
    """Page-wide nofollow (nav/footer) fires nofollow_detected, but if the
    operator's OWN target link is dofollow, target_nofollow stays False."""
    html = _html(
        f'<a href="{_TARGET}" rel="noopener">myblog</a>',
        '<a href="https://nav.example.com" rel="nofollow">nav</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["nofollow_detected"] is True      # page-wide fire
    assert result["target_nofollow"] is False       # our link is dofollow
    assert result["target_found"] is True


def test_target_dofollow_wins_over_same_nofollow_duplicate():
    """If the same target URL appears twice (once dofollow, once nofollow),
    a single surviving dofollow instance means target_nofollow=False."""
    html = _html(
        f'<a href="{_TARGET}" rel="nofollow">nofollow copy</a>',
        f'<a href="{_TARGET}" rel="noopener">dofollow copy</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[_TARGET])

    assert result["target_nofollow"] is False  # dofollow copy survives


def test_back_compat_no_target_fields_when_target_urls_none():
    """Back-compat: target_urls=None → no target_* keys in result."""
    html = _html('<a href="https://a.com" target="_blank">link</a>')
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com")  # no target_urls

    assert "target_found" not in result
    assert "target_nofollow" not in result
    assert "target_missing_urls" not in result


def test_back_compat_no_target_fields_when_target_urls_empty():
    """Back-compat: target_urls=[] → no target_* keys in result."""
    html = _html('<a href="https://a.com" target="_blank">link</a>')
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[])

    assert "target_found" not in result
    assert "target_nofollow" not in result


def test_skipped_result_has_no_target_fields():
    """verification=skipped → no target_* fields (target_urls present but unused)."""
    import requests as req_lib
    with patch("backlink_publisher.http.get", side_effect=req_lib.ConnectionError("refused")):
        result = verify_link_attributes("http://127.0.0.1:1/x", timeout=0.1,
                                        target_urls=[_TARGET])

    assert result["verification"] == "skipped"
    assert "target_found" not in result
    assert "target_nofollow" not in result


def test_multiple_required_links_all_dofollow():
    """All required links present and dofollow → target_found=True, no drift."""
    t1 = "https://myblog.example.com/post/one"
    t2 = "https://myblog.example.com/post/two"
    html = _html(
        f'<a href="{t1}" rel="noopener">one</a>',
        f'<a href="{t2}" rel="noopener">two</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com", target_urls=[t1, t2])

    assert result["target_found"] is True
    assert result["target_nofollow"] is False
    assert result["target_missing_urls"] == []


def test_multiple_required_links_one_missing_one_nofollow():
    """One required link missing, another nofollow: both failure modes surface."""
    present = "https://myblog.example.com/post/one"
    missing = "https://myblog.example.com/post/two"
    html = _html(
        f'<a href="{present}" rel="nofollow">one</a>',
        '<a href="https://unrelated.com">x</a>',
    )
    with patch("backlink_publisher.http.get", return_value=_mock_resp(html)):
        result = verify_link_attributes("https://pub.example.com",
                                        target_urls=[present, missing])

    assert result["target_found"] is False
    assert result["target_nofollow"] is True
    assert present in result["target_nofollow_urls"]
    assert missing in result["target_missing_urls"]


# ---------------------------------------------------------------------------
# Unit 1 — required_link_urls() extractor
# ---------------------------------------------------------------------------

def test_required_link_urls_extracts_required_only():
    payload = {
        "links": [
            {"url": "https://a.com", "required": True},
            {"url": "https://b.com", "required": False},
            {"url": "https://c.com"},               # no required key
            {"url": "https://d.com", "required": True},
        ]
    }
    result = required_link_urls(payload)
    assert result == ["https://a.com", "https://d.com"]


def test_required_link_urls_empty_links():
    assert required_link_urls({"links": []}) == []


def test_required_link_urls_no_links_key():
    assert required_link_urls({}) == []


def test_required_link_urls_links_is_none():
    assert required_link_urls({"links": None}) == []


def test_required_link_urls_skips_non_dict_entries():
    payload = {"links": ["not-a-dict", 42, {"url": "https://x.com", "required": True}]}
    result = required_link_urls(payload)
    assert result == ["https://x.com"]


def test_required_link_urls_url_coerced_to_str():
    """URL value should be coerced to str regardless of input type."""
    payload = {"links": [{"url": 12345, "required": True}]}
    result = required_link_urls(payload)
    assert result == ["12345"]
