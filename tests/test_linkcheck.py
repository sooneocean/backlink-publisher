"""Tests for linkcheck.check_url (Unit 4 of plan 2026-05-14-001).

Focused on the additive public wrapper. The existing
``_check_url_with_retry`` and ``check_urls_strict`` paths are not
re-tested here — they're exercised via test_validate_backlinks.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher import linkcheck


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock time.sleep at the module reference so retry delays don't slow tests."""
    monkeypatch.setattr("backlink_publisher.linkcheck.time", _FakeTime())


class _FakeTime:
    def sleep(self, _seconds: float) -> None:
        return None


def test_check_url_reachable_returns_true_none() -> None:
    with patch(
        "backlink_publisher.linkcheck._check_url_once",
        return_value=(True, None),
    ):
        ok, err = linkcheck.check_url("https://example.com")
    assert ok is True
    assert err is None


def test_check_url_unreachable_after_retries_returns_false_with_error() -> None:
    with patch(
        "backlink_publisher.linkcheck._check_url_once",
        return_value=(False, "HTTP 404"),
    ) as mocked:
        ok, err = linkcheck.check_url("https://example.com/dead")
    assert ok is False
    assert err == "HTTP 404"
    # 3 attempts total: initial + MAX_RETRIES=2 retries.
    assert mocked.call_count == 3


def test_check_url_succeeds_on_second_attempt() -> None:
    side_effects = iter([(False, "Timeout"), (True, None)])

    def fake_once(_url: str) -> tuple[bool, str | None]:
        return next(side_effects)

    with patch("backlink_publisher.linkcheck._check_url_once", side_effect=fake_once):
        ok, err = linkcheck.check_url("https://example.com/slow")
    assert ok is True
    assert err is None


# ── Plan 2026-05-21-005 ────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, code: int) -> None:
        self._code = code
    def getcode(self) -> int:
        return self._code


def test_check_url_once_normalizes_cjk_url_before_request() -> None:
    """HEAD branch: CJK URL is percent-encoded before urlopen sees it."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        ok, err = linkcheck._check_url_once("https://velog.io/@한글/슬러그")

    assert ok is True
    assert err is None
    assert len(captured) == 1
    captured[0].encode("ascii")  # would raise if non-ASCII slipped through
    assert "%" in captured[0]


def test_check_url_once_get_fallback_also_normalizes() -> None:
    """When HEAD fails, GET fallback must use the same normalized URL."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        if req.get_method() == "HEAD":
            raise OSError("simulated head failure")
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        ok, err = linkcheck._check_url_once("https://velog.io/@한글/슬러그")

    assert ok is True
    assert len(captured) == 2
    for u in captured:
        u.encode("ascii")  # both HEAD and GET URLs must be ASCII-clean


def test_check_url_once_ascii_url_passthrough() -> None:
    """ASCII URLs are not silently rewritten."""
    captured: list[str] = []

    def fake_urlopen(req, **kw):
        captured.append(req.full_url)
        return _FakeResp(200)

    with patch("backlink_publisher.linkcheck.http.urlopen", side_effect=fake_urlopen):
        linkcheck._check_url_once("https://example.com/api/v1?q=1")

    assert captured == ["https://example.com/api/v1?q=1"]


@pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1", "http://["])
def test_check_url_once_malformed_ipv6_returns_invalid_not_raises(bad) -> None:
    """A malformed-IPv6 URL must yield the (False, 'invalid URL') verdict, not a
    bare ValueError — _check_url_once parses before the scheme/netloc check, so
    the raise would otherwise escape its never-raises contract (Plan 006 R2)."""
    ok, err = linkcheck._check_url_once(bad)
    assert ok is False
    assert err is not None and "invalid URL" in err


# ── check_urls canonical-key dedup (mirrors content/fetch verify_urls_batch) ──


def test_check_urls_dedups_equivalent_canonical_urls() -> None:
    """utm/fragment variants collapse to ONE reachability check, but every
    original input URL still gets its own result entry."""
    from backlink_publisher.linkcheck.http import check_urls

    calls: list[str] = []

    def fake_once(url: str) -> tuple[bool, str | None]:
        calls.append(url)
        return (True, None)

    originals = [
        "https://a.example/p?utm_source=x",
        "https://a.example/p",
        "https://a.example/p#frag",
    ]
    with patch("backlink_publisher.linkcheck._check_url_once", side_effect=fake_once):
        results = check_urls(originals)

    assert set(results) == set(originals)
    assert all(results[u] == (True, None) for u in originals)
    assert len(calls) == 1, "equivalent URLs collapse to a single check"


def test_check_urls_fans_out_canonical_results_to_all_originals() -> None:
    """Two distinct canonicals (each with an equivalent variant) → two checks,
    with the per-canonical verdict fanned out to all four original URLs."""
    from backlink_publisher.linkcheck.http import check_urls

    calls: list[str] = []

    def fake_once(url: str) -> tuple[bool, str | None]:
        calls.append(url)
        return (True, None)

    originals = [
        "https://a.example/p?utm_source=x",
        "https://a.example/p",
        "https://b.example/q#frag",
        "https://b.example/q",
    ]
    with patch("backlink_publisher.linkcheck._check_url_once", side_effect=fake_once):
        results = check_urls(originals)

    assert set(results) == set(originals)
    assert all(v == (True, None) for v in results.values())
    assert len(calls) == 2, "two distinct canonicals → two checks"


@pytest.mark.parametrize("bad", ["http://[invalid", "http://[::1"])
def test_check_urls_malformed_url_does_not_crash_batch(bad: str) -> None:
    """A malformed URL whose urlsplit raises must not crash the batch — it falls
    back to its raw dedup key (via _dedup_key) and still yields a result."""
    from backlink_publisher.linkcheck.http import check_urls

    with patch("backlink_publisher.linkcheck._check_url_once", return_value=(True, None)):
        results = check_urls(["https://ok.example/", bad])

    assert results["https://ok.example/"] == (True, None)
    assert bad in results


def test_check_urls_non_str_element_does_not_crash_batch() -> None:
    """A non-str element (contract violation, but defensively handled) must not
    take down results for the valid URLs in the same batch."""
    from backlink_publisher.linkcheck.http import check_urls

    with patch("backlink_publisher.linkcheck._check_url_once", return_value=(True, None)):
        results = check_urls(["https://ok.example/", 123])  # type: ignore[list-item]

    assert results["https://ok.example/"] == (True, None)
    assert 123 in results
