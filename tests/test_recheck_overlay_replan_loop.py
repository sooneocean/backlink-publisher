"""Unit 4: end-to-end positive-assertion loop — the no-theater gate (R7/R8).

Proves the overlay actually CLOSES the recheck→re-plan loop rather than asserting
a shape. The signature check is the *characterization*: the identical fixture
WITHOUT the overlay emits zero replacement seeds (the current parallel-wired
no-op), and WITH the overlay it emits a replacement — so the difference is the
fix, not pre-existing behavior.

Pipes the real CLI verbs: ``recheck-overlay`` (reads the sandbox events.db) →
``plan-gap`` (real active-dofollow roster), on default flags (no --include-failed).
"""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher.cli import plan_gap as plan_gap_cli
from backlink_publisher.cli import recheck_overlay as overlay_cli
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.gap.engine import active_dofollow_platforms
from backlink_publisher.recheck import verdicts

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
TARGET = "https://money.site/landing"
LIVE_URL = "https://medium.com/@me/the-post"  # the page carrying our backlink
AD = active_dofollow_platforms()  # real registry roster (>=1 dofollow platform)
DEAD_PLATFORM = AD[0]


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _seed_recheck(verdict, *, platform=DEAD_PLATFORM, ts=NOW, aid=1, live_url=LIVE_URL):
    # Real recheck events carry live_url in the payload (emit_recheck); the overlay
    # keys on it, so a stdin recheck (aid=None) is still identified by its live_url.
    EventStore().append(
        LINK_RECHECKED,
        {"verdict": verdict, "platform": platform, "live_url": live_url},
        article_id=aid,
        target_url=TARGET,
        ts_utc=ts.isoformat(),
    )


def _ledger_line(liveness="live"):
    # The ledger still counts the (now-dead) link as live equity — publish-time
    # clock only, never reads recheck verdicts. desired=1 → deficit 0 here.
    return json.dumps({
        "target_url": TARGET,
        "live_dofollow": 1,
        "live_dofollow_platforms": [DEAD_PLATFORM],
        "liveness": liveness,
        "liveness_verified_at": "2026-06-01T00:00:00",
    }) + "\n"


def _run(main_fn, argv, stdin):
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr, sys.stdin = out, err, io.StringIO(stdin)
    code = 0
    try:
        main_fn(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
    return out.getvalue(), err.getvalue(), code


def _seeds(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _plan_gap(stdin, *, emit_stale=False):
    argv = ["--desired", "1", "--language", "en"]
    if emit_stale:
        argv.append("--emit-stale")
    return _run(plan_gap_cli.main, argv, stdin)


def _overlay(stdin, argv=None):
    return _run(overlay_cli.main, argv or [], stdin)


def test_characterization_no_overlay_emits_zero_seeds():
    # The current parallel-wired no-op: a confirmed-dead link still counts as live
    # equity, so the deficit reads 0 and plan-gap proposes no replacement.
    _seed_recheck(verdicts.HOST_GONE)  # events.db knows it's dead...
    out, err, code = _plan_gap(_ledger_line())  # ...but plan-gap never reads that.
    assert code == 0
    assert _seeds(out) == []


def test_positive_flip_overlay_produces_replacement_seed():
    _seed_recheck(verdicts.HOST_GONE)
    overlaid, _e, oc = _overlay(_ledger_line())
    assert oc == 0
    # The overlay discounted the dead link: live_dofollow 1 → 0.
    assert _seeds(overlaid)[0]["live_dofollow"] == 0
    # Re-plan now sees a real deficit and emits exactly one replacement seed.
    out, err, code = _plan_gap(overlaid)
    assert code == 0
    seeds = _seeds(out)
    assert len(seeds) == 1
    assert seeds[0]["target_url"] == TARGET


def test_recency_later_alive_restores_baseline_no_seed():
    # host_gone, then a NEWER alive for the same link → overlay applies no discount;
    # the ledger passes through unchanged and the deficit returns to baseline (0).
    _seed_recheck(verdicts.HOST_GONE, ts=NOW - timedelta(days=3))
    _seed_recheck(verdicts.ALIVE, ts=NOW - timedelta(days=1))
    overlaid, _e, _c = _overlay(_ledger_line())
    assert _seeds(overlaid)[0]["live_dofollow"] == 1  # restored
    out, _err, _code = _plan_gap(overlaid)
    assert _seeds(out) == []


def test_determinism_rerun_identical_output():
    _seed_recheck(verdicts.HOST_GONE)
    a, _e1, _c1 = _overlay(_ledger_line())
    b, _e2, _c2 = _overlay(_ledger_line())
    assert a == b  # deterministic given a fixed event set (R8)


def test_probe_error_only_no_spurious_seed():
    _seed_recheck(verdicts.PROBE_ERROR)
    overlaid, _e, _c = _overlay(_ledger_line())
    assert _seeds(overlaid)[0]["live_dofollow"] == 1  # indeterminate → no discount
    out, _err, _code = _plan_gap(overlaid)
    assert _seeds(out) == []


def test_aged_stale_target_needs_emit_stale_to_close_loop():
    # The realistic recheck population: an aged target whose ledger liveness is
    # `stale`/`unverified` (publish-time clock, never re-verified). Discounting its
    # last live link to 0 makes plan-gap SUPPRESS it by default — so the documented
    # recipe pipes `plan-gap --emit-stale`. This test proves both halves; a
    # fresh-publish-only fixture (liveness=live) would silently mask the bug.
    _seed_recheck(verdicts.HOST_GONE)
    overlaid, _e, oc = _overlay(_ledger_line(liveness="stale"))
    assert oc == 0
    assert _seeds(overlaid)[0]["live_dofollow"] == 0  # discounted regardless

    # Default plan-gap suppresses the stale, zero-coverage row → zero seeds.
    out_default, _e1, _c1 = _plan_gap(overlaid)
    assert _seeds(out_default) == []

    # With --emit-stale (the documented recipe), the loop closes: one replacement.
    out_stale, _e2, _c2 = _plan_gap(overlaid, emit_stale=True)
    seeds = _seeds(out_stale)
    assert len(seeds) == 1
    assert seeds[0]["target_url"] == TARGET


def test_stdin_null_article_id_still_discounts_and_seeds():
    # The headless regression (A1): a stdin-sourced recheck carries a real live_url
    # but NULL article_id. Keying on article_id (or filtering it NOT NULL) would drop
    # it — re-opening the exact false-success the overlay exists to close. The overlay
    # keys on canonical live_url, so the dead link is still discounted end-to-end.
    _seed_recheck(verdicts.HOST_GONE, aid=None, live_url=LIVE_URL)
    overlaid, _e, oc = _overlay(_ledger_line())
    assert oc == 0
    assert _seeds(overlaid)[0]["live_dofollow"] == 0  # discounted despite NULL aid
    out, _err, code = _plan_gap(overlaid)
    assert code == 0
    seeds = _seeds(out)
    assert len(seeds) == 1
    assert seeds[0]["target_url"] == TARGET
