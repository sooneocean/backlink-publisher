"""Characterization net for the fresh publish_backlinks.main() path.

Pins the cross-iteration / control-flow behaviors that the decomposition plan
(docs/plans/2026-06-02-001-refactor-publish-backlinks-decompose-plan.md, Unit 1)
must preserve byte-identically — specifically the ones the existing 187-patch
suite does NOT already cover:

- R3a: AuthExpiredError mid-run returns from main() and SKIPS _publish_epilogue.
  A naive loop-extraction would make the epilogue run on auth-abort. Pinned by
  spying _publish_epilogue.
- run_id value-rebinding: a failed checkpoint.update_item nulls run_id, so the
  next row attempts no checkpoint update (it is value-threaded, not by-ref).
- output-row shape parity (the baseline the extraction must not drift).

Mirrors the harness in tests/test_publish_backlinks_auth_expired_flip.py.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher._util.errors import AuthExpiredError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.linkcheck.verify import VerificationResult


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    fake_config_dir = tmp_path / "config"
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ), patch(
        "backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache",
    ):
        from webui_store.channel_status import channel_status_store as _store
        _store.path = fake_config_dir / "channel-status.json"
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _mock_verify_pass():
    with patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    ):
        yield


def _run_publish(input_data, argv=None):
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin, sys.stdout, sys.stderr = StringIO(input_data), StringIO(), StringIO()
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return sys.stdout.getvalue(), sys.stderr.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old


def _payload(platform="blogger", row_id="char-1"):
    return {
        "id": row_id, "platform": platform, "language": "en", "publish_mode": "draft",
        "target_url": "https://example.com/article", "main_domain": "https://example.com",
        "url_mode": "A", "title": "Test Article", "slug": "test-article",
        "excerpt": "An excerpt.", "tags": ["tag1"],
        "content_markdown": "Content about https://example.com page.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GH", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "T", "description": "D", "canonical_url": "https://example.com/article"},
    }


def _ok_result():
    return AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )


class TestAuthExpiredSkipsEpilogue:
    """R3a: AuthExpiredError must short-circuit main() BEFORE _publish_epilogue."""

    @patch("backlink_publisher.cli.publish_backlinks._publish_epilogue")
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_auth_expired_skips_epilogue(self, mock_pub, mock_verify, mock_epilogue):
        mock_pub.side_effect = AuthExpiredError(channel="medium", reason="Medium /me HTTP 401")
        _, _, code = _run_publish(
            json.dumps(_payload(platform="medium")),
            ["--platform", "medium", "--mode", "draft", "--skip-publish-time-check"],
        )
        assert code == 3, f"auth-expired should exit 3, got {code}"
        mock_epilogue.assert_not_called()  # the load-bearing R3a invariant

    @patch("backlink_publisher.cli.publish_backlinks._publish_epilogue")
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_normal_success_calls_epilogue(self, mock_pub, mock_verify, mock_epilogue):
        mock_pub.return_value = _ok_result()
        _run_publish(json.dumps(_payload()), ["--mode", "draft", "--skip-publish-time-check"])
        mock_epilogue.assert_called_once()  # contrast: normal path DOES reach the epilogue


class TestRunIdNulledOnCheckpointFailure:
    """run_id is value-rebound: a failed checkpoint update nulls it, so the next
    row attempts no further checkpoint update (guarded by `if run_id is not None`)."""

    @patch("backlink_publisher.checkpoint.update_item")
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_checkpoint_update_failure_nulls_run_id(self, mock_pub, mock_verify, mock_update):
        mock_pub.return_value = _ok_result()
        mock_update.side_effect = RuntimeError("checkpoint write blew up")
        rows = "\n".join(json.dumps(_payload(row_id=f"r{i}")) for i in range(2))
        _run_publish(rows, ["--mode", "draft", "--skip-publish-time-check"])
        # Row 1's success-path update_item raises -> run_id nulled -> row 2 skips it.
        assert mock_update.call_count == 1, (
            f"expected exactly 1 update_item call (run_id nulled after the first "
            f"failure), got {mock_update.call_count}"
        )


class TestOutputRowShapeParity:
    """Baseline output-row shape the extraction must not drift."""

    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_published_row_shape(self, mock_pub, mock_verify):
        mock_pub.return_value = _ok_result()
        stdout, _, code = _run_publish(
            json.dumps(_payload()), ["--mode", "draft", "--skip-publish-time-check"]
        )
        assert code == 0
        out = json.loads(stdout.strip())
        for key in ("id", "platform", "status", "title", "draft_url",
                    "published_url", "created_at", "adapter", "error"):
            assert key in out, f"published row missing key {key!r}: {out}"
        assert out["status"] == "drafted"
        assert out["adapter"] == "blogger-api"
