"""Unit 7a — enforce gate (Phase B). With BACKLINK_PUBLISHER_DEDUP_ENFORCE unset
behavior is unchanged (observe: record + dispatch all); with it = "1" the gate
decides done->skip, uncertain/live-attempting->hold, absent/failed/stale-
attempting->claim+dispatch. Gate lives on BOTH the fresh and resume seams.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U7).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.cli._dedup_gate import is_crashed_in_flight
from backlink_publisher.idempotency import DedupKey, DedupStore
from backlink_publisher.linkcheck.verify import VerificationResult

_TARGET = "https://example.com/article"
_ENFORCE = "BACKLINK_PUBLISHER_DEDUP_ENFORCE"


@pytest.fixture(autouse=True)
def _fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv(_ENFORCE, raising=False)


@pytest.fixture(autouse=True)
def _verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _payload(target=_TARGET, item_id="r1", platform="medium") -> dict:
    return {
        "id": item_id, "platform": platform, "language": "en",
        "publish_mode": "draft", "target_url": target,
        "main_domain": "https://example.com", "url_mode": "A",
        "title": "T", "slug": "t", "excerpt": "e", "tags": ["a", "b"],
        "content_markdown": f"About {target} and https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "E", "kind": "main_domain", "required": True},
            {"url": target, "anchor": "A", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "W", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "M", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "S", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "G", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "S", "description": "d", "canonical_url": target},
    }


def _drafted(platform="medium"):
    return AdapterResult(
        status="drafted", adapter="medium-api", platform=platform,
        draft_url="https://medium.com/p/new", published_url="",
    )


def _run(rows, argv, enforce=False, monkeypatch=None):
    if enforce:
        os.environ[_ENFORCE] = "1"
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO("\n".join(json.dumps(r) for r in rows))
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old
        os.environ.pop(_ENFORCE, None)


def _seed(state, *, platform="medium", target=_TARGET, live_url=None):
    store = DedupStore()
    key = DedupKey(platform=platform, target_url=target)
    store.intent_write(key)
    if state != "attempting":
        store.transition(key, state, live_url=live_url)
    return key


# --------------------------------------------------------------------------- #
# Observe (flag unset) — behavior unchanged
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_observe_done_key_still_dispatches(mock_pub, _mv):
    """Flag unset: even a done key dispatches (observe never skips)."""
    _seed("done", live_url="https://medium.com/p/old")
    mock_pub.return_value = _drafted()
    _out, _err, code = _run([_payload()], ["--platform", "medium"], enforce=False)
    assert code == 0
    mock_pub.assert_called_once()


# --------------------------------------------------------------------------- #
# Enforce — gate decisions
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_done_key_skipped(mock_pub, _mv):
    _seed("done", live_url="https://medium.com/p/old")
    stdout, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 0, stderr
    mock_pub.assert_not_called()
    out = json.loads(stdout.strip())
    assert out["status"] == "skipped_duplicate"
    assert out["published_url"] == "https://medium.com/p/old"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_uncertain_key_held(mock_pub, _mv):
    _seed("uncertain")
    _stdout, _stderr, _code = _run([_payload()], ["--platform", "medium"], enforce=True)
    mock_pub.assert_not_called()  # held, not dispatched


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_absent_key_dispatches_and_records_done(mock_pub, _mv):
    mock_pub.return_value = _drafted()
    _out, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 0, stderr
    mock_pub.assert_called_once()
    assert DedupStore().get(DedupKey(platform="medium", target_url=_TARGET)).state == "done"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_failed_key_redispatches(mock_pub, _mv):
    _seed("failed")
    mock_pub.return_value = _drafted()
    _out, _err, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 0
    mock_pub.assert_called_once()  # failed is re-publishable


# --------------------------------------------------------------------------- #
# Sequential re-run: clean completed run re-run under enforce -> zero new posts
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_clean_rerun_under_enforce_zero_new_posts(mock_pub, _mv):
    mock_pub.return_value = _drafted()
    # First run (observe) records done.
    _run([_payload()], ["--platform", "medium"], enforce=False)
    assert mock_pub.call_count == 1
    # Re-run the SAME plan under enforce -> the key is done -> skip, no new post.
    mock_pub.reset_mock()
    _out, _err, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert mock_pub.call_count == 0
    assert code == 0


# --------------------------------------------------------------------------- #
# RECON line: counts only, never campaign URLs
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_recon_line_counts_only_no_urls(mock_pub, _mv):
    _seed("done", live_url="https://medium.com/p/secret-campaign")
    _stdout, stderr, _code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert "dedup_reconciliation" in stderr
    assert "skipped_already_published" in stderr
    # The recorded live_url must NOT leak to the stderr RECON line.
    assert "secret-campaign" not in stderr


# --------------------------------------------------------------------------- #
# Fail-closed: a gate store error holds (never dispatches)
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_gate_store_error_fails_closed(mock_pub, _mv, mocker):
    mocker.patch(
        "backlink_publisher.cli._dedup_gate.DedupStore",
        side_effect=RuntimeError("dedup.db unreadable"),
    )
    mock_pub.return_value = _drafted()
    _out, _err, _code = _run([_payload()], ["--platform", "medium"], enforce=True)
    mock_pub.assert_not_called()  # fail-closed: held, never a possible double-post


# --------------------------------------------------------------------------- #
# Store-level: lease-takeover / staleness topology
# --------------------------------------------------------------------------- #
def test_gate_holds_on_live_attempting_and_holds_uncertain_on_stale():
    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    # Live owner (this PID, fresh) -> a second runner must HOLD (unchanged).
    store.intent_write(key, owner_pid=os.getpid())
    assert store.gate_and_claim(key, run_id="b").verdict == "hold"

    # Dead-PID attempting (run crashed mid-dispatch): record_intent writes
    # `attempting` BEFORE the post is created, so the post MAY already be live.
    # Re-dispatching could duplicate it, so the stale row is promoted to
    # `uncertain` and HELD for --adjudicate-uncertain (not reclaimed+dispatched).
    key2 = DedupKey(platform="velog", target_url="https://example.com/x")
    store.intent_write(key2, owner_pid=2_147_483_000)  # almost certainly dead
    decision = store.gate_and_claim(key2, run_id="b")
    assert decision.verdict == "hold"
    promoted = store.get(key2)
    assert promoted.state == "uncertain"
    assert promoted.owner_pid is None  # dead owner cleared on promotion (no stale PID)


def test_gate_holds_attempting_aged_past_ttl_as_uncertain():
    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    store.intent_write(key, owner_pid=os.getpid())
    # now far in the future -> aged past the TTL backstop -> stale -> hold
    # (uncertain), same may-have-committed safety as the dead-PID case.
    decision = store.gate_and_claim(key, run_id="b", now=time.time() + 10_000)
    assert decision.verdict == "hold"
    assert store.get(key).state == "uncertain"


def test_gate_force_overrides_stale_attempting_and_dispatches():
    # A manifest force-flag still overrides the stale-attempting hold: it
    # reclaims and dispatches, exactly like forcing a `uncertain` hold.
    store = DedupStore()
    key = DedupKey(platform="velog", target_url="https://example.com/forced")
    store.intent_write(key, owner_pid=2_147_483_000)  # dead -> stale
    decision = store.gate_and_claim(key, run_id="b", force=True)
    assert decision.verdict == "dispatch"
    assert store.get(key).state == "attempting"


# --------------------------------------------------------------------------- #
# Resume seam (R17): a resumed run consults the dedup record like a fresh run
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_enforce_skips_done(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(target=_TARGET, item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    _seed("done", platform="blogger", live_url="https://blogger.com/p/old")

    _out, _err, code = _run([], ["--resume", run_id], enforce=True)
    assert code == 0
    mock_pub.assert_not_called()  # done in dedup -> resume skips


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_enforce_holds_uncertain(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(target=_TARGET, item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    _seed("uncertain", platform="blogger")

    _run([], ["--resume", run_id], enforce=True)
    mock_pub.assert_not_called()  # uncertain -> held on resume


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_enforce_absent_dispatches(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(target=_TARGET, item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.com/p/new",
    )
    _out, _err, code = _run([], ["--resume", run_id], enforce=True)
    assert code == 0
    mock_pub.assert_called_once()  # absent -> dispatched


# --------------------------------------------------------------------------- #
# Crashed-in-flight (hard crash mid-dispatch): may-already-be-live safety
# --------------------------------------------------------------------------- #
def test_is_crashed_in_flight_true_only_for_stale_attempting():
    store = DedupStore()
    # absent key -> False (never dispatched)
    assert is_crashed_in_flight({"target_url": "https://example.com/absent"}, "medium") is False
    # live attempting (this PID) -> False (an active run owns it, not a crash)
    store.intent_write(DedupKey(platform="medium", target_url="https://example.com/live"),
                       owner_pid=os.getpid())
    assert is_crashed_in_flight({"target_url": "https://example.com/live"}, "medium") is False
    # stale attempting (dead PID) -> True (a run died mid-dispatch; may be live)
    store.intent_write(DedupKey(platform="medium", target_url="https://example.com/stale"),
                       owner_pid=2_147_483_000)
    assert is_crashed_in_flight({"target_url": "https://example.com/stale"}, "medium") is True
    # done -> False (settled, not in-flight)
    k_done = DedupKey(platform="medium", target_url="https://example.com/done")
    store.intent_write(k_done)
    store.transition(k_done, "done", live_url="https://medium.com/p/x")
    assert is_crashed_in_flight({"target_url": "https://example.com/done"}, "medium") is False
    # unusable key (no target_url) -> False (observe-safe)
    assert is_crashed_in_flight({}, "medium") is False


def test_is_crashed_in_flight_store_error_returns_false(mocker):
    # Observe-safe: a store read error must never break resume — return False.
    store = DedupStore()
    store.intent_write(DedupKey(platform="medium", target_url="https://example.com/err"),
                       owner_pid=2_147_483_000)
    mocker.patch.object(DedupStore, "get", side_effect=RuntimeError("dedup.db unreadable"))
    assert is_crashed_in_flight({"target_url": "https://example.com/err"}, "medium") is False


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_fresh_stale_attempting_holds(mock_pub, _mv):
    """Fresh seam (not resume): a stale-attempting key (prior run crashed mid-dispatch)
    is held + promoted to uncertain, never re-published. All-held -> exit 3."""
    DedupStore().intent_write(
        DedupKey(platform="medium", target_url=_TARGET), owner_pid=2_147_483_000
    )
    mock_pub.return_value = _drafted()
    _out, _err, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    mock_pub.assert_not_called()  # stale attempting held on the fresh seam too
    assert DedupStore().get(DedupKey(platform="medium", target_url=_TARGET)).state == "uncertain"
    assert code == 3  # all rows held -> operator-action-required (adjudicate)


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_enforce_holds_crashed_in_flight_as_uncertain(mock_pub, _mv, _ms, mock_cache, tmp_path):
    """Enforce: a pending item whose dedup row is a stale `attempting` (prior run
    crashed mid-dispatch) is HELD and promoted to uncertain — never re-published —
    so an already-live post is not duplicated."""
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(target=_TARGET, item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    key = DedupKey(platform="blogger", target_url=_TARGET)
    DedupStore().intent_write(key, owner_pid=2_147_483_000)  # dead PID -> stale

    _out, err, code = _run([], ["--resume", run_id], enforce=True)
    mock_pub.assert_not_called()  # stale attempting -> held, never re-published
    assert DedupStore().get(key).state == "uncertain"  # promoted for --adjudicate
    assert code == 4  # held item leaves the run "unfinished" until adjudicated
    # The operator must not blind-retry into the same hold: finalize surfaces the
    # adjudication path, and the held pending row is not mislabeled "unknown error".
    assert "was interrupted mid-publish" in err  # per-item warning (Phase 3)
    assert "--adjudicate-uncertain" in err  # finalize guidance (Phase 7)
    assert "unknown error" not in err


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_observe_warns_crashed_in_flight_then_dispatches(mock_pub, _mv, _ms, mock_cache, tmp_path):
    """Observe (default): the dedup gate dispatches by contract, but resume now
    WARNS on a stale-attempting item (parity with the http_5xx warning) so a hard
    crash is no longer silent."""
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(target=_TARGET, item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    DedupStore().intent_write(
        DedupKey(platform="blogger", target_url=_TARGET), owner_pid=2_147_483_000
    )
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.com/p/new",
    )
    _out, err, _code = _run([], ["--resume", run_id], enforce=False)
    assert "was interrupted mid-publish" in err
    assert "Verify before resuming" in err
    mock_pub.assert_called_once()  # observe dispatches by contract; warning is advisory


# --------------------------------------------------------------------------- #
# gate_and_claim concurrency (the core single-flight safety claim)
# --------------------------------------------------------------------------- #
def test_concurrent_gate_and_claim_exactly_one_dispatches(tmp_path):
    """Two threads race gate_and_claim on the same absent key: exactly one gets
    `dispatch` (claims attempting), the other `hold` (observes the live claim).
    Proves the BEGIN IMMEDIATE read-decide-claim is TOCTOU-safe for enforce."""
    db = tmp_path / "dedup.db"
    key = DedupKey(platform="medium", target_url=_TARGET)
    barrier = threading.Barrier(2)
    verdicts: list[str] = []
    lock = threading.Lock()

    def worker():
        s = DedupStore(path=db)
        barrier.wait()
        v = s.gate_and_claim(key, run_id="r", owner_pid=os.getpid()).verdict
        with lock:
            verdicts.append(v)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(verdicts) == ["dispatch", "hold"]  # exactly one winner


# --------------------------------------------------------------------------- #
# All rows held under enforce -> exit 3 (operator adjudication), not 5
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_all_held_exits_3_not_5(mock_pub, _mv):
    _seed("uncertain")
    _out, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 3  # DependencyError (adjudicate), not InternalError (5)
    assert "held" in stderr.lower()
    mock_pub.assert_not_called()
