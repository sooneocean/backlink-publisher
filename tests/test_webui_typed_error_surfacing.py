"""Unit 3 — the WebUI bridge consumes the typed-error envelope.

Layered on the fidelity work (run_pipe_capture / surface_cli_error): the bridge
now parses the Unit 1/2 envelope into ``PipeResult.error_class``/``.exit_code``
and surfaces the *full* message — the ``stderr[:200]`` truncation is gone — while
falling back to the full banner-stripped text (QUARANTINE) when no envelope is
present.
"""

from __future__ import annotations

import json
from unittest import mock

from backlink_publisher._util.error_envelope import ErrorEnvelope
from webui_app.api.pipeline_api import PipelineAPI, PipeResult
from webui_app.helpers.cli_runner import describe_cli_error, surface_cli_error

_BANNER = (
    "[publish-backlinks] effective config:\n"
    "  config:    /tmp/cfg\n"
    "  env:       (none)\n"
    "  platforms: medium\n"
    "  sha:       0123456789abcdef\n"
)


def _stderr_with_envelope(error_class: str, exit_code: int, message: str) -> str:
    return _BANNER + ErrorEnvelope(error_class, exit_code, message).serialize() + "\n"


# --- PipelineAPI typed-error surfacing ------------------------------------


def test_pipeline_api_parses_typed_envelope():
    stderr = _stderr_with_envelope(
        "AuthExpiredError", 3, "channel 'medium' credentials expired"
    )
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe", side_effect=Exception(stderr)
    ):
        result = PipelineAPI().publish("{}", "medium", "draft")
    assert result.success is False
    assert result.error_class == "AuthExpiredError"
    assert result.exit_code == 3
    assert result.error == "channel 'medium' credentials expired"


def test_pipeline_api_quarantine_when_no_envelope():
    # An argparse usage error / crash carries no envelope → QUARANTINE: loud,
    # full banner-stripped text, flagged "unrecognized" (never empty/truncated).
    # Uses publish() — a still-subprocess method — to exercise the run_pipe →
    # _typed_error_result bridge. (validate() is now in-process per thin-WebUI
    # Phase 2 Unit 6, so it no longer routes through run_pipe.)
    stderr = _BANNER + "error: unrecognized arguments: --bogus\n"
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe", side_effect=Exception(stderr)
    ):
        result = PipelineAPI().publish("{}", "medium", "draft")
    assert result.success is False
    assert result.error_class == "unrecognized"
    assert "unrecognized arguments: --bogus" in result.error
    assert result.exit_code is None


def test_pipeline_api_success_has_no_error():
    # U7: plan() is in-process; mock plan_rows not run_pipe.
    from backlink_publisher.cli.plan_backlinks._engine import PlanOutcome
    stub_outcome = PlanOutcome(outputs=[{"id": "1", "target_url": "https://x/y"}])
    with (
        mock.patch("backlink_publisher.config.load_config", return_value=mock.MagicMock()),
        mock.patch(
            "backlink_publisher.cli.plan_backlinks._engine.plan_rows",
            return_value=stub_outcome,
        ),
    ):
        result = PipelineAPI().plan("{}")
    assert result.success is True
    assert result.error is None
    assert result.error_class is None
    assert result.rows[0]["id"] == "1"


def test_pipeline_api_long_error_not_truncated():
    # Regression for the stderr[:200] bug: a long typed message survives in full.
    # publish() exercises the run_pipe bridge (validate() is now in-process, U6).
    long_msg = "validation failed: " + "x" * 600
    stderr = _stderr_with_envelope("InputValidationError", 2, long_msg)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe", side_effect=Exception(stderr)
    ):
        result = PipelineAPI().publish("{}", "medium", "draft")
    assert result.error == long_msg
    assert len(result.error) > 200  # the old [:200] truncation is gone


# --- describe_cli_error (string form, used by the checkpoint route) -------


def test_describe_cli_error_prefers_envelope():
    stderr = _stderr_with_envelope("ExternalServiceError", 4, "2 payload(s) failed")
    out = describe_cli_error(stderr)
    assert out == "[ExternalServiceError] 2 payload(s) failed"


def test_describe_cli_error_falls_back_to_full_text():
    stderr = _BANNER + "Traceback (most recent call last): KeyError: 'x'\n"
    out = describe_cli_error(stderr)
    # Banner stripped, real text retained, no envelope tag.
    assert "KeyError: 'x'" in out
    assert not out.startswith("[")
    assert out == surface_cli_error(stderr)


def test_pipeline_api_caps_oversized_envelope_message():
    # A huge envelope message (validate aggregate / untrusted snippet) must be
    # bounded the same as surface_cli_error — it flows into logs + history JSON.
    # publish() exercises the run_pipe bridge (validate() is now in-process, U6).
    huge = "x" * 50_000
    stderr = _stderr_with_envelope("InputValidationError", 2, huge)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe", side_effect=Exception(stderr)
    ):
        result = PipelineAPI().publish("{}", "medium", "draft")
    assert len(result.error) < len(huge)
    assert result.error.endswith("…(truncated)")
    assert len(result.error) <= 4000 + len(" …(truncated)")


def test_quarantine_does_not_leak_raw_sentinel():
    # A malformed/truncated envelope (parse rejects it) must NOT surface the raw
    # __BLP_ERR__ sentinel JSON to the operator — strip it on the human path.
    stderr = _BANNER + "__BLP_ERR__ {not valid json\nKeyError: 'x'\n"
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe", side_effect=Exception(stderr)
    ):
        result = PipelineAPI().publish("{}", "medium", "draft")
    assert result.error_class == "unrecognized"
    assert "__BLP_ERR__" not in result.error
    assert "KeyError: 'x'" in result.error  # the real error still surfaces


def test_describe_cli_error_strips_malformed_sentinel():
    stderr = _BANNER + "__BLP_ERR__ {broken\nboom: real error\n"
    out = describe_cli_error(stderr)
    assert "__BLP_ERR__" not in out
    assert "boom: real error" in out


def test_pipe_result_error_is_string_backward_compatible():
    # .error stays a plain str so existing slicing/format consumers don't break.
    r = PipeResult(success=False, error="boom", error_class="UsageError", exit_code=1)
    assert isinstance(r.error, str)
    assert r.error[:3] == "boo"  # subscriptable — no typed-object regression
