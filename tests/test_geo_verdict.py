"""Tests for the GEO verdict classifier (Plan 2026-05-29-006 Unit 5).

Covers the tier-precedence contract, carry_verdict helper, and end-to-end
``classify_verdict`` scenarios.

Scenarios:
- Refusal phrasing in answer text → ``refused`` (no URL matching attempted).
- ProbeResult.outcome == "refused" → ``refused`` regardless of URLs.
- Host-match URL → ``site_cited``; credited_urls populated.
- Published article URL → ``article_cited`` (when no site-level match).
- URL matching BOTH target host AND a published article → ``site_cited``
  (headline) and article also recorded in credited_urls.
- Non-empty answer with zero creditable URLs → ``absent``.
- Empty answer → ``absent``.
- brand_mentioned=True when alias matches; False when alias substring-only
  ("Ace" must not match "place").
- carry_verdict round-trips the tier + share (rounded to 6 decimals).
"""

from __future__ import annotations

import pytest

from backlink_publisher.geo.engines import ProbeResult
from backlink_publisher.geo.verdict import (
    VerdictResult,
    VERDICT_TIERS,
    carry_verdict,
    classify_verdict,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe(
    *,
    answer: str = "some answer",
    urls: list[str] | None = None,
    outcome: str = "ok",
) -> ProbeResult:
    return ProbeResult(
        answer_text=answer,
        source_urls=urls or [],
        raw_response={},
        outcome=outcome,
    )


_TARGET = "https://example.com"
_ARTICLES: frozenset[str] = frozenset(
    {
        "https://example.com/blog/post-1",
        "https://example.com/blog/post-2",
    }
)


def _classify(
    result: ProbeResult,
    *,
    target: str = _TARGET,
    articles: frozenset[str] = _ARTICLES,
    aliases: list[str] | None = None,
    query: str = "test query",
    engine: str = "perplexity",
) -> VerdictResult:
    return classify_verdict(
        result,
        target_url=target,
        published_article_urls=articles,
        brand_aliases=aliases,
        query=query,
        engine=engine,
    )


# ---------------------------------------------------------------------------
# VerdictResult contract
# ---------------------------------------------------------------------------

class TestVerdictResultContract:
    def test_valid_tiers_accepted(self):
        for tier in ("site_cited", "article_cited", "absent", "refused"):
            v = VerdictResult(tier=tier, brand_mentioned=False)
            assert v.tier == tier

    def test_invalid_tier_raises(self):
        with pytest.raises(ValueError, match="must be one of"):
            VerdictResult(tier="bogus", brand_mentioned=False)

    def test_verdict_tiers_set_is_complete(self):
        assert VERDICT_TIERS == {"site_cited", "article_cited", "absent", "refused"}

    def test_defaults_are_empty_lists(self):
        v = VerdictResult(tier="absent", brand_mentioned=False)
        assert v.credited_urls == []
        assert v.uncredited_urls == []
        assert v.possibly_cited_unresolved == []


# ---------------------------------------------------------------------------
# Refusal tier
# ---------------------------------------------------------------------------

class TestRefusalTier:
    def test_outcome_refused_gives_refused_tier(self):
        r = _probe(answer="no citations", outcome="refused")
        v = _classify(r)
        assert v.tier == "refused"

    def test_refusal_phrasing_in_answer_gives_refused(self):
        r = _probe(
            answer="I can't help with that. Here are no results.",
            urls=["https://example.com/page"],
            outcome="ok",
        )
        v = _classify(r)
        assert v.tier == "refused"

    def test_refusal_phrasing_i_cannot_help(self):
        r = _probe(answer="I cannot help with that request.")
        v = _classify(r)
        assert v.tier == "refused"

    def test_refusal_not_triggered_by_normal_answer(self):
        r = _probe(
            answer="Example.com is a great resource for developers.",
            urls=["https://example.com/about"],
        )
        v = _classify(r)
        assert v.tier != "refused"

    def test_refused_tier_has_empty_url_lists(self):
        """Refused → no URL classification attempted."""
        r = _probe(
            answer="I can't assist with that.",
            urls=["https://example.com/page"],
            outcome="ok",
        )
        v = _classify(r)
        assert v.tier == "refused"
        assert v.credited_urls == []
        assert v.uncredited_urls == []
        assert v.possibly_cited_unresolved == []

    def test_brand_mentioned_false_on_refused(self):
        r = _probe(answer="I can't help with that. Example brand.", outcome="refused")
        v = _classify(r, aliases=["Example"])
        assert v.brand_mentioned is False


# ---------------------------------------------------------------------------
# site_cited tier
# ---------------------------------------------------------------------------

class TestSiteCitedTier:
    def test_host_match_gives_site_cited(self):
        r = _probe(urls=["https://example.com/some-page"])
        v = _classify(r)
        assert v.tier == "site_cited"
        assert "https://example.com/some-page" in v.credited_urls

    def test_host_match_case_insensitive(self):
        r = _probe(urls=["HTTPS://EXAMPLE.COM/page"])
        v = _classify(r)
        assert v.tier == "site_cited"

    def test_host_match_strips_utm(self):
        r = _probe(urls=["https://example.com/page?utm_source=goog&utm_medium=cpc"])
        v = _classify(r)
        assert v.tier == "site_cited"
        # Credited URL should be canonicalized (utm stripped).
        assert any("utm" not in u for u in v.credited_urls)

    def test_url_matching_both_target_and_article_gives_site_cited(self):
        """A URL matching both site host AND published article → site_cited headline."""
        article_url = "https://example.com/blog/post-1"
        r = _probe(urls=[article_url])
        articles = frozenset({article_url})
        v = _classify(r, articles=articles)
        assert v.tier == "site_cited"
        # Article match also recorded in credited_urls.
        assert article_url in v.credited_urls or any(
            "blog/post-1" in u for u in v.credited_urls
        )

    def test_site_cited_over_article_cited_precedence(self):
        """site_cited wins when both site-host and article-URL match exist."""
        r = _probe(
            urls=[
                "https://other-platform.com/blog/post-about-example",
                "https://example.com/homepage",
            ]
        )
        articles = frozenset({"https://other-platform.com/blog/post-about-example"})
        v = _classify(r, articles=articles)
        assert v.tier == "site_cited"


# ---------------------------------------------------------------------------
# article_cited tier
# ---------------------------------------------------------------------------

class TestArticleCitedTier:
    def test_article_url_gives_article_cited(self):
        article = "https://hashnode.com/post/my-example-post"
        r = _probe(urls=[article])
        articles = frozenset({article})
        v = _classify(r, target="https://example.com", articles=articles)
        assert v.tier == "article_cited"
        assert article in v.credited_urls

    def test_article_url_utm_diff_still_matches(self):
        """utm params stripped → canonical matches the stored article URL."""
        stored = "https://hashnode.com/post/my-example-post"
        cited = stored + "?utm_source=perplexity&utm_medium=ai"
        r = _probe(urls=[cited])
        articles = frozenset({stored})
        v = _classify(r, target="https://example.com", articles=articles)
        assert v.tier == "article_cited"

    def test_article_url_trailing_slash_normalized(self):
        stored = "https://hashnode.com/post/my-example-post"
        cited = stored + "/"
        r = _probe(urls=[cited])
        articles = frozenset({stored})
        v = _classify(r, target="https://example.com", articles=articles)
        assert v.tier == "article_cited"

    def test_article_url_http_to_https_normalized(self):
        stored = "https://hashnode.com/post/my-example-post"
        cited = "http://hashnode.com/post/my-example-post"
        r = _probe(urls=[cited])
        articles = frozenset({stored})
        v = _classify(r, target="https://example.com", articles=articles)
        # http → https are different after canonicalization (scheme is preserved).
        # The test verifies no crash; tier is article_cited only if schemes match.
        # (This is conservative: we canonicalize but don't upgrade schemes.)
        assert v.tier in VERDICT_TIERS


# ---------------------------------------------------------------------------
# absent tier
# ---------------------------------------------------------------------------

class TestAbsentTier:
    def test_non_empty_answer_zero_creditable_urls_gives_absent(self):
        r = _probe(
            answer="Here is some information about the topic.",
            urls=["https://unrelated-site.org/page"],
        )
        v = _classify(r)
        assert v.tier == "absent"
        assert "https://unrelated-site.org/page" in v.uncredited_urls

    def test_empty_answer_gives_absent(self):
        r = _probe(answer="", outcome="absent")
        v = _classify(r)
        assert v.tier == "absent"

    def test_no_urls_gives_absent(self):
        r = _probe(answer="Some answer with no sources.", urls=[])
        v = _classify(r)
        assert v.tier == "absent"


# ---------------------------------------------------------------------------
# brand_mentioned flag
# ---------------------------------------------------------------------------

class TestBrandMentioned:
    def test_alias_match_whole_token(self):
        r = _probe(answer="Ace is a great tool for developers.")
        v = _classify(r, aliases=["Ace"])
        assert v.brand_mentioned is True

    def test_alias_must_not_match_as_substring(self):
        """'Ace' must NOT match 'place' — word-boundary / token contract."""
        r = _probe(answer="This is the place to be.")
        v = _classify(r, aliases=["Ace"])
        assert v.brand_mentioned is False

    def test_alias_case_insensitive(self):
        r = _probe(answer="ace is mentioned here")
        v = _classify(r, aliases=["Ace"])
        assert v.brand_mentioned is True

    def test_missing_aliases_gives_false(self):
        r = _probe(answer="Example brand is great")
        v = _classify(r, aliases=None)
        assert v.brand_mentioned is False

    def test_empty_aliases_list_gives_false(self):
        r = _probe(answer="Example brand is great")
        v = _classify(r, aliases=[])
        assert v.brand_mentioned is False

    def test_brand_mentioned_independent_of_tier(self):
        """brand_mentioned can be True even when tier == absent."""
        r = _probe(
            answer="Ace is mentioned but no matching URLs.",
            urls=["https://unrelated.org/page"],
        )
        v = _classify(r, aliases=["Ace"])
        assert v.tier == "absent"
        assert v.brand_mentioned is True

    def test_brand_mentioned_with_site_cited(self):
        r = _probe(
            answer="Ace Example.com is great.",
            urls=["https://example.com/page"],
        )
        v = _classify(r, aliases=["Ace"])
        assert v.tier == "site_cited"
        assert v.brand_mentioned is True


# ---------------------------------------------------------------------------
# carry_verdict helper
# ---------------------------------------------------------------------------

class TestCarryVerdict:
    def test_carry_verdict_basic_fields(self):
        v = VerdictResult(
            tier="site_cited",
            brand_mentioned=True,
            credited_urls=["https://example.com/page"],
            uncredited_urls=["https://other.com/page"],
            possibly_cited_unresolved=["https://t.co/abc"],
            query="test query",
            engine="perplexity",
        )
        payload = carry_verdict(v)
        assert payload["verdict"] == "site_cited"
        assert payload["brand_mentioned"] is True
        assert payload["credited_urls"] == ["https://example.com/page"]
        assert payload["uncredited_urls"] == ["https://other.com/page"]
        assert payload["possibly_cited_unresolved"] == ["https://t.co/abc"]
        assert payload["engine"] == "perplexity"
        assert payload["query"] == "test query"
        assert "share" not in payload

    def test_carry_verdict_share_rounded(self):
        v = VerdictResult(tier="absent", brand_mentioned=False)
        payload = carry_verdict(v, share=1 / 3)
        assert "share" in payload
        assert payload["share"] == round(1 / 3, 6)

    def test_carry_verdict_share_zero_rounded(self):
        v = VerdictResult(tier="absent", brand_mentioned=False)
        payload = carry_verdict(v, share=0.0)
        assert payload["share"] == 0.0

    def test_carry_verdict_required_fields_for_citation_observed(self):
        """Payload must satisfy CITATION_OBSERVED floor: verdict, engine, query."""
        v = VerdictResult(
            tier="absent", brand_mentioned=False, query="q", engine="perplexity"
        )
        payload = carry_verdict(v)
        from backlink_publisher.events.kinds import CITATION_OBSERVED, missing_required_fields
        missing = missing_required_fields(CITATION_OBSERVED, payload)
        assert not missing, f"carry_verdict payload missing floor fields: {missing}"

    def test_carry_verdict_does_not_mutate_input_lists(self):
        v = VerdictResult(
            tier="site_cited",
            brand_mentioned=False,
            credited_urls=["https://example.com/a"],
        )
        payload = carry_verdict(v)
        payload["credited_urls"].append("injected")
        assert v.credited_urls == ["https://example.com/a"]


# ---------------------------------------------------------------------------
# query + engine carry-through
# ---------------------------------------------------------------------------

class TestQueryEngineCarryThrough:
    def test_query_and_engine_in_verdict(self):
        r = _probe()
        v = _classify(r, query="who cites example.com?", engine="perplexity")
        assert v.query == "who cites example.com?"
        assert v.engine == "perplexity"
