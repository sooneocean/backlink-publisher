"""Unit 2 — G2 money-page decay baseline probe (plan 2026-06-01-005)."""

from __future__ import annotations

from backlink_publisher.content._preflight_fetch import PreflightFacts
from backlink_publisher.gates import g2_decay
from backlink_publisher.gates import verdict as gv


def _facts(**kw) -> PreflightFacts:
    base = dict(status=200)
    base.update(kw)
    return PreflightFacts(**base)


def _fetcher(mapping):
    """Return a fetch_fn resolving each URL to a canned PreflightFacts."""
    return lambda url: mapping[url]


# --- classify_page (pure, total) ---------------------------------------------
def test_classify_healthy():
    assert g2_decay.classify_page(_facts(status=200)) == ("healthy", "ok")


def test_classify_noindex_is_decayed():
    assert g2_decay.classify_page(_facts(noindex=True))[0] == "decayed"


def test_classify_soft404_and_http_4xx_are_decayed():
    assert g2_decay.classify_page(_facts(soft404=True)) == ("decayed", "soft404")
    assert g2_decay.classify_page(_facts(status=404)) == ("decayed", "http_404")


def test_classify_offhost_redirect_is_decayed():
    facts = _facts(status=200, redirected=True, host_diff=True)
    assert g2_decay.classify_page(facts) == ("decayed", "offhost_redirect")


def test_classify_no_status_is_unmeasurable():
    facts = _facts(status=None, reason="connect_timeout")
    assert g2_decay.classify_page(facts) == ("unmeasurable", "connect_timeout")


# --- assess_decay: rate + verdict --------------------------------------------
def test_decay_rate_is_decayed_over_measurable():
    urls = ["a", "b", "c", "d"]
    facts = {
        "a": _facts(noindex=True),     # decayed
        "b": _facts(status=404),       # decayed
        "c": _facts(status=200),       # healthy
        "d": _facts(status=200),       # healthy
    }
    v = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.4)
    assert v.rate == 0.5  # 2 decayed / 4 measurable
    assert v.state == gv.GO  # 0.5 >= 0.4 → premise confirmed, build justified


def test_low_decay_with_threshold_is_kill():
    urls = ["a", "b", "c", "d"]
    facts = {u: _facts(status=200) for u in urls}
    facts["a"] = _facts(noindex=True)  # 1/4 = 0.25
    v = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.4)
    assert v.rate == 0.25
    assert v.state == gv.KILL  # below threshold → don't build the machine


def test_unmeasurable_pages_excluded_from_rate_and_gate_thin_sample():
    # 3 of 4 pages unmeasurable → readable fraction 0.25 < 0.5 → INCONCLUSIVE,
    # never silently GO/KILL on the one readable page.
    urls = ["a", "b", "c", "d"]
    facts = {
        "a": _facts(status=None, reason="connect_timeout"),
        "b": _facts(status=None, reason="ssrf_blocked"),
        "c": _facts(status=None, reason="connect_timeout"),
        "d": _facts(noindex=True),
    }
    v = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.4)
    assert v.state == gv.INCONCLUSIVE


def test_first_run_without_threshold_is_calibration_inconclusive():
    # Even a sky-high decay rate is INCONCLUSIVE on the calibration run.
    urls = ["a", "b"]
    facts = {"a": _facts(status=404), "b": _facts(noindex=True)}
    v = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts))  # no threshold
    assert v.rate == 1.0
    assert v.state == gv.INCONCLUSIVE


def test_empty_urls_is_inconclusive_zero_sample():
    v = g2_decay.assess_decay([], decay_threshold=0.4)
    assert v.state == gv.INCONCLUSIVE
    assert v.sample_n == 0


def test_dedupes_urls():
    facts = {"a": _facts(status=404)}
    v = g2_decay.assess_decay(["a", "a", "a"], fetch_fn=_fetcher(facts), decay_threshold=0.5)
    assert v.sample_n == 1


def test_determinism_same_facts_same_verdict():
    urls = ["a", "b", "c"]
    facts = {"a": _facts(noindex=True), "b": _facts(status=200), "c": _facts(status=500)}
    v1 = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.5)
    v2 = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.5)
    assert v1 == v2


def test_evidence_is_host_stripped_aggregate_not_raw_urls():
    urls = ["https://example.test/secret-money-page"]
    facts = {urls[0]: _facts(noindex=True)}
    v = g2_decay.assess_decay(urls, fetch_fn=_fetcher(facts), decay_threshold=0.5)
    joined = " ".join(v.evidence)
    assert "secret-money-page" not in joined  # no raw operator URL leaks
    assert "noindex" in joined and "readable=1/1" in joined
