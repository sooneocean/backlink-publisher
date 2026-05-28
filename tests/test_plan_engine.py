"""Unit tests for the pure plan-backlinks engine (thin-WebUI Phase 2 Unit 7).

Tests :func:`backlink_publisher.cli.plan_backlinks._engine.plan_rows` DIRECTLY —
no stdin/stdout, no SystemExit, no banner. The engine is the shared kernel behind
both the CLI shell (``cli/plan_backlinks/core.py``) and the in-process
``PipelineAPI.plan``. CLI-shell behavior guards live in
``tests/test_plan_backlinks.py``; these pin the pure engine contract.

Coverage goals (plan spec):
- Happy path: engine produces output rows with the correct shape.
- Error path: invalid seed row → collected into errors, no raise.
- Cell gate drop: enrolled site, wrong platform → dropped row.
- Empty input: no output, no error.
- PlanOutcome fields populated: content_fetch_stats, drop lists.
- Backward-compat: _cell_gate_drop / _dispatch_row still importable from core.py.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from backlink_publisher.cli.plan_backlinks._engine import (
    PlanOutcome,
    _cell_gate_drop,
    plan_rows,
)
from backlink_publisher.config import Config


# ── fixtures ───────────────────────────────────────────────────────────────────


def _make_seed_row(
    platform: str = "medium",
    main_domain: str = "https://51acgs.com",
    language: str = "en",
) -> dict:
    """Minimal valid seed row for plan-backlinks input."""
    return {
        "platform": platform,
        "language": language,
        "url_mode": "A",
        "publish_mode": "draft",
        "target_url": f"{main_domain}/article-1",
        "main_domain": main_domain,
    }


def _stub_config(cell_assignments: dict | None = None) -> Config:
    """Return a minimal Config-like object that satisfies plan_rows."""
    cfg = MagicMock(spec=Config)
    cfg.cell_assignments = cell_assignments or {}
    cfg.llm_anchor_provider = None
    cfg.image_gen = None
    cfg.anchor_proportions = MagicMock()
    cfg.anchor_alarm = MagicMock()
    return cfg


# ── _cell_gate_drop (pure function, no config needed) ─────────────────────────


def test_cell_gate_drop_enrolled_wrong_platform():
    cells = {"https://example.com": ["medium", "blogger"]}
    assert _cell_gate_drop("https://example.com", "velog", cells) is True


def test_cell_gate_drop_enrolled_correct_platform():
    cells = {"https://example.com": ["medium", "blogger"]}
    assert _cell_gate_drop("https://example.com", "medium", cells) is False


def test_cell_gate_drop_unenrolled_site():
    cells = {"https://other.com": ["medium"]}
    assert _cell_gate_drop("https://example.com", "velog", cells) is False


def test_cell_gate_drop_empty_assignments():
    assert _cell_gate_drop("https://example.com", "velog", {}) is False


def test_cell_gate_drop_normalises_trailing_slash():
    cells = {"https://example.com": ["medium"]}
    assert _cell_gate_drop("https://example.com/", "velog", cells) is True
    assert _cell_gate_drop("https://example.com/", "medium", cells) is False


# ── backward-compat: re-exports from core.py ──────────────────────────────────


def test_cell_gate_drop_importable_from_core():
    from backlink_publisher.cli.plan_backlinks.core import _cell_gate_drop as f
    assert callable(f)


def test_dispatch_row_importable_from_core():
    from backlink_publisher.cli.plan_backlinks.core import _dispatch_row
    assert callable(_dispatch_row)


def test_plan_rows_importable_from_core():
    from backlink_publisher.cli.plan_backlinks.core import plan_rows as pr
    assert callable(pr)


# ── plan_rows: PlanOutcome structure ─────────────────────────────────────────


def test_empty_input_returns_empty_outcome():
    """Empty rows → PlanOutcome with no outputs, no errors."""
    cfg = _stub_config()
    outcome = plan_rows([], cfg, fetch_verify_enabled=False)
    assert isinstance(outcome, PlanOutcome)
    assert outcome.outputs == []
    assert outcome.errors == []
    assert outcome.validation_drops == []
    assert outcome.content_fetch_stats == {} or isinstance(
        outcome.content_fetch_stats, dict
    )


def test_invalid_seed_row_collected_not_raised():
    """Malformed row (missing required fields) → error collected, no exception."""
    cfg = _stub_config()
    bad_row = {"platform": "medium"}  # missing main_domain, target_url etc.
    outcome = plan_rows([bad_row], cfg, fetch_verify_enabled=False)
    # Should not raise; errors list is non-empty
    assert outcome.errors
    assert any("line 1" in e for e in outcome.errors)
    assert 1 in outcome.validation_drops


def test_cell_gate_drops_enrolled_wrong_platform():
    """Enrolled site + wrong platform → cell_gate_drops, not outputs."""
    cells = {"https://51acgs.com": ["blogger"]}
    cfg = _stub_config(cell_assignments=cells)
    row = _make_seed_row(platform="medium", main_domain="https://51acgs.com")

    with patch(
        "backlink_publisher.cli.plan_backlinks._engine._dispatch_row"
    ) as mock_dispatch:
        mock_dispatch.return_value = iter([])
        outcome = plan_rows([row], cfg, fetch_verify_enabled=False)

    # _dispatch_row should NOT be called — the cell gate fires first.
    mock_dispatch.assert_not_called()
    assert outcome.outputs == []
    assert 1 in outcome.cell_gate_drops
    assert outcome.errors == []


def test_cell_gate_passes_unenrolled_site():
    """Unenrolled site → passes cell gate; dispatch is attempted."""
    cells = {"https://other.com": ["blogger"]}
    cfg = _stub_config(cell_assignments=cells)
    row = _make_seed_row(platform="medium", main_domain="https://51acgs.com")

    payload = {
        "id": "t1", "platform": "medium", "main_domain": "https://51acgs.com",
        "metadata": {},
    }
    with (
        patch(
            "backlink_publisher.cli.plan_backlinks._engine._dispatch_row",
            return_value=iter([payload]),
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine.get_anchor_pool_v2",
            return_value=[],
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine.dofollow_tier_metadata",
            return_value={},
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine._build_banner_runtime",
            return_value=None,
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine.compute_config_sha",
            return_value="deadbeef",
        ),
    ):
        outcome = plan_rows([row], cfg, fetch_verify_enabled=False)

    assert len(outcome.outputs) == 1
    assert outcome.outputs[0]["id"] == "t1"
    assert outcome.cell_gate_drops == []


def test_generation_error_collected_not_raised():
    """_dispatch_row raising a generic exception → collected into errors."""
    cfg = _stub_config()
    row = _make_seed_row()

    with (
        patch(
            "backlink_publisher.cli.plan_backlinks._engine._dispatch_row",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine._build_banner_runtime",
            return_value=None,
        ),
        patch(
            "backlink_publisher.cli.plan_backlinks._engine.compute_config_sha",
            return_value="abc",
        ),
    ):
        outcome = plan_rows([row], cfg, fetch_verify_enabled=False)

    assert outcome.outputs == []
    assert outcome.errors
    assert any("generation error" in e for e in outcome.errors)
    assert 1 in outcome.generation_drops


def test_content_fetch_stats_returned():
    """outcome.content_fetch_stats is a dict (may be zeroed or have counts)."""
    cfg = _stub_config()
    outcome = plan_rows([], cfg, fetch_verify_enabled=False)
    assert isinstance(outcome.content_fetch_stats, dict)


def test_outcome_does_not_write_to_stdout(capsys):
    """Engine MUST NOT write to sys.stdout (H3)."""
    cfg = _stub_config()
    plan_rows([], cfg, fetch_verify_enabled=False)
    captured = capsys.readouterr()
    assert captured.out == ""


# ── PipelineAPI.plan in-process path ──────────────────────────────────────────


def test_pipeline_api_plan_returns_pipe_result():
    """PipelineAPI.plan() should return a PipeResult (not raise)."""
    from webui_app.api.pipeline_api import PipelineAPI, PipeResult

    api = PipelineAPI()
    # Empty seed JSONL → should succeed or fail gracefully (no SystemExit).
    result = api.plan("")
    assert isinstance(result, PipeResult)


def test_pipeline_api_plan_config_load_failure_returns_error():
    """Config load failure → PipeResult with error, not exception."""
    from webui_app.api.pipeline_api import PipelineAPI

    api = PipelineAPI()
    with patch(
        "backlink_publisher.cli.plan_backlinks._engine.plan_rows"
    ):
        with patch(
            "backlink_publisher.config.load_config",
            side_effect=RuntimeError("config missing"),
        ):
            result = api.plan("{}")
    assert not result.success
    assert result.error_class == "InputValidationError"
    assert "config" in (result.error or "").lower()
