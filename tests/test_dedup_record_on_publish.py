"""Unit 2 — record-on-publish (observe-only): the publish path records dedup state
across **both** the fresh (``publish_backlinks``) and resume (``_resume``) seams
WITHOUT gating. Publish behavior is unchanged; a dedup-store failure is swallowed.

Failure -> state mapping (R8, conservative): only ``http_5xx`` -> ``uncertain``;
every other error class -> ``failed`` (re-publishable).

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U2).
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.retry import RETRYABLE_HTTP_STATUSES
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.idempotency import DedupKey, DedupStore
from backlink_publisher.linkcheck.verify import VerificationResult


_TARGET = "https://example.com/article"


@pytest.fixture(autouse=True)
def _fresh_dedup_dir(tmp_path, monkeypatch):
    """Per-test config dir so the dedup store starts empty each test. The session
    conftest sandbox is shared, which would otherwise let one test's terminal row
    suppress the next test's recording. monkeypatch.setenv (not del) per
    ``feedback_del_os_environ_poisons_later_tests``."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _payload(platform: str = "medium") -> dict:
    return {
        "id": "abc123",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": _TARGET,
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": "Test article about https://example.com and more.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": _TARGET, "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": _TARGET,
        },
    }


def _drafted(platform="medium", adapter="medium-api") -> AdapterResult:
    return AdapterResult(
        status="drafted",
        adapter=adapter,
        platform=platform,
        draft_url="https://medium.com/p/abc123",
        published_url="",
    )


def _run(input_data: str, argv: list[str]) -> tuple[str, str, int]:
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO(input_data)
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


def _record(platform="medium"):
    """Read back the dedup record the publish path should have written."""
    return DedupStore().get(DedupKey(platform=platform, target_url=_TARGET))


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_success_records_done_with_verify_ok(mock_pub, _mock_verify):
    mock_pub.return_value = _drafted()
    _, stderr, code = _run(json.dumps(_payload()), ["--platform", "medium", "--mode", "draft"])
    assert code == 0, stderr

    rec = _record()
    assert rec is not None
    assert rec.state == "done"
    assert rec.verify_ok is True
    assert rec.live_url == "https://medium.com/p/abc123"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_verify_failure_still_records_done(mock_pub, _mock_verify, mocker):
    """A verify flake leaves the key done (re-publish must not be unlocked)."""
    mock_pub.return_value = _drafted()
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=False, reason="404"),
    )
    # Unverified publish exits 5 (documented), but the dedup row is still done —
    # a verify flake must not leave the key re-publishable.
    _, _stderr, code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert code == 5

    rec = _record()
    assert rec.state == "done"
    assert rec.verify_ok is False


# --------------------------------------------------------------------------- #
# Failure -> state mapping (R8)
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_http_5xx_records_uncertain(mock_pub, _mock_verify):
    """A 5xx (may-have-committed) holds the key uncertain — never auto-retried."""
    mock_pub.side_effect = ExternalServiceError("503 Service Unavailable")
    _, _stderr, _code = _run(json.dumps(_payload()), ["--platform", "medium"])

    rec = _record()
    assert rec is not None
    assert rec.state == "uncertain"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_transient_records_failed(mock_pub, _mock_verify):
    """A non-5xx transport error is confirmed-not-landed -> failed (re-publishable)."""
    mock_pub.side_effect = ExternalServiceError("connection reset by peer")
    _, _stderr, _code = _run(json.dumps(_payload()), ["--platform", "medium"])

    rec = _record()
    assert rec is not None
    assert rec.state == "failed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_content_rejected_records_failed(mock_pub, _mock_verify):
    mock_pub.side_effect = ContentRejectedError(channel="medium", reason="rejected by platform")
    _, _stderr, _code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert _record().state == "failed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_banner_upload_records_failed(mock_pub, _mock_verify):
    mock_pub.side_effect = BannerUploadError("banner upload failed")
    _, _stderr, _code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert _record().state == "failed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_unexpected_records_failed(mock_pub, _mock_verify):
    mock_pub.side_effect = RuntimeError("boom")
    _, _stderr, _code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert _record().state == "failed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_dependency_error_records_failed_before_exit3(mock_pub, _mock_verify):
    mock_pub.side_effect = DependencyError("oauth not configured")
    _, _stderr, code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert code == 3
    assert _record().state == "failed"


@patch("backlink_publisher.cli.publish_backlinks._handle_auth_expired")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_auth_expired_records_failed(mock_pub, _mock_verify, _mock_handle):
    mock_pub.side_effect = AuthExpiredError(channel="medium")
    _run(json.dumps(_payload()), ["--platform", "medium"])
    assert _record().state == "failed"


