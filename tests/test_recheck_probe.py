"""Unit 2: shared liveness primitive (probe_liveness) + recheck_link.

The probe maps an inspect_target_anchor result onto the 5-verdict taxonomy.
Tests inject a fake ``inspect_fn`` so they are deterministic and zero-network.
Positive present-assertions on every verdict (not just "no exception"), the
dofollow cross-check against the real manifest registry, anchor-drift
best-effort degradation, the never-raises contract, and that the WebUI recheck
service shares this one engine.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher.content._preflight_fetch import PreflightFacts
from backlink_publisher.recheck import indexability, verdicts
from backlink_publisher.recheck.probe import probe_liveness, recheck_link

INDEXABILITY_OK = indexability.OK
INDEXABILITY_BLOCKED = indexability.BLOCKED
INDEXABILITY_UNKNOWN = indexability.UNKNOWN

LIVE = "https://medium.com/p/abc"
TARGET = "https://my.site/"


def _inspect(**overrides):
    """Build a fake inspect_target_anchor returning a canned result dict."""
    base = {
        "page_readable": True,
        "marker_present": None,
        "target_anchor_found": True,
        "target_rel": None,
        "target_is_nofollow": False,
        "target_anchor_text": None,
        "reason": None,
    }
    base.update(overrides)
    return lambda url, target, **kw: dict(base)


def _facts_fn(**overrides):
    """Build a fake ``fetch_target`` returning canned PreflightFacts (clean 200)."""
    base = {
        "status": 200,
        "reason": None,
        "noindex": False,
        "head_complete": True,
        "x_robots_tag": None,
    }
    base.update(overrides)
    return lambda url, *, timeout=None: PreflightFacts(**base)


# ── liveness verdicts ────────────────────────────────────────────────────────

def test_alive():
    out = probe_liveness(LIVE, TARGET, inspect_fn=_inspect())
    assert out["verdict"] == verdicts.ALIVE


@pytest.mark.parametrize("reason", ["http_404", "http_410"])
def test_host_gone_on_deterministic_dead_status(reason):
    out = probe_liveness(LIVE, TARGET, inspect_fn=_inspect(page_readable=False, reason=reason))
    assert out["verdict"] == verdicts.HOST_GONE
    assert out["reason"] == reason


def test_link_stripped_when_page_alive_but_anchor_absent():
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(target_anchor_found=False),
    )
    assert out["verdict"] == verdicts.LINK_STRIPPED


def test_uncanonicalizable_target_is_probe_error_not_link_stripped():
    # A malformed/uncanonicalizable target_url yields target_anchor_found=False
    # with reason=target_uncanonicalizable — it must NOT be reported as a
    # deterministic dead link (which could wrongly trip --fail-on-dead).
    out = probe_liveness(
        LIVE, "::not-a-url::",
        inspect_fn=_inspect(target_anchor_found=False, reason="target_uncanonicalizable"),
    )
    assert out["verdict"] == verdicts.PROBE_ERROR
    assert not verdicts.is_deterministic_dead(out["verdict"])


def test_no_target_url_is_alive_not_link_stripped():
    # An empty target means we can only confirm page liveness — must NOT be
    # reported as link_stripped just because no anchor was searched for.
    out = probe_liveness(LIVE, "", inspect_fn=_inspect(target_anchor_found=False))
    assert out["verdict"] == verdicts.ALIVE


# ── probe_error: transient / anti-bot must NOT be a deterministic dead ───────

@pytest.mark.parametrize(
    "reason", ["http_403", "http_429", "http_500", "http_503", "network_error",
               "invalid_url", "ssrf_blocked", "empty_body"],
)
def test_probe_error_on_transient_or_antibot(reason):
    out = probe_liveness(LIVE, TARGET, inspect_fn=_inspect(page_readable=False, reason=reason))
    assert out["verdict"] == verdicts.PROBE_ERROR
    assert not verdicts.is_deterministic_dead(out["verdict"])
    # probe_error must not advance the age cursor (D3).
    assert not verdicts.advances_age_cursor(out["verdict"])


def test_never_raises_returns_probe_error():
    def _boom(url, target, **kw):
        raise RuntimeError("boom")

    out = probe_liveness(LIVE, TARGET, inspect_fn=_boom)
    assert out["verdict"] == verdicts.PROBE_ERROR
    assert "probe_exception" in out["reason"]


# ── dofollow drift cross-check against the real manifest registry ────────────

def test_dofollow_lost_when_channel_is_dofollow():
    # medium is a registered dofollow=True channel.
    out = probe_liveness(
        LIVE, TARGET, platform="medium",
        inspect_fn=_inspect(target_is_nofollow=True, target_rel="nofollow"),
    )
    assert out["verdict"] == verdicts.DOFOLLOW_LOST


@pytest.mark.parametrize("platform", ["devto", "wordpress", "hashnode"])
def test_no_dofollow_lost_when_channel_not_known_dofollow(platform):
    # devto=False, wordpress=None, hashnode="uncertain" — a nofollow on these is
    # expected/unverifiable, not drift (D6).
    out = probe_liveness(
        LIVE, TARGET, platform=platform,
        inspect_fn=_inspect(target_is_nofollow=True, target_rel="nofollow"),
    )
    assert out["verdict"] == verdicts.ALIVE
    assert out["expected_nofollow"] is True


# ── anchor-text drift (best-effort) ──────────────────────────────────────────

def test_anchor_baseline_missing_when_no_baseline():
    out = probe_liveness(LIVE, TARGET, inspect_fn=_inspect())
    assert out["anchor_baseline_missing"] is True
    assert out["anchor_drift"] is False


def test_anchor_drift_recorded_but_verdict_stays_alive():
    out = probe_liveness(
        LIVE, TARGET, baseline_anchor="Old Anchor",
        inspect_fn=_inspect(target_anchor_text="Brand New Anchor"),
    )
    assert out["verdict"] == verdicts.ALIVE  # R3: drift recorded, not a death
    assert out["anchor_drift"] is True
    assert out["reason"] == "anchor_text_changed"


def test_no_anchor_drift_when_text_matches_modulo_whitespace_case():
    out = probe_liveness(
        LIVE, TARGET, baseline_anchor="My  Anchor",
        inspect_fn=_inspect(target_anchor_text="my anchor"),
    )
    assert out["verdict"] == verdicts.ALIVE
    assert out["anchor_drift"] is False


# ── recheck_link: dry preview vs probe ───────────────────────────────────────

def test_dry_preview_is_zero_network():
    calls = []

    def _spy(url, target, **kw):
        calls.append(url)
        return _inspect()(url, target)

    rec = {"live_url": LIVE, "target_url": TARGET, "host": "medium.com",
           "article_id": 5, "platform": "medium", "published_age_days": 30}
    out = recheck_link(rec, probe=False, inspect_fn=_spy)
    assert out["will_probe"] is True
    assert out["live_url"] == LIVE
    assert calls == []  # zero network on dry preview


def test_probe_merges_verdict_and_identity():
    rec = {"live_url": LIVE, "target_url": TARGET, "host": "medium.com",
           "article_id": 5, "platform": "medium"}
    out = recheck_link(rec, probe=True, inspect_fn=_inspect())
    assert out["verdict"] == verdicts.ALIVE
    assert out["article_id"] == 5
    assert out["host"] == "medium.com"


# ── indexability axis (orthogonal metadata; verdict UNCHANGED) ───────────────

def test_indexability_blocked_meta_noindex_keeps_alive():
    # HTTP 200 + meta-robots noindex → blocked(meta_noindex); liveness still alive.
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(), fetch_fn=_facts_fn(noindex=True),
    )
    assert out["verdict"] == verdicts.ALIVE  # orthogonal — verdict UNCHANGED
    assert out["indexability"] == INDEXABILITY_BLOCKED
    assert out["indexability_reason"] == "meta_noindex"


def test_indexability_blocked_x_robots_header():
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(noindex=True, x_robots_tag="googlebot: noindex"),
    )
    assert out["verdict"] == verdicts.ALIVE
    assert out["indexability"] == INDEXABILITY_BLOCKED
    assert out["indexability_reason"] == "x_robots"


def test_indexability_ok_clean_head():
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(noindex=False, head_complete=True),
    )
    assert out["verdict"] == verdicts.ALIVE
    assert out["indexability"] == INDEXABILITY_OK
    assert out["indexability_reason"] is None


def test_indexability_ok_when_directive_present_but_not_noindex():
    # X-Robots-Tag: all / follow,index / none all resolve to facts.noindex=False
    # upstream (single-source); presence of a directive alone never blocks.
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(noindex=False, x_robots_tag="all"),
    )
    assert out["indexability"] == INDEXABILITY_OK


def test_indexability_unknown_when_head_truncated_never_ok():
    # Captured prefix lacks </head> (stray pre-head <h1>): meta could be below the
    # cut, so a clean-looking page must downgrade to unknown, NEVER false-ok.
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(noindex=False, head_complete=False),
    )
    assert out["indexability"] == INDEXABILITY_UNKNOWN
    assert out["indexability"] != INDEXABILITY_OK


def test_indexability_unknown_on_non_200_facts():
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(status=403, reason="http_403", noindex=False),
    )
    assert out["indexability"] == INDEXABILITY_UNKNOWN


def test_indexability_unknown_on_fetch_error_never_blocked():
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=_facts_fn(status=None, reason="network_error"),
    )
    assert out["indexability"] == INDEXABILITY_UNKNOWN
    assert out["indexability"] != INDEXABILITY_BLOCKED


def test_indexability_unknown_and_never_raises_when_fetch_raises():
    def _boom(url, *, timeout=None):
        raise RuntimeError("fetch boom")

    out = probe_liveness(LIVE, TARGET, inspect_fn=_inspect(), fetch_fn=_boom)
    assert out["verdict"] == verdicts.ALIVE  # liveness unaffected by index probe
    assert out["indexability"] == INDEXABILITY_UNKNOWN


def test_indexability_skipped_and_unknown_when_host_gone():
    # An unreadable (dead) page must not trigger a second fetch — and indexability
    # defaults to unknown for a page we never read.
    calls = []

    def _spy_fetch(url, *, timeout=None):
        calls.append(url)
        return PreflightFacts(noindex=True)  # would be "blocked" if ever called

    out = probe_liveness(
        LIVE, TARGET,
        inspect_fn=_inspect(page_readable=False, reason="http_404"),
        fetch_fn=_spy_fetch,
    )
    assert out["verdict"] == verdicts.HOST_GONE
    assert out["indexability"] == INDEXABILITY_UNKNOWN
    assert calls == []  # no second fetch on a confirmed-dead page


def test_indexability_computed_even_when_link_stripped():
    # A readable page whose anchor was stripped is still a successfully-read page;
    # indexability is computed (orthogonal to the stripped-link verdict).
    out = probe_liveness(
        LIVE, TARGET,
        inspect_fn=_inspect(target_anchor_found=False),
        fetch_fn=_facts_fn(noindex=True),
    )
    assert out["verdict"] == verdicts.LINK_STRIPPED
    assert out["indexability"] == INDEXABILITY_BLOCKED


def test_indexability_single_source_with_canary_facts():
    # Parity: probe_liveness reads the SAME PreflightFacts.noindex fact that
    # cli/canary_targets._classify consumes — a noindex body yields blocked here
    # exactly as canary would gate link-alive on `not noindex`.
    from backlink_publisher.content import _preflight_fetch as pf

    body = (
        b'<html><head><meta name="robots" content="noindex,nofollow">'
        b"<title>T</title></head><body><h1>H</h1></body></html>"
    )

    class _Resp:
        def getcode(self):
            return 200

        def geturl(self):
            return LIVE

        def info(self):
            import email.message

            return email.message.Message()

        def read(self, n):
            return body if n else b""

        def close(self):
            pass

    facts = pf._build_facts_from_response(_Resp(), LIVE)
    assert facts.noindex is True  # canary gates link-alive on `not noindex`
    out = probe_liveness(
        LIVE, TARGET, inspect_fn=_inspect(),
        fetch_fn=lambda url, *, timeout=None: facts,
    )
    assert out["indexability"] == INDEXABILITY_BLOCKED


def test_recheck_link_carries_indexability_to_emit():
    # The fields must survive recheck_link's {**base, **verdict} merge so the
    # single emit seam can persist them.
    rec = {"live_url": LIVE, "target_url": TARGET, "host": "medium.com",
           "article_id": 5, "platform": "medium"}
    out = recheck_link(rec, probe=True, inspect_fn=_inspect(),
                       fetch_fn=_facts_fn(noindex=True))
    assert out["indexability"] == INDEXABILITY_BLOCKED
    assert out["indexability_reason"] == "meta_noindex"


# ── engine unification: WebUI recheck shares this one engine ─────────────────

def test_webui_recheck_service_routes_through_probe_liveness():
    """recheck_one's default verify_fn must use the shared engine, so the WebUI
    and CLI can never disagree about the same URL (origin R1)."""
    from webui_app.services.recheck import recheck_one

    item = {"id": "x", "status": "published_unverified", "title": "t",
            "target_url": TARGET, "article_urls": [LIVE]}
    with patch(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        _inspect(page_readable=False, reason="http_404"),
    ):
        mutation = recheck_one(item)
    # host_gone -> ok=False -> downgraded -> failed, proving the service used
    # the inspect_target_anchor engine (not the old verify_published path).
    assert mutation["status"] == "failed"
    assert mutation["_outcome"] == "downgraded"
