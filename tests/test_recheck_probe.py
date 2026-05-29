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

from backlink_publisher.recheck import verdicts
from backlink_publisher.recheck.probe import probe_liveness, recheck_link

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
