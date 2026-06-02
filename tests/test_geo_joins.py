"""Tests for the GEO article-URL set and brand-alias joins (Plan 2026-05-29-006 Unit 5).

Covers:
- build_published_article_set: returns frozenset of canonical article URLs from
  the injected bucket store; handles empty / malformed inputs gracefully.
- Article URL canonicalization round-trips: utm / trailing-slash / http→https.
- build_brand_aliases: v1 stub returns []; injectable config is respected (stub
  ignores it; the test just verifies no crash).
- known-cited vs known-uncited input → tier contract (integration with
  classify_verdict to verify the join feeds the gate correctly).
"""

from __future__ import annotations

import pytest

from backlink_publisher.geo.joins import build_brand_aliases, build_published_article_set
from backlink_publisher.geo.verdict import classify_verdict
from backlink_publisher.geo.engines import ProbeResult


# ---------------------------------------------------------------------------
# Helpers: minimal fake store
# ---------------------------------------------------------------------------

class _FakeLinkRecord:
    def __init__(self, live_url: str):
        self.live_url = live_url
        self.platform = None
        self.history_item_id = None
        self.verified_at = None
        self.verify_error = None


class _FakeBucket:
    def __init__(self, target_url: str, live_urls: list[str]):
        from backlink_publisher._util.url import canonicalize_url
        self.target_url = target_url
        self.links: dict[str, _FakeLinkRecord] = {
            canonicalize_url(u): _FakeLinkRecord(canonicalize_url(u))
            for u in live_urls
        }
        self.profile_entries = []
        self.has_anchor_data = False


def _fake_store_patch(monkeypatch, buckets: dict):
    """Patch build_target_buckets at the joins module level (module-level import)."""
    monkeypatch.setattr(
        "backlink_publisher.geo.joins.build_target_buckets",
        lambda store=None, history=None: buckets,
        raising=True,
    )


# ---------------------------------------------------------------------------
# build_published_article_set
# ---------------------------------------------------------------------------

class TestBuildPublishedArticleSet:
    def test_returns_frozenset(self, monkeypatch):
        _fake_store_patch(monkeypatch, {})
        result = build_published_article_set()
        assert isinstance(result, frozenset)

    def test_empty_store_returns_empty_frozenset(self, monkeypatch):
        _fake_store_patch(monkeypatch, {})
        result = build_published_article_set()
        assert result == frozenset()

    def test_single_article_url_included(self, monkeypatch):
        article_url = "https://hashnode.com/post/my-article"
        bucket = _FakeBucket("https://example.com", [article_url])
        _fake_store_patch(monkeypatch, {"https://example.com": bucket})
        result = build_published_article_set()
        assert article_url in result

    def test_multiple_article_urls_from_multiple_buckets(self, monkeypatch):
        urls1 = ["https://hashnode.com/post/one", "https://hashnode.com/post/two"]
        urls2 = ["https://dev.to/user/three"]
        buckets = {
            "https://target1.com": _FakeBucket("https://target1.com", urls1),
            "https://target2.com": _FakeBucket("https://target2.com", urls2),
        }
        _fake_store_patch(monkeypatch, buckets)
        result = build_published_article_set()
        for url in urls1 + urls2:
            assert url in result

    def test_urls_are_canonicalized(self, monkeypatch):
        """UTM params stripped; trailing slashes normalized."""
        raw_url = "https://hashnode.com/post/my-article/?utm_source=test"
        bucket = _FakeBucket("https://example.com", [raw_url])
        _fake_store_patch(monkeypatch, {"https://example.com": bucket})
        result = build_published_article_set()
        # Canonical form should be in the set (utm stripped, slash stripped).
        assert "https://hashnode.com/post/my-article" in result

    def test_blank_live_url_skipped(self, monkeypatch):
        """A bucket with blank live_url entries should produce no URLs."""
        bucket = _FakeBucket("https://example.com", [])
        # Manually insert a blank entry.
        bucket.links[""] = _FakeLinkRecord("")
        _fake_store_patch(monkeypatch, {"https://example.com": bucket})
        result = build_published_article_set()
        assert "" not in result

    def test_exception_from_store_returns_empty_frozenset(self, monkeypatch):
        """A broken store → empty set (warning logged), no crash."""
        def _bad_store(store=None, history=None):
            raise RuntimeError("database unavailable")
        monkeypatch.setattr(
            "backlink_publisher.geo.joins.build_target_buckets",
            _bad_store,
            raising=True,
        )
        result = build_published_article_set()
        assert result == frozenset()

    def test_injectable_history(self, monkeypatch):
        """The ``history`` kwarg is forwarded to build_target_buckets."""
        captured: dict = {}

        def _record_history(store=None, history=None):
            captured["history"] = history
            return {}

        monkeypatch.setattr(
            "backlink_publisher.geo.joins.build_target_buckets",
            _record_history,
            raising=True,
        )
        build_published_article_set(history=[{"platform": "velog"}])
        assert captured["history"] == [{"platform": "velog"}]


# ---------------------------------------------------------------------------
# Integration: join feeds the credit gate correctly
# ---------------------------------------------------------------------------

class TestJoinFeedsGate:
    def _probe(self, urls: list[str]) -> ProbeResult:
        return ProbeResult(
            answer_text="Some answer.",
            source_urls=urls,
            raw_response={},
            outcome="ok",
        )

    def test_known_cited_url_gives_article_cited(self, monkeypatch):
        article = "https://hashnode.com/post/my-article"
        bucket = _FakeBucket("https://example.com", [article])
        _fake_store_patch(monkeypatch, {"https://example.com": bucket})

        articles = build_published_article_set()
        v = classify_verdict(
            self._probe([article]),
            target_url="https://mysite.com",
            published_article_urls=articles,
            query="q",
            engine="perplexity",
        )
        assert v.tier == "article_cited"

    def test_known_uncited_url_gives_absent(self, monkeypatch):
        """A URL not in the article set and not matching the target → absent."""
        bucket = _FakeBucket("https://example.com", ["https://hashnode.com/post/real"])
        _fake_store_patch(monkeypatch, {"https://example.com": bucket})

        articles = build_published_article_set()
        v = classify_verdict(
            self._probe(["https://unknown-blog.net/post"]),
            target_url="https://mysite.com",
            published_article_urls=articles,
            query="q",
            engine="perplexity",
        )
        assert v.tier == "absent"
        assert "https://unknown-blog.net/post" in v.uncredited_urls


# ---------------------------------------------------------------------------
# build_brand_aliases (v1 stub)
# ---------------------------------------------------------------------------

class TestBuildBrandAliases:
    def test_returns_empty_list_for_any_target(self):
        result = build_brand_aliases("https://example.com")
        assert result == []

    def test_injectable_config_does_not_crash(self):
        result = build_brand_aliases("https://example.com", config={"brand": "Ace"})
        assert isinstance(result, list)

    def test_returns_list_not_none(self):
        result = build_brand_aliases("https://any-url.com")
        assert result is not None
        assert isinstance(result, list)