# --------------------------------------------------------------------------- #
# Observe-only invariants
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_dry_run_records_nothing(mock_pub):
    mock_pub.return_value = AdapterResult(
        status="draft", adapter="medium-api", platform="medium",
        _dry_run=True, _command="dry-run",
    )
    _, _stderr, code = _run(json.dumps(_payload()), ["--dry-run"])
    assert code == 0
    assert _record() is None  # dry-run never touches the dedup store


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_store_failure_does_not_break_publish(mock_pub, _mock_verify, mocker):
    """Observe-safe: a dedup-store error is swallowed; the publish still succeeds."""
    mock_pub.return_value = _drafted()
    mocker.patch(
        "backlink_publisher.cli._dedup_gate.DedupStore",
        side_effect=RuntimeError("disk full"),
    )
    stdout, stderr, code = _run(json.dumps(_payload()), ["--platform", "medium"])
    assert code == 0, stderr
    output = json.loads(stdout.strip())
    assert output["status"] == "drafted"


def test_retryable_http_statuses_is_429_only():
    """U2 invariant: 429 is the only retryable status. If this set grows, the
    5xx->uncertain hold reasoning (R8) must be revisited."""
    assert RETRYABLE_HTTP_STATUSES == frozenset({429})


# --------------------------------------------------------------------------- #
# Resume seam parity (_resume has its own dispatch loop)
# --------------------------------------------------------------------------- #
def _checkpoint_payload(platform="blogger", item_id="r0") -> dict:
    p = _payload(platform)
    p["id"] = item_id
    return p


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_success_records_done(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_checkpoint_payload(platform="blogger", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/r0",
    )
    _, stderr, code = _run("", ["--resume", run_id])
    assert code == 0, stderr

    rec = _record("blogger")
    assert rec is not None
    assert rec.state == "done"
    assert rec.verify_ok is True
    assert rec.live_url == "https://blogger.example.com/p/r0"


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_http_5xx_records_uncertain(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_checkpoint_payload(platform="blogger", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.side_effect = ExternalServiceError("502 Bad Gateway")
    _run("", ["--resume", run_id])

    rec = _record("blogger")
    assert rec is not None
    assert rec.state == "uncertain"


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_transient_records_failed(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_checkpoint_payload(platform="blogger", item_id="r0")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.side_effect = ExternalServiceError("connection reset")
    _run("", ["--resume", run_id])

    rec = _record("blogger")
    assert rec is not None
    assert rec.state == "failed"


# --------------------------------------------------------------------------- #
# Terminal-write guards (ce:review fixes): never downgrade a held key
# --------------------------------------------------------------------------- #
def test_uncertain_not_downgraded_to_failed_by_later_non_5xx():
    """A 5xx-set `uncertain` key must NOT be flipped to `failed` (re-publishable)
    by a subsequent non-5xx failure — that would let enforce re-publish a post
    that may have committed. Regression for the ce:review P1."""
    from backlink_publisher.cli._dedup_gate import record_failure

    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    store.intent_write(key)
    store.transition(key, "uncertain")

    row = {"id": "x", "target_url": _TARGET}
    record_failure(row, "medium", error_class="unexpected", run_id="r")  # non-5xx
    assert store.get(key).state == "uncertain"  # held, not demoted


def test_uncertain_can_still_settle_to_done():
    """A subsequent confirmed success DOES settle an uncertain key to done."""
    from backlink_publisher.cli._dedup_gate import record_done

    store = DedupStore()
    key = DedupKey(platform="medium", target_url=_TARGET)
    store.intent_write(key)
    store.transition(key, "uncertain")

    row = {"id": "x", "target_url": _TARGET}
    record_done(row, "medium", live_url="https://m/p", verify_ok=True, run_id="r")
    assert store.get(key).state == "done"


# --------------------------------------------------------------------------- #
# Resume seam: in-band adapter error (returned, not raised) records failed
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_inband_error_records_failed_not_done(mock_pub, mock_cache, _ms, _mv, tmp_path):
    """A resume dispatch returning AdapterResult(error=...) (not raising) records
    `failed`, not `done` — parity with the fresh seam, so enforce won't later skip
    a post that never landed."""
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    mock_pub.return_value = AdapterResult(
        status="failed", adapter="blogger-api", platform="blogger",
        draft_url="", published_url="", error="rejected in-band",
    )
    _run("", ["--resume", run_id])
    rec = _record("blogger")
    assert rec is not None
    assert rec.state == "failed"
