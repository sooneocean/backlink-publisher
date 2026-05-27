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
def test_gate_holds_on_live_attempting_reclaims_on_stale():
    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    # Live owner (this PID, fresh) -> a second runner must HOLD.
    store.intent_write(key, owner_pid=os.getpid())
    assert store.gate_and_claim(key, run_id="b").verdict == "hold"

    # Dead-PID attempting (lease-takeover after crash) -> reclaim + dispatch.
    key2 = DedupKey(platform="velog", target_url="https://example.com/x")
    store.intent_write(key2, owner_pid=2_147_483_000)  # almost certainly dead
    decision = store.gate_and_claim(key2, run_id="b")
    assert decision.verdict == "dispatch"
    assert store.get(key2).state == "attempting"


def test_gate_reclaims_attempting_aged_past_ttl():
    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    store.intent_write(key, owner_pid=os.getpid())
    # now far in the future -> aged past the TTL backstop -> reclaim.
    assert store.gate_and_claim(
        key, run_id="b", now=time.time() + 10_000
    ).verdict == "dispatch"


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
