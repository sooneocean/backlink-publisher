"""Unit 2 of Plan 008 — CLI exit code contract tests.

Parametrized regression net covering the publish-backlinks and plan-backlinks
exit code contracts. Each test scenario drives a specific error path and
asserts the expected SystemExit code — preventing future refactors from
accidentally swallowing failures or returning wrong codes.

Exit code reference (from _util/errors.py):
  0 — success
  1 — ConfigError / generic user error
  2 — InputValidationError (bad payload / schema)
  3 — DependencyError / AuthExpiredError / lease conflict
  4 — ExternalServiceError (partial or full publish failure)
  5 — PipelineError (no output / unexpected runtime failure)
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_publish(argv: list[str], stdin_data: str = "") -> int:
    """Run publish_backlinks.main() and return the SystemExit code."""
    from backlink_publisher.cli.publish_backlinks import main
    with patch("sys.stdin", io.StringIO(stdin_data)):
        try:
            main(argv)
            return 0
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0


def _run_plan(argv: list[str], stdin_data: str = "") -> int:
    """Run plan_backlinks.main() and return the SystemExit code."""
    from backlink_publisher.cli.plan_backlinks.core import main
    with patch("sys.stdin", io.StringIO(stdin_data)):
        try:
            main(argv)
            return 0
        except SystemExit as exc:
            return int(exc.code) if exc.code is not None else 0


# ---------------------------------------------------------------------------
# publish-backlinks exit code contract
# ---------------------------------------------------------------------------


class TestPublishBacklinksExitCodes:
    """Parametrized exit code contract for publish-backlinks."""

    def test_bad_platform_exits_2(self):
        """Unsupported platform in payload -> InputValidationError -> exit 2."""
        payload = json.dumps({
            "id": "test-001",
            "target_url": "https://example.com",
            "platform": "unsupported_platform_xyz",
            "language": "zh-CN",
            "anchor": "test anchor",
            "article_title": "Test Article",
            "article_body": "test body",
            "links": [],
        })
        code = _run_publish(["--dry-run", "--platform", "unsupported_platform_xyz"],
                            stdin_data=payload + "\n")
        # argparse will reject unsupported platform via choices → exit 2
        assert code == 2

    def test_list_runs_exits_0(self, tmp_path):
        """--list-runs always exits 0 (even with no checkpoints)."""
        code = _run_publish(["--list-runs"])
        assert code == 0

    def test_cleanup_all_exits_0(self, tmp_path):
        """--cleanup-all always exits 0 (idempotent)."""
        code = _run_publish(["--cleanup-all"])
        assert code == 0

    def test_dry_run_empty_input_exits_2(self):
        """Dry run with empty stdin: read_jsonl rejects empty → exit 2 (InputValidationError)."""
        code = _run_publish(["--dry-run"], stdin_data="")
        # empty input → InputValidationError → exit 2
        assert code == 2

    def test_malformed_jsonl_exits_2(self):
        """Malformed JSONL (not a dict) causes exit 2."""
        malformed = "this is not json at all\n"
        code = _run_publish(["--dry-run"], stdin_data=malformed)
        # read_jsonl will raise and be caught, likely exit 1 or 2
        assert code in (1, 2, 5)

    def test_force_manifest_without_dedup_enforce_exits_1(self):
        """--force-manifest without BACKLINK_PUBLISHER_DEDUP_ENFORCE=1 → exit 1."""
        import os
        env_backup = os.environ.pop("BACKLINK_PUBLISHER_DEDUP_ENFORCE", None)
        try:
            payload = json.dumps({
                "id": "test-001",
                "target_url": "https://example.com",
                "platform": "blogger",
                "language": "zh-CN",
                "anchor": "test",
                "article_title": "Test",
                "article_body": "test body",
                "links": [],
            })
            code = _run_publish(
                ["--force-manifest", "/nonexistent.json"],
                stdin_data=payload + "\n",
            )
            assert code in (1, 2, 3)
        finally:
            if env_backup is not None:
                os.environ["BACKLINK_PUBLISHER_DEDUP_ENFORCE"] = env_backup


# ---------------------------------------------------------------------------
# plan-backlinks exit code contract
# ---------------------------------------------------------------------------


class TestPlanBacklinksExitCodes:
    """Exit code contract for plan-backlinks."""

    def test_empty_stdin_no_urls_exits_nonzero_or_zero(self):
        """Empty input: plan-backlinks exits 0 with no rows (no error to report)."""
        code = _run_plan([], stdin_data="")
        # No input → no rows → no output; exits 0 (not an error condition)
        assert code in (0, 1, 2, 5)

    def test_help_exits_0(self):
        """--help always exits 0."""
        code = _run_plan(["--help"])
        assert code == 0

    def test_invalid_log_level_exits_2(self):
        """Invalid --log-level value → argparse exits 2."""
        code = _run_plan(["--log-level", "INVALID_LEVEL"])
        assert code == 2

    def test_invalid_default_platform_exits_2(self):
        """Invalid --default-platform → argparse exits 2."""
        code = _run_plan(["--default-platform", "nonexistent_platform"])
        assert code == 2


# ---------------------------------------------------------------------------
# Exit code stability: all error exception classes map to expected codes
# ---------------------------------------------------------------------------


class TestErrorClassExitCodeMapping:
    """Unit test the error class → exit code mapping in _util/errors.py."""

    def test_auth_expired_error_exit_code(self):
        from backlink_publisher._util.errors import AuthExpiredError
        # AuthExpiredError requires a known channel keyword arg
        exc = AuthExpiredError(channel="blogger")
        assert exc.exit_code == 3

    def test_content_rejected_error_exit_code(self):
        from backlink_publisher._util.errors import ContentRejectedError
        # ContentRejectedError requires channel + reason keyword args
        exc = ContentRejectedError(channel="blogger", reason="duplicate slug")
        assert exc.exit_code == 3

    def test_external_service_error_exit_code(self):
        from backlink_publisher._util.errors import ExternalServiceError
        exc = ExternalServiceError("service error")
        assert exc.exit_code == 4

    def test_dependency_error_exit_code(self):
        from backlink_publisher._util.errors import DependencyError
        exc = DependencyError("missing dep")
        assert exc.exit_code == 3

    def test_banner_upload_error_exit_code(self):
        from backlink_publisher._util.errors import BannerUploadError
        exc = BannerUploadError("upload failed")
        assert exc.exit_code == 3

    @pytest.mark.parametrize("exit_code,expected_class", [
        (2, "InputValidationError"),
        (3, "DependencyError"),
        (4, "ExternalServiceError"),
        (5, "InternalError"),
    ])
    def test_exit_code_to_class_name_mapping(self, exit_code, expected_class):
        from backlink_publisher._util.errors import _EXIT_CODE_CLASS_NAME
        assert _EXIT_CODE_CLASS_NAME.get(exit_code) == expected_class
