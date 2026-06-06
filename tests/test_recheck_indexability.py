"""Unit tests for the pure indexability classifier (recheck.indexability).

``classify_indexability`` maps a PreflightFacts-like object onto (state, reason).
Pure, total, never raises — exercised here directly (no probe/fetch involved) so
the tri-state fail-open ladder is pinned independent of the liveness probe.
"""

from __future__ import annotations

from backlink_publisher.content._preflight_fetch import PreflightFacts
from backlink_publisher.recheck import indexability as ix


def _facts(**over):
    base = dict(status=200, reason=None, noindex=False, head_complete=True, x_robots_tag=None)
    base.update(over)
    return PreflightFacts(**base)


def test_clean_head_no_barrier_is_ok():
    assert ix.classify_indexability(_facts()) == (ix.OK, None)


def test_meta_noindex_is_blocked_meta_reason():
    assert ix.classify_indexability(_facts(noindex=True)) == (ix.BLOCKED, ix.REASON_META_NOINDEX)


def test_x_robots_noindex_is_blocked_xrobots_reason():
    # noindex True + the X-Robots-Tag header carries the directive → header wins.
    state, reason = ix.classify_indexability(_facts(noindex=True, x_robots_tag="googlebot: noindex"))
    assert (state, reason) == (ix.BLOCKED, ix.REASON_X_ROBOTS)


def test_noindex_without_xrobots_directive_falls_back_to_meta():
    # noindex True but the stored X-Robots header has no noindex token → meta fired.
    state, reason = ix.classify_indexability(_facts(noindex=True, x_robots_tag="max-image-preview:large"))
    assert (state, reason) == (ix.BLOCKED, ix.REASON_META_NOINDEX)


def test_truncated_head_downgrades_clean_page_to_unknown_never_ok():
    out = ix.classify_indexability(_facts(noindex=False, head_complete=False))
    assert out == (ix.UNKNOWN, None)
    assert out[0] != ix.OK


def test_non_200_is_unknown_never_blocked():
    assert ix.classify_indexability(_facts(status=403, reason="http_403")) == (ix.UNKNOWN, None)


def test_fetch_error_reason_is_unknown():
    assert ix.classify_indexability(_facts(status=None, reason="network_error")) == (ix.UNKNOWN, None)


def test_directive_present_but_not_noindex_is_ok():
    # facts.noindex=False already reflects upstream parsing (all/follow,index/none
    # resolve to noindex=False); a directive's mere presence never blocks.
    assert ix.classify_indexability(_facts(noindex=False, x_robots_tag="all"))[0] == ix.OK


def test_total_and_never_raises_on_duck_typed_partial_facts():
    class _Partial:  # only some attributes; getattr-based reads must not crash
        noindex = False

    assert ix.classify_indexability(_Partial()) == (ix.UNKNOWN, None)  # no status/head → unknown


def test_reason_vocab_is_closed():
    assert ix.REASON_VOCAB == {ix.REASON_META_NOINDEX, ix.REASON_X_ROBOTS}
    assert ix.STATES == {ix.OK, ix.BLOCKED, ix.UNKNOWN}
