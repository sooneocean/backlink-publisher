"""Tests for backlink_publisher.verify_publish module."""

from __future__ import annotations

from unittest.mock import patch


from backlink_publisher.linkcheck.verify import verify_published


# ── helpers ────────────────────────────────────────────────────────────────────

def _mock_get(status: int, body: str):
    """Patch _get_body to return a fixed status+body."""
    return patch(
        "backlink_publisher.linkcheck.verify._get_body",
        return_value=(status, body),
    )


def _good_body(title="Test Article", link_url="https://example.com"):
    return f"""<html><head><title>{title}</title></head>
<body><h1>{title}</h1><a href="{link_url}">Link</a></body></html>"""


# ── happy paths ────────────────────────────────────────────────────────────────

def test_verify_passes_when_all_conditions_met():
    body = _good_body("My Article", "https://example.com")
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=["https://example.com"],
        )
    assert result.ok is True
    assert result.reason == ""


def test_verify_passes_with_empty_title():
    body = _good_body()
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="",
            required_link_urls=["https://example.com"],
        )
    assert result.ok is True


def test_verify_passes_with_no_required_links():
    body = _good_body("My Article")
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=[],
        )
    assert result.ok is True


def test_verify_title_case_insensitive():
    body = "<html><body>MY ARTICLE is great. <a href='https://x.com'>x</a></body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="my article",
            required_link_urls=["https://x.com"],
        )
    assert result.ok is True


# ── failure paths ──────────────────────────────────────────────────────────────

def test_verify_fails_on_http_404():
    with _mock_get(404, "<html>Not Found</html>"):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="Title",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "HTTP 404" in result.reason


def test_verify_fails_when_title_missing():
    body = "<html><body>completely different content</body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="My Article",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "title not found" in result.reason


def test_verify_fails_when_required_link_missing():
    body = "<html><body>article text without the target link</body></html>"
    with _mock_get(200, body):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="",
            required_link_urls=["https://example.com/must-be-here"],
            max_wait=0,
        )
    assert result.ok is False
    assert "required links not found" in result.reason


def test_verify_fails_on_fetch_error():
    with _mock_get(0, "Connection refused"):
        result = verify_published(
            "https://blog.example.com/post/1",
            title="Title",
            required_link_urls=[],
            max_wait=0,
        )
    assert result.ok is False
    assert "fetch failed" in result.reason


def test_verify_fails_on_empty_url():
    result = verify_published("", title="Title", required_link_urls=[], max_wait=0)
    assert result.ok is False
    assert "no valid URL" in result.reason


def test_verify_fails_on_non_http_url():
    result = verify_published(
        "ftp://example.com/post",
        title="Title",
        required_link_urls=[],
        max_wait=0,
    )
    assert result.ok is False
    assert "no valid URL" in result.reason


# ── retry / polling behaviour ──────────────────────────────────────────────────

def test_verify_retries_until_success():
    """First attempt returns 404, second returns 200 with content."""
    good_body = _good_body("My Article", "https://example.com")
    call_count = 0

    def side_effect(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (404, "")
        return (200, good_body)

    with patch("backlink_publisher.linkcheck.verify._get_body", side_effect=side_effect):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            result = verify_published(
                "https://blog.example.com/post/1",
                title="My Article",
                required_link_urls=["https://example.com"],
                max_wait=30,
            )
    assert result.ok is True
    assert call_count == 2


def test_verify_gives_up_after_max_wait():
    """All attempts fail; verify returns False after max_wait."""
    with _mock_get(404, ""):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            with patch("backlink_publisher.linkcheck.verify.time.monotonic") as mock_mono:
                # Simulate: first call is before deadline, second is after
                mock_mono.side_effect = [0, 0, 100]  # start, check 1, check 2
                result = verify_published(
                    "https://blog.example.com/post/1",
                    title="Title",
                    required_link_urls=[],
                    max_wait=30,
                )
    assert result.ok is False
    assert "verification failed" in result.reason


# ── Plan 2026-05-21-005: non-ASCII URLs must not crash request-line encoding ──


class TestNonAsciiUrls:
    """Regression: velog returns ``https://velog.io/@<korean>/<cjk-slug>``.

    Before this fix, ``urlopen(Request(url))`` raised ``UnicodeEncodeError``
    at the ASCII request-line encoder, surfacing as
    ``fetch failed: 'ascii' codec can't encode characters in position 21-22``
    and demoting legitimately-published posts to ``published_unverified``.
    """

    def test_verify_published_succeeds_with_cjk_url(self):
        body = _good_body("제목", "https://example.com")
        captured: list[str] = []

        def fake_urlopen(req, **kw):
            captured.append(req.full_url)

            class _Resp:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, *a):
                    return False
                def getcode(self_inner):
                    return 200
                def read(self_inner):
                    return body.encode("utf-8")
            return _Resp()

        with patch("backlink_publisher.linkcheck.verify.urlopen", side_effect=fake_urlopen):
            result = verify_published(
                "https://velog.io/@한글유저/제목-슬러그",
                title="제목",
                required_link_urls=["https://example.com"],
            )

        assert result.ok is True, f"expected ok=True, got reason={result.reason!r}"
        assert len(captured) == 1
        captured[0].encode("ascii")  # would raise if non-ASCII slipped through
        assert "%" in captured[0]

    def test_verify_published_does_not_crash_when_url_carries_cjk(self):
        """Even on a real fetch failure, no UnicodeEncodeError surfaces."""
        def fake_urlopen(*a, **kw):
            raise ConnectionRefusedError("connection refused")

        with patch("backlink_publisher.linkcheck.verify.urlopen", side_effect=fake_urlopen):
            with patch("backlink_publisher.linkcheck.verify.time.sleep"):
                with patch("backlink_publisher.linkcheck.verify.time.monotonic") as mock_mono:
                    mock_mono.side_effect = [0, 0, 0, 100]
                    result = verify_published(
                        "https://velog.io/@한글/some-slug",
                        title="t",
                        required_link_urls=[],
                        max_wait=30,
                    )
        assert result.ok is False
        assert "'ascii' codec" not in result.reason
        assert "connection refused" in result.reason

    def test_ascii_url_passes_through_unchanged_to_request(self):
        captured: list[str] = []

        def fake_urlopen(req, **kw):
            captured.append(req.full_url)

            class _Resp:
                def __enter__(self_inner):
                    return self_inner
                def __exit__(self_inner, *a):
                    return False
                def getcode(self_inner):
                    return 200
                def read(self_inner):
                    return _good_body("T", "https://example.com").encode("utf-8")
            return _Resp()

        with patch("backlink_publisher.linkcheck.verify.urlopen", side_effect=fake_urlopen):
            verify_published(
                "https://blog.example.com/post/1",
                title="T",
                required_link_urls=["https://example.com"],
            )
        assert captured == ["https://blog.example.com/post/1"]


