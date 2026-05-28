"""Unit tests for the pure report-anchors engine (thin-WebUI Phase 2 Unit 7).

Tests :func:`backlink_publisher.cli._report_engine.report_from_profile` and
:func:`~.report_from_rows` DIRECTLY — no stdin/stdout, no SystemExit, no banner.
The engine is the shared kernel behind both the CLI shell
(``cli/report_anchors.py``) and the in-process ``PipelineAPI.report_anchors``.
CLI-shell behavior guards live in ``tests/test_report_anchors.py``; these pin
the pure engine contract.

Coverage goals (plan spec):
- report_from_rows: produces a document string, never alarms, exit_code=0.
- report_from_rows: as_json=True → JSON document, as_json=False → markdown.
- report_from_profile: alarm_breach=False → exit_code=0, document populated.
- report_from_profile: alarm_breach=True → exit_code=6, document still populated.
- Engine MUST NOT write to sys.stdout (H3).
- ReportOutcome fields: document, alarm_breach, breach_count, breach_lines, exit_code.
- PipelineAPI.report_anchors: returns PipeResult, alarm → advisory error_class.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import json
import pytest

from backlink_publisher.cli._report_engine import (
    ReportOutcome,
    report_from_rows,
    report_from_profile,
)


# ── ReportOutcome defaults ────────────────────────────────────────────────────


def test_report_outcome_defaults():
    outcome = ReportOutcome()
    assert outcome.document == ""
    assert outcome.alarm_breach is False
    assert outcome.breach_count == 0
    assert outcome.breach_lines == []
    assert outcome.exit_code == 0


# ── report_from_rows ──────────────────────────────────────────────────────────


def test_report_from_rows_empty_returns_outcome():
    outcome = report_from_rows([])
    assert isinstance(outcome, ReportOutcome)
    assert isinstance(outcome.document, str)
    assert outcome.alarm_breach is False
    assert outcome.exit_code == 0


def test_report_from_rows_as_json_false_produces_markdown():
    outcome = report_from_rows([], as_json=False)
    # Markdown output; may be empty table or header but must be a string.
    assert isinstance(outcome.document, str)


def test_report_from_rows_as_json_true_produces_json():
    outcome = report_from_rows([], as_json=True)
    # Should be parseable JSON.
    parsed = json.loads(outcome.document)
    assert isinstance(parsed, dict)


def test_report_from_rows_never_alarms():
    """The stdin-aggregate path is structurally incapable of computing the alarm."""
    rows = [
        {
            "platform": "medium",
            "main_domain": "https://51acgs.com",
            "target_url": "https://51acgs.com/a",
            "links": [
                {"anchor": "test", "url": "https://51acgs.com", "kind": "main_domain"},
            ],
        }
    ]
    outcome = report_from_rows(rows)
    assert outcome.alarm_breach is False
    assert outcome.exit_code == 0
    assert outcome.breach_lines == []


def test_report_from_rows_does_not_write_stdout(capsys):
    """Engine MUST NOT write to sys.stdout (H3)."""
    report_from_rows([])
    captured = capsys.readouterr()
    assert captured.out == ""


# ── report_from_profile ───────────────────────────────────────────────────────


def _stub_profile():
    """Minimal profile object accepted by _build_profile_report."""
    return {
        "main_domain": "https://51acgs.com",
        "entries": [],
    }


def _stub_config_for_report():
    cfg = MagicMock()
    cfg.anchor_proportions = MagicMock()
    cfg.anchor_proportions.branded = 0.2
    cfg.anchor_proportions.exact = 0.2
    cfg.anchor_proportions.partial = 0.2
    cfg.anchor_proportions.generic = 0.2
    cfg.anchor_proportions.naked = 0.2
    cfg.anchor_alarm = MagicMock()
    cfg.anchor_alarm.enabled = False
    return cfg


def test_report_from_profile_no_alarm(tmp_path):
    """Profile with no alarm breach → document populated, exit_code=0."""
    cfg = _stub_config_for_report()
    alarm_block = {"any_breach": False, "targets": []}

    with (
        patch(
            "backlink_publisher.cli._report_engine.load_profile",
            return_value=_stub_profile(),
        ),
        patch(
            "backlink_publisher.cli._report_engine._build_profile_report",
            return_value={"alarm": None},
        ),
        patch(
            "backlink_publisher.cli._report_engine._compute_alarm",
            return_value=(alarm_block, []),
        ),
        patch(
            "backlink_publisher.cli._report_engine._format_profile_report_json",
            return_value='{"ok": true}',
        ),
    ):
        outcome = report_from_profile("https://51acgs.com", cfg, as_json=True)

    assert isinstance(outcome, ReportOutcome)
    assert outcome.alarm_breach is False
    assert outcome.exit_code == 0
    assert outcome.document == '{"ok": true}'
    assert outcome.breach_lines == []


def test_report_from_profile_alarm_breach(tmp_path):
    """Profile with alarm breach → exit_code=6, document still populated."""
    cfg = _stub_config_for_report()
    alarm_block = {"any_breach": True, "targets": ["https://51acgs.com/t1"]}
    breach_lines = ["WARN: target breach"]

    with (
        patch(
            "backlink_publisher.cli._report_engine.load_profile",
            return_value=_stub_profile(),
        ),
        patch(
            "backlink_publisher.cli._report_engine._build_profile_report",
            return_value={"alarm": None},
        ),
        patch(
            "backlink_publisher.cli._report_engine._compute_alarm",
            return_value=(alarm_block, breach_lines),
        ),
        patch(
            "backlink_publisher.cli._report_engine._format_profile_report_json",
            return_value='{"alarm": "breach"}',
        ),
    ):
        outcome = report_from_profile("https://51acgs.com", cfg, as_json=True)

    assert outcome.alarm_breach is True
    assert outcome.exit_code == 6
    assert outcome.breach_count == 1
    assert outcome.breach_lines == ["WARN: target breach"]
    # Document STILL populated even when alarm fires.
    assert outcome.document == '{"alarm": "breach"}'


def test_report_from_profile_does_not_write_stdout(capsys):
    """Engine MUST NOT write to sys.stdout (H3)."""
    cfg = _stub_config_for_report()
    with (
        patch(
            "backlink_publisher.cli._report_engine.load_profile",
            return_value=_stub_profile(),
        ),
        patch(
            "backlink_publisher.cli._report_engine._build_profile_report",
            return_value={},
        ),
        patch(
            "backlink_publisher.cli._report_engine._compute_alarm",
            return_value=({"any_breach": False}, []),
        ),
        patch(
            "backlink_publisher.cli._report_engine._format_profile_report_json",
            return_value="{}",
        ),
    ):
        report_from_profile("https://51acgs.com", cfg, as_json=True)

    captured = capsys.readouterr()
    assert captured.out == ""


# ── PipelineAPI.report_anchors in-process path ───────────────────────────────


def test_pipeline_api_report_anchors_returns_pipe_result():
    """PipelineAPI.report_anchors() returns a PipeResult (not raise)."""
    from webui_app.api.pipeline_api import PipelineAPI, PipeResult

    cfg_mock = _stub_config_for_report()
    alarm_block = {"any_breach": False}

    api = PipelineAPI()
    with (
        patch("backlink_publisher.config.load_config", return_value=cfg_mock),
        patch(
            "backlink_publisher.cli._report_engine.load_profile",
            return_value=_stub_profile(),
        ),
        patch(
            "backlink_publisher.cli._report_engine._build_profile_report",
            return_value={},
        ),
        patch(
            "backlink_publisher.cli._report_engine._compute_alarm",
            return_value=(alarm_block, []),
        ),
        patch(
            "backlink_publisher.cli._report_engine._format_profile_report_json",
            return_value='{"ok": 1}',
        ),
    ):
        result = api.report_anchors("https://51acgs.com")

    assert isinstance(result, PipeResult)
    assert result.success is True
    assert result.stdout == '{"ok": 1}'


def test_pipeline_api_report_anchors_alarm_is_advisory():
    """Alarm exit-6 → PipeResult.success=False, exit_code=6, stdout still populated."""
    from webui_app.api.pipeline_api import PipelineAPI

    cfg_mock = _stub_config_for_report()
    alarm_block = {"any_breach": True}
    breach_lines = ["WARN line"]

    api = PipelineAPI()
    with (
        patch("backlink_publisher.config.load_config", return_value=cfg_mock),
        patch(
            "backlink_publisher.cli._report_engine.load_profile",
            return_value=_stub_profile(),
        ),
        patch(
            "backlink_publisher.cli._report_engine._build_profile_report",
            return_value={},
        ),
        patch(
            "backlink_publisher.cli._report_engine._compute_alarm",
            return_value=(alarm_block, breach_lines),
        ),
        patch(
            "backlink_publisher.cli._report_engine._format_profile_report_json",
            return_value='{"alarm": true}',
        ),
    ):
        result = api.report_anchors("https://51acgs.com")

    assert result.success is False
    assert result.error_class == "AnchorDistributionAlarm"
    assert result.exit_code == 6
    # Document still on stdout even when alarm fires.
    assert result.stdout == '{"alarm": true}'


def test_pipeline_api_report_anchors_config_failure():
    """Config load failure → PipeResult error, not exception."""
    from webui_app.api.pipeline_api import PipelineAPI

    api = PipelineAPI()
    with patch(
        "backlink_publisher.config.load_config",
        side_effect=RuntimeError("no config"),
    ):
        result = api.report_anchors("https://51acgs.com")

    assert not result.success
    assert result.error_class == "InputValidationError"
