"""Tests for Medium inter-row throttle in publish_backlinks."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch, call

import pytest

from backlink_publisher.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.verify_publish import VerificationResult


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli.publish_backlinks.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _make_payload(platform="medium"):
    return {
        "id": f"id-{platform}",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test",
        "slug": "test",
        "excerpt": "A test.",
        "tags": [],
        "content_markdown": "About https://example.com content here.",
        "links": [
            {"url": "https://example.com", "anchor": "ex", "kind": "main_domain", "required": True},
            {"url": "https://example.com/a", "anchor": "a", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "w", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "m", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "s", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "g", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "T", "description": "D", "canonical_url": "https://example.com/article"},
    }


def _run(rows, argv=None, env=None):
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    old_env = dict(os.environ)
    try:
        if env:
            os.environ.update(env)
        sys.stdin = StringIO("\n".join(json.dumps(r) for r in rows))
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


def _medium_result():
    return AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/abc",
        post_publish_delay_seconds=30,  # R9c: adapter-declared throttle
    )


@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_throttle_between_two_medium_rows(mock_pub, mock_verify, mock_sleep):
    """Sleep called between two consecutive Medium rows."""
    mock_pub.return_value = _medium_result()
    rows = [_make_payload("medium"), _make_payload("medium")]
    _run(rows, ["--mode", "draft"])
    mock_sleep.assert_called_once()


@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_throttle_respects_env_override(mock_pub, mock_verify, mock_sleep):
    """Sleep value falls within MEDIUM_THROTTLE_MIN/MAX range."""
    mock_pub.return_value = _medium_result()
    rows = [_make_payload("medium"), _make_payload("medium")]
    _run(rows, ["--mode", "draft"], env={"MEDIUM_THROTTLE_MIN": "10", "MEDIUM_THROTTLE_MAX": "15"})

    assert mock_sleep.called
    sleep_val = mock_sleep.call_args[0][0]
    assert 10 <= sleep_val <= 15, f"Sleep {sleep_val} outside [10,15]"


@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_no_throttle_for_single_row(mock_pub, mock_verify, mock_sleep):
    """No sleep for a single row."""
    mock_pub.return_value = _medium_result()
    _run([_make_payload("medium")], ["--mode", "draft"])
    mock_sleep.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_no_throttle_on_dry_run(mock_pub, mock_verify, mock_sleep):
    """No sleep in dry-run mode."""
    mock_pub.return_value = AdapterResult(
        status="draft", adapter="medium-api", platform="medium",
        _dry_run=True, _command="plan"
    )
    rows = [_make_payload("medium"), _make_payload("medium")]
    _run(rows, ["--dry-run"])
    mock_sleep.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_no_throttle_when_next_row_is_blogger(mock_pub, mock_verify, mock_sleep):
    """No sleep when next row after Medium is a Blogger row."""
    def side_effect(payload, mode, config, dry_run=False):
        platform = payload.get("platform", "")
        return AdapterResult(
            status="drafted", adapter=f"{platform}-api", platform=platform,
            draft_url="https://url.example.com"
        )
    mock_pub.side_effect = side_effect

    rows = [_make_payload("medium"), _make_payload("blogger")]
    _run(rows, ["--mode", "draft"])
    mock_sleep.assert_not_called()
