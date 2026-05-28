"""Tests for publish-backlinks --resume, --list-runs, --cleanup, --cleanup-all flags."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.checkpoint import create_checkpoint, update_item, mark_complete
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.linkcheck.verify import VerificationResult


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    """Default: verification always passes so tests stay fast and network-free."""
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _run_publish(
    input_data: str = "",
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_env = dict(os.environ)
    try:
        if env:
            os.environ.update(env)
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        os.environ.clear()
        os.environ.update(old_env)


def _make_payload(platform="blogger", item_id="r0"):
    target_url = f"https://example.com/{item_id}"
    return {
        "id": item_id,
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": target_url,
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": f"Test Article {item_id}",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": f"This is a test article about {target_url}.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": target_url, "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test SEO",
            "description": "SEO description",
            "canonical_url": target_url,
        },
    }


def _blogger_result(item_id="r0"):
    return AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url=f"https://blogger.example.com/p/{item_id}",
    )


# ── --list-runs ────────────────────────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
def test_list_runs_shows_incomplete(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(item_id=f"r{i}") for i in range(2)]
    create_checkpoint(rows, platform="blogger", mode="draft")

    stdout, stderr, code = _run_publish(argv=["--list-runs"])

    assert code == 0
    assert "pending" in stdout.lower() or stdout.count("20") >= 1  # table has content


@patch("backlink_publisher.checkpoint._cache_dir")
def test_list_runs_empty(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"

    stdout, stderr, code = _run_publish(argv=["--list-runs"])

    assert code == 0
    assert "No incomplete" in stdout


# ── --cleanup / --cleanup-all ──────────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
def test_cleanup_deletes_checkpoint(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload()]
    run_id, path = create_checkpoint(rows, platform="blogger", mode="draft")

    stdout, stderr, code = _run_publish(argv=["--cleanup", run_id])

    assert code == 0
    assert not path.exists()


@patch("backlink_publisher.checkpoint._cache_dir")
def test_cleanup_nonexistent_exits_2(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"

    stdout, stderr, code = _run_publish(argv=["--cleanup", "20260101T000000-deadbeef"])

    assert code == 2


@patch("backlink_publisher.checkpoint._cache_dir")
def test_cleanup_all_removes_only_complete(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload()]
    r1, p1 = create_checkpoint(rows, platform="blogger", mode="draft")
    r2, p2 = create_checkpoint(rows, platform="blogger", mode="draft")
    mark_complete(r1)

    stdout, stderr, code = _run_publish(argv=["--cleanup-all"])

    assert code == 0
    assert not p1.exists()
    assert p2.exists()


@patch("backlink_publisher.checkpoint._cache_dir")
def test_cleanup_all_empty_is_ok(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    stdout, stderr, code = _run_publish(argv=["--cleanup-all"])
    assert code == 0


# ── mutual exclusion ───────────────────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
def test_mutual_exclusion_resume_and_list_runs(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload()]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id, "--list-runs"])
    assert code == 2


# ── --resume happy paths ───────────────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_all_done_no_adapter_calls(mock_pub, mock_verify, mock_cache, tmp_path):
    """--resume on all-done checkpoint emits union, marks complete, makes no adapter calls."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(item_id=f"r{i}") for i in range(2)]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="https://x.com/a", completed_at="2026-01-01T00:00:00+00:00", adapter="blogger-api")
    update_item(run_id, "r1", "done", published_url="https://x.com/b", completed_at="2026-01-01T00:00:01+00:00", adapter="blogger-api")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0
    mock_pub.assert_not_called()
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2
    ids = {json.loads(l)["id"] for l in lines}
    assert ids == {"r0", "r1"}


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_skips_done_processes_failed_pending(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """3-row checkpoint (1 done, 1 failed, 1 pending) → skips done, processes other 2."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(platform="blogger", item_id=f"r{i}") for i in range(3)]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="https://x.com/a", completed_at="t", adapter="blogger-api")
    update_item(run_id, "r1", "failed", error="oops", error_class="transient")
    # r2 stays pending

    mock_pub.return_value = _blogger_result()

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0
    assert mock_pub.call_count == 2  # r1 and r2
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 3  # union: r0 + r1 + r2 (all done)


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_partial_still_failing_exits_4(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """After resume, 1 item still failing → exit(4), stdout has done items."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(platform="blogger", item_id=f"r{i}") for i in range(2)]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="https://x.com/a", completed_at="t", adapter="blogger-api")
    # r1 stays pending

    mock_pub.side_effect = ExternalServiceError("still broken")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 4
    # stdout still has r0 (done)
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "r0"


@patch("backlink_publisher.checkpoint._cache_dir")
def test_resume_nonexistent_exits_2(mock_cache, tmp_path):
    mock_cache.return_value = tmp_path / "cache"
    stdout, stderr, code = _run_publish(argv=["--resume", "20260101T000000-deadbeef"])
    assert code == 2
    assert "checkpoint not found" in stderr


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_empty_stdin_ok(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """--resume ignores stdin completely."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.return_value = _blogger_result("r0")

    stdout, stderr, code = _run_publish(input_data="", argv=["--resume", run_id])
    assert code == 0


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
def test_resume_verify_fails_exits_3(mock_verify, mock_cache, tmp_path):
    """verify_adapter_setup raises DependencyError on resume → exit(3), no adapter calls."""
    mock_cache.return_value = tmp_path / "cache"
    mock_verify.side_effect = DependencyError("oauth not configured")
    rows = [_make_payload(platform="blogger", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])
    assert code == 3


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_http_5xx_warning_in_stderr(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """http_5xx failed item → warning on stderr, adapter still called."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(platform="blogger", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    update_item(run_id, "r0", "failed", error="500 error", error_class="http_5xx")

    mock_pub.return_value = _blogger_result("r0")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert "HTTP 5xx" in stderr
    assert mock_pub.call_count == 1  # still called


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_union_preserves_original_order(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """Union output is in original checkpoint array order (not completion order)."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(platform="blogger", item_id=f"r{i}") for i in range(3)]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    # r0 and r2 done from prior run; r1 pending
    update_item(run_id, "r0", "done", published_url="https://x.com/0", completed_at="t0", adapter="blogger-api")
    update_item(run_id, "r2", "done", published_url="https://x.com/2", completed_at="t2", adapter="blogger-api")

    mock_pub.return_value = _blogger_result("r1")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 3
    ids = [json.loads(l)["id"] for l in lines]
    assert ids == ["r0", "r1", "r2"]  # original order


# ── R8 throttle on resume ──────────────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_throttle_applied_when_elapsed_under_300(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """If last Medium done was 200s ago → sleep is applied for first Medium item."""
    mock_cache.return_value = tmp_path / "cache"
    env = {"MEDIUM_THROTTLE_MIN": "10", "MEDIUM_THROTTLE_MAX": "20"}

    recent_ts = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    rows = [_make_payload(platform="medium", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="medium", mode="draft")
    # pre-mark r0 as done with recent medium timestamp; add a new pending r1 via create
    rows2 = [_make_payload(platform="medium", item_id="r1")]
    run_id2, _ = create_checkpoint(rows2, platform="medium", mode="draft")
    # patch a done item with recent medium completion in a fresh checkpoint
    rows3 = [
        _make_payload(platform="medium", item_id="done0"),
        _make_payload(platform="medium", item_id="pend1"),
    ]
    run_id3, _ = create_checkpoint(rows3, platform="medium", mode="draft")
    update_item(run_id3, "done0", "done", published_url="u", completed_at=recent_ts, adapter="medium-api")

    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/1",
    )

    stdout, stderr, code = _run_publish(argv=["--resume", run_id3], env=env)

    assert code == 0
    assert mock_sleep.called  # throttle was applied


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_throttle_skipped_when_elapsed_over_300(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """If last Medium done was 400s ago → no sleep for first Medium item."""
    mock_cache.return_value = tmp_path / "cache"

    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
    rows = [
        _make_payload(platform="medium", item_id="done0"),
        _make_payload(platform="medium", item_id="pend1"),
    ]
    run_id, _ = create_checkpoint(rows, platform="medium", mode="draft")
    update_item(run_id, "done0", "done", published_url="u", completed_at=old_ts, adapter="medium-api")

    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/2",
    )

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0
    mock_sleep.assert_not_called()


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_full_throttle_no_prior_medium(mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path):
    """No prior Medium done → full throttle applied for first Medium item."""
    mock_cache.return_value = tmp_path / "cache"
    env = {"MEDIUM_THROTTLE_MIN": "10", "MEDIUM_THROTTLE_MAX": "20"}

    rows = [_make_payload(platform="medium", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="medium", mode="draft")

    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/1",
    )

    stdout, stderr, code = _run_publish(argv=["--resume", run_id], env=env)

    assert code == 0
    assert mock_sleep.called


# ── item_to_publish_output has all required fields ───────────────────────────

def test_item_to_publish_output_has_all_fields():
    from backlink_publisher.cli._resume import item_to_publish_output
    item = {
        "id": "r0",
        "platform": "blogger",
        "status": "done",
        "title": "T",
        "published_url": "https://x.com",
        "article_urls": ["https://x.com"],
        "completed_at": "2026-01-01T00:00:00+00:00",
        "adapter": "blogger-api",
    }
    out = item_to_publish_output(item)
    required = {
        "id", "platform", "status", "title", "article_urls", "draft_url",
        "published_url", "created_at", "adapter", "error",
    }
    assert required.issubset(out.keys())
    assert out["created_at"] == item["completed_at"]
    assert out["article_urls"] == ["https://x.com"]


def test_item_to_publish_output_omits_verdict_when_absent():
    """Checkpoint items lack _provider_meta today → no verification key emitted."""
    from backlink_publisher.cli._resume import item_to_publish_output
    item = {"id": "r1", "platform": "txtfyi", "status": "done",
            "published_url": "https://txt.fyi/a", "adapter": "txtfyi-form"}
    out = item_to_publish_output(item)
    assert "link_attr_verification" not in out


def test_item_to_publish_output_emits_verdict_when_checkpoint_carries_it():
    """Forward-compatible: emit the verdict if a checkpoint item ever carries it."""
    from backlink_publisher.cli._resume import item_to_publish_output
    verdict = {"verification": "ok", "nofollow_detected": False}
    item = {"id": "r2", "platform": "txtfyi", "status": "done",
            "published_url": "https://txt.fyi/b", "adapter": "txtfyi-form",
            "link_attr_verification": verdict}
    out = item_to_publish_output(item)
    assert out["link_attr_verification"] == verdict


# ── integration: Unit 2 → Unit 3 ──────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_fresh_run_then_resume_full_flow(
    mock_resume_pub, mock_resume_verify,
    mock_pub, mock_verify, mock_sleep, mock_cache, tmp_path,
):
    """Simulate fresh run that fails on item 3, then resume processes item 3."""
    mock_cache.return_value = tmp_path / "cache"
    rows = [_make_payload(platform="blogger", item_id=f"r{i}") for i in range(3)]
    stdin_data = "\n".join(json.dumps(r) for r in rows)

    # Fresh run: r0 ok, r1 ok, r2 fails
    mock_pub.side_effect = [
        _blogger_result("r0"),
        _blogger_result("r1"),
        ExternalServiceError("r2 failed"),
    ]
    stdout, stderr, code = _run_publish(
        stdin_data, argv=["--mode", "draft", "--log-level", "INFO"]
    )
    assert code == 4
    assert "run_id=" in stderr
    run_id = stderr.split("run_id=")[1].split('"')[0].strip()

    # Resume: r2 now succeeds
    mock_resume_pub.side_effect = None
    mock_resume_pub.return_value = _blogger_result("r2")

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0
    assert mock_resume_pub.call_count == 1  # only r2
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 3  # r0 + r1 + r2

    # After full success, run is marked complete → list_incomplete returns []
    list_out, _, _ = _run_publish(argv=["--list-runs"])
    assert "No incomplete" in list_out


# ---------------------------------------------------------------------------
# Unit 3 — R7: resume path records forward-path drift advisory
# Plan 2026-05-27-006 Unit 3
# ---------------------------------------------------------------------------

def _drift_blogger_result(item_id: str = "r0", *, nofollow: bool = False) -> AdapterResult:
    """A blogger AdapterResult with target-specific link_attr_verification set."""
    link_attr = {
        "verification": "ok",
        "total_anchors": 2,
        "target_found": True,
        "target_nofollow": nofollow,
        "target_rewritten": False,
        "target_nofollow_urls": ["https://example.com"] if nofollow else [],
        "target_missing_urls": [],
        "target_rewritten_urls": [],
    }
    return AdapterResult(
        status="drafted",
        adapter="blogger-api",
        platform="blogger",
        draft_url=f"https://blogger.example.com/p/{item_id}",
        _provider_meta={"link_attr_verification": link_attr},
    )


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_records_publish_path_link_alive(
    mock_pub, mock_verify, mock_cache, tmp_path, monkeypatch
):
    """R7: --resume path records link-alive when required links are dofollow."""
    mock_cache.return_value = tmp_path / "cache"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.canary import store as cstore
    cstore.canary_health_store.reset()

    rows = [_make_payload(item_id="r0")]
    create_checkpoint(rows, platform="blogger", mode="draft")
    run_id = [
        f.stem for f in (tmp_path / "cache" / "checkpoints").glob("*.json")
    ][0]

    mock_pub.return_value = _drift_blogger_result("r0", nofollow=False)

    _run_publish(argv=["--resume", run_id])

    health = cstore.get_publish_path_health("blogger")
    assert health["status"] == cstore.STATUS_LINK_ALIVE
    cstore.canary_health_store.reset()


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_records_publish_path_drift_nofollow(
    mock_pub, mock_verify, mock_cache, tmp_path, monkeypatch
):
    """R7: --resume path records drift when required link is nofollow; no exit-code change."""
    mock_cache.return_value = tmp_path / "cache"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.canary import store as cstore
    cstore.canary_health_store.reset()

    rows = [_make_payload(item_id="r0")]
    create_checkpoint(rows, platform="blogger", mode="draft")
    run_id = [
        f.stem for f in (tmp_path / "cache" / "checkpoints").glob("*.json")
    ][0]

    mock_pub.return_value = _drift_blogger_result("r0", nofollow=True)

    stdout, stderr, code = _run_publish(argv=["--resume", run_id])

    assert code == 0, f"drift must not change exit code. stderr={stderr}"
    health = cstore.get_publish_path_health("blogger")
    assert health["status"] == cstore.STATUS_DRIFT_CONFIRMED
    cstore.canary_health_store.reset()
