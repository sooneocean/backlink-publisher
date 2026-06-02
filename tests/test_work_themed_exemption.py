"""Tests for work_themed citability exemption (U8 R11).

work_themed payloads (any language with three_url config) receive ONLY zero-cost
levers: freshness + entity_claim. They must NOT receive FAQ or stats blocks.
The plan spec notes work_themed fires for ANY language, not zh-only.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from backlink_publisher.cli.plan_backlinks._citability import apply_zero_cost_levers
from backlink_publisher.cli.plan_backlinks._engine import _apply_zero_cost_and_emit


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_work_themed_payload(
    main_domain: str = "https://work-site.com",
    language: str = "en",
    content: str = "Work-themed article content.",
) -> dict:
    """Minimal work_themed-like payload."""
    return {
        "id": "wt001",
        "platform": "blogger",
        "language": language,
        "main_domain": main_domain + "/",
        "content_markdown": content,
        "links": [
            {"url": main_domain + "/", "anchor": "WorkSite", "kind": "main_domain"},
            {"url": main_domain + "/list", "anchor": "category list", "kind": "category"},
            {"url": main_domain + "/work/item1", "anchor": "Item 1", "kind": "target"},
        ],
    }


def _make_row(main_domain: str = "https://work-site.com", language: str = "en") -> dict:
    return {
        "target_url": main_domain + "/article",
        "main_domain": main_domain,
        "language": language,
        "platform": "blogger",
    }


# ── zero-cost levers applied ──────────────────────────────────────────────────


def test_work_themed_gets_freshness_and_entity_claim_en():
    today = datetime.date.today().isoformat()
    payload = _make_work_themed_payload(language="en")
    row = _make_row(language="en")
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    md = result["content_markdown"]
    assert today in md
    assert "work-site.com" in md


def test_work_themed_gets_freshness_zh():
    today = datetime.date.today().isoformat()
    payload = _make_work_themed_payload(language="zh-CN")
    row = _make_row(language="zh-CN")
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    md = result["content_markdown"]
    assert today in md
    assert "更新于" in md


def test_work_themed_gets_freshness_ko():
    payload = _make_work_themed_payload(language="ko")
    row = _make_row(language="ko")
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    md = result["content_markdown"]
    assert "업데이트" in md


def test_work_themed_gets_freshness_ru():
    payload = _make_work_themed_payload(language="ru")
    row = _make_row(language="ru")
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    md = result["content_markdown"]
    assert "актуален" in md


# ── thick levers are excluded ─────────────────────────────────────────────────


def test_work_themed_no_faq_block():
    for lang in ("en", "zh-CN", "ko", "ru"):
        payload = _make_work_themed_payload(language=lang)
        row = _make_row(language=lang)
        result = _apply_zero_cost_and_emit(payload, row, "work_themed")
        md = result["content_markdown"]
        assert "FAQ" not in md
        assert "常见问题" not in md
        assert "자주 묻는 질문" not in md
        assert "Часто задаваемые вопросы" not in md


def test_work_themed_no_stat_block():
    payload = _make_work_themed_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")
    md = result["content_markdown"]
    assert "By the numbers" not in md
    assert "数据参考" not in md


def test_work_themed_levers_are_only_zero_cost():
    payload = _make_work_themed_payload()
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    levers = result["_citability_levers"]
    assert set(levers) == {"entity_claim", "freshness"}
    assert len(levers) == 2


# ── link count unchanged ──────────────────────────────────────────────────────


def test_work_themed_link_count_unchanged():
    """Zero-cost levers MUST NOT add new links (only text)."""
    payload = _make_work_themed_payload()
    original_links = list(payload["links"])
    row = _make_row()
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    assert result["links"] == original_links


# ── any language fires for work_themed ───────────────────────────────────────


@pytest.mark.parametrize("language", ["en", "zh-CN", "ko", "ru"])
def test_work_themed_fires_for_any_language(language: str):
    """work_themed is not zh-only — all language payloads get zero-cost levers."""
    payload = _make_work_themed_payload(language=language)
    row = _make_row(language=language)
    result = _apply_zero_cost_and_emit(payload, row, "work_themed")

    levers = result.get("_citability_levers", [])
    assert "freshness" in levers
    assert "entity_claim" in levers


# ── metadata: applied_levers recorded in payload ──────────────────────────────


def test_work_themed_levers_in_metadata_via_plan_rows():
    """plan_rows moves _citability_levers from payload into metadata.citability_levers."""
    from unittest.mock import MagicMock
    from backlink_publisher.cli.plan_backlinks._engine import plan_rows
    from backlink_publisher.config import Config

    row = {
        "target_url": "https://work-site.com/article",
        "main_domain": "https://work-site.com",
        "language": "en",
        "platform": "blogger",
        "url_mode": "A",
        "publish_mode": "draft",
    }

    # Stub a payload with _citability_levers already set (as _dispatch_row would)
    stub_payload = {
        "id": "wt-meta",
        "platform": "blogger",
        "main_domain": "https://work-site.com/",
        "metadata": {},
        "_citability_levers": ["entity_claim", "freshness"],
    }

    cfg = MagicMock(spec=Config)
    cfg.cell_assignments = {}
    cfg.llm_anchor_provider = None
    cfg.image_gen = None

    with (
        patch(
            "backlink_publisher.cli.plan_backlinks._engine._dispatch_row",
            return_value=iter([stub_payload]),
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
            return_value="abc",
        ),
    ):
        outcome = plan_rows([row], cfg, fetch_verify_enabled=False)

    assert len(outcome.outputs) == 1
    out = outcome.outputs[0]
    # _citability_levers must be moved into metadata
    assert "_citability_levers" not in out
    assert "citability_levers" in out["metadata"]
    assert set(out["metadata"]["citability_levers"]) == {"entity_claim", "freshness"}
