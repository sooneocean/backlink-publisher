"""Integration tests: publish-backlinks + post-publish verification."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.errors import ExternalServiceError
from backlink_publisher.verify_publish import VerificationResult


def _run_publish(
    input_data: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    old_env = dict(os.environ)
    try:
        if env:
            os.environ.update(env)
        sys.stdin = StringIO(input_data)
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
        os.environ.clear()
        os.environ.update(old_env)


def _make_payload(platform="blogger"):
    return {
        "id": "test001",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1"],
        "content_markdown": "This is a test article about https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GH", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test SEO",
            "description": "SEO desc",
            "canonical_url": "https://example.com/article",
        },
    }


# ── happy path: verification passes ───────────────────────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_verify_passes_exit_0(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )
    mock_vp.return_value = VerificationResult(ok=True, reason="")

    stdout, stderr, code = _run_publish(
        json.dumps(_make_payload()), ["--mode", "draft"]
    )
    assert code == 0
    mock_vp.assert_called_once()
    out = json.loads(stdout.strip())
    assert "unverified" not in out["status"]


# ── verification fails: exit 5, status = *_unverified ──────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_verify_fails_exit_5_status_unverified(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )
    mock_vp.return_value = VerificationResult(ok=False, reason="HTTP 404")

    stdout, stderr, code = _run_publish(
        json.dumps(_make_payload()), ["--mode", "draft"]
    )
    assert code == 5
    out = json.loads(stdout.strip())
    assert out["status"] == "drafted_unverified"
    assert "verification failed" in stderr


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_verify_fails_published_mode_gets_unverified_status(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="published", adapter="blogger-api", platform="blogger",
        published_url="https://blogger.example.com/post/abc",
    )
    mock_vp.return_value = VerificationResult(ok=False, reason="title not found")

    payload = _make_payload()
    payload["publish_mode"] = "publish"
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "publish"])
    assert code == 5
    out = json.loads(stdout.strip())
    assert out["status"] == "published_unverified"


# ── --no-verify skips verification ────────────────────────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_no_verify_skips_verification(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )

    stdout, stderr, code = _run_publish(
        json.dumps(_make_payload()), ["--mode", "draft", "--no-verify"]
    )
    assert code == 0
    mock_vp.assert_not_called()
    out = json.loads(stdout.strip())
    assert out["status"] == "drafted"  # not unverified


# ── dry-run skips verification ─────────────────────────────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_dry_run_skips_verification(mock_vp, mock_pub):
    mock_pub.return_value = AdapterResult(
        status="draft", adapter="blogger-api", platform="blogger",
        _dry_run=True, _command="dry-run plan",
    )

    stdout, stderr, code = _run_publish(
        json.dumps(_make_payload()), ["--dry-run"]
    )
    assert code == 0
    mock_vp.assert_not_called()


# ── publish failure + verification: exit 4 (not 5) ────────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_publish_failure_dominates_over_unverified(mock_vp, mock_pub, mock_verify):
    """If one item fails to publish and another is unverified, exit 4 (not 5)."""
    payloads = [_make_payload(), {**_make_payload(), "id": "test002"}]

    mock_pub.side_effect = [
        ExternalServiceError("service down"),  # first fails
        AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                      draft_url="https://x.com/p/2"),  # second succeeds
    ]
    mock_vp.return_value = VerificationResult(ok=False, reason="title missing")

    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "draft"]
    )
    assert code == 4  # publish failure exit takes precedence


# ── verify uses correct URL (published_url preferred over draft_url) ───────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_verify_uses_published_url_when_available(mock_vp, mock_pub, mock_verify):
    published = "https://blogger.example.com/2026/live-post.html"
    mock_pub.return_value = AdapterResult(
        status="published", adapter="blogger-api", platform="blogger",
        published_url=published,
        draft_url="https://blogger.example.com/p/draft",
    )
    mock_vp.return_value = VerificationResult(ok=True, reason="")

    _run_publish(json.dumps(_make_payload()), ["--mode", "publish"])

    call_args = mock_vp.call_args
    assert call_args[0][0] == published  # first positional arg is url


# ── Medium adapter uses max_wait=30 ───────────────────────────────────────────

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_medium_uses_long_max_wait(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/abc",
        post_publish_delay_seconds=30,  # R9c: adapter-declared throttle
    )
    mock_vp.return_value = VerificationResult(ok=True, reason="")

    _run_publish(json.dumps({**_make_payload(), "platform": "medium"}), ["--mode", "draft"])

    call_kwargs = mock_vp.call_args[1]
    assert call_kwargs.get("max_wait") == 30


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
@patch("backlink_publisher.cli.publish_backlinks.verify_published")
def test_blogger_uses_short_max_wait(mock_vp, mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )
    mock_vp.return_value = VerificationResult(ok=True, reason="")

    _run_publish(json.dumps(_make_payload()), ["--mode", "draft"])

    call_kwargs = mock_vp.call_args[1]
    assert call_kwargs.get("max_wait") == 10