class TestInterstitialRewrittenLinks:
    """Regression: LiveJournal rewrites outbound ``<a href>`` through
    ``https://www.livejournal.com/away?to=<url-encoded-target>``.

    Observed live (2026-05-29): a published post with backlink
    ``https://51acgs.com/comic/117/`` rendered the anchor as
    ``away?to=https%3A%2F%2F51acgs.com%2Fcomic%2F117`` — URL-encoded AND missing
    the trailing slash. The old naive ``url in body`` substring scan
    false-negatived it, demoting a genuinely-live dofollow backlink to
    ``published_unverified`` and exiting 5 (``InternalError``). The gate now
    unwraps + canonicalizes, matching the dofollow-canary path.
    """

    def _lj_body(self, title="51漫畫", away_target="https%3A%2F%2F51acgs.com%2Fcomic%2F117"):
        return (
            f"<html><head><title>{title}: redredchen02 — LiveJournal</title></head>"
            f"<body><h1>{title}</h1>"
            f'<a href="https://www.livejournal.com/away?to={away_target}" '
            'rel="noopener noreferrer">link</a></body></html>'
        )

    def test_away_redirect_with_trailing_slash_mismatch_now_verifies(self):
        body = self._lj_body()
        with _mock_get(200, body):
            result = verify_published(
                "https://redredchen02.livejournal.com/574.html",
                title="51漫畫",
                # required URL HAS the trailing slash; the away-wrapped href does NOT.
                required_link_urls=["https://51acgs.com/comic/117/"],
            )
        assert result.ok is True, f"expected ok=True, got reason={result.reason!r}"

    def test_away_redirect_exact_match_verifies(self):
        body = self._lj_body(away_target="https%3A%2F%2F51acgs.com%2Fcomic%2F117%2F")
        with _mock_get(200, body):
            result = verify_published(
                "https://redredchen02.livejournal.com/574.html",
                title="51漫畫",
                required_link_urls=["https://51acgs.com/comic/117/"],
            )
        assert result.ok is True

    def test_genuinely_absent_link_still_fails_with_interstitials_present(self):
        # An away-wrapped link to a DIFFERENT target must not satisfy the gate.
        body = self._lj_body(away_target="https%3A%2F%2Fsomeone-else.com%2Fpost")
        with _mock_get(200, body):
            result = verify_published(
                "https://redredchen02.livejournal.com/574.html",
                title="51漫畫",
                required_link_urls=["https://51acgs.com/comic/117/"],
                max_wait=0,
            )
        assert result.ok is False
        assert "required links not found" in result.reason


def test_verify_reports_attempt_count_in_reason():
    """Failure reason mentions attempt count."""
    with _mock_get(404, ""):
        with patch("backlink_publisher.linkcheck.verify.time.sleep"):
            with patch("backlink_publisher.linkcheck.verify.time.monotonic") as mock_mono:
                mock_mono.side_effect = [0, 0, 0, 100]
                result = verify_published(
                    "https://x.com/p/1",
                    title="T",
                    required_link_urls=[],
                    max_wait=30,
                )
    assert "attempt" in result.reason
