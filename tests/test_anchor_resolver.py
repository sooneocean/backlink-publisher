"""Tests for backlink_publisher.anchor_resolver."""

from __future__ import annotations

import random
from unittest.mock import MagicMock

import pytest

from backlink_publisher.adapters.llm_anchor_provider import LLMAnchorRequest
from backlink_publisher.anchor_resolver import (
    FORBIDDEN_ANCHOR_TEXTS,
    _passes_filters,
    resolve_anchor,
)
from backlink_publisher.config import Config
from backlink_publisher.errors import DependencyError


# ── fixtures ────────────────────────────────────────────────────────────────


def _config_with_pool(pool: dict[str, dict[str, list[str]]]) -> Config:
    """Build a minimal Config with pools v2 pre-populated for one site."""
    return Config(
        target_anchor_pools_v2={
            "https://51acgs.com": pool,
        },
    )


def _fixed_rng(seed: int = 0) -> random.Random:
    return random.Random(seed)


# ── _passes_filters ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("热门漫画", True),       # 4 CJK / 4 total = 100% CJK
    ("本周热门漫画", True),   # 6 CJK / 6 total = 100% CJK
    ("漫画ACG", False),       # 5 chars, 2 CJK = 40% — below 50% threshold
    ("漫画AC", True),         # 4 chars, 2 CJK = 50% — exactly at threshold
    ("51", False),            # 2 chars, 0 CJK = 0% — below threshold
])
def test_passes_filters_boundary_examples(text, expected):
    assert _passes_filters(text) is expected


def test_passes_filters_rejects_too_short():
    assert _passes_filters("热") is False  # 1 char


def test_passes_filters_rejects_too_long():
    assert _passes_filters("热门漫画推荐网站点") is False  # 9 chars


def test_passes_filters_rejects_forbidden_text():
    for word in FORBIDDEN_ANCHOR_TEXTS:
        assert _passes_filters(word) is False


def test_passes_filters_rejects_html_brackets():
    assert _passes_filters("<漫画>") is False
    assert _passes_filters("漫画[1]") is False


def test_passes_filters_rejects_script_injection():
    assert _passes_filters("<scrip") is False  # would expand to <script>
    # A 6-char "script" with brackets — every < > and bracket char is blocked
    assert _passes_filters("<漫画>x") is False


def test_passes_filters_rejects_bidi_override():
    # U+202E RLO followed by Chinese — predominantly CJK but contains bidi attack char
    assert _passes_filters("‮漫画") is False


def test_passes_filters_rejects_control_chars():
    assert _passes_filters("漫画\x00") is False
    assert _passes_filters("漫画\n推荐") is False


def test_passes_filters_rejects_mostly_english():
    # 6 chars total, only 1 CJK = 16% < 50%
    assert _passes_filters("ACGxx画") is False


def test_passes_filters_rejects_non_string():
    assert _passes_filters(None) is False  # type: ignore[arg-type]
    assert _passes_filters(123) is False  # type: ignore[arg-type]


def test_passes_filters_rejects_empty():
    assert _passes_filters("") is False


# ── resolve_anchor: typed pool path ────────────────────────────────────────


def test_resolves_from_typed_pool():
    config = _config_with_pool({
        "home": {"branded": ["51漫画首页", "51漫画", "51漫画平台"]},
    })
    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="成人漫画",
        target_url="https://51acgs.com/",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=None,
        rng=_fixed_rng(),
    )
    assert result in {"51漫画首页", "51漫画", "51漫画平台"}


def test_pool_dedup_against_recent_texts():
    config = _config_with_pool({
        "home": {"branded": ["a头条", "b头条", "c头条"]},
    })
    # All but c头条 are "recent"
    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=["a头条", "b头条"],
        provider=None,
        rng=_fixed_rng(),
    )
    assert result == "c头条"


def test_pool_exhausted_returns_none_without_provider():
    config = _config_with_pool({
        "home": {"branded": ["热门漫画", "本周漫画"]},
    })
    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=["热门漫画", "本周漫画"],
        provider=None,
    )
    assert result is None


def test_pool_filter_rejects_bad_entries_then_picks_good_one():
    """Pool contains entries that fail _passes_filters; they get skipped."""
    config = _config_with_pool({
        "home": {"branded": ["点击这里", "更多", "好的品牌"]},
    })
    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=None,
    )
    assert result == "好的品牌"


# ── resolve_anchor: LLM fallback path ──────────────────────────────────────


def test_empty_pool_falls_through_to_llm():
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.return_value = ["热门漫画", "本周热门"]

    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="成人漫画",
        target_url="https://51acgs.com/comic/hot",
        url_subject="热门",
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=provider,
    )
    assert result == "热门漫画"
    # Provider received the proper request object
    assert provider.generate_candidates.call_count == 1
    req: LLMAnchorRequest = provider.generate_candidates.call_args[0][0]
    assert req.url_category == "hot"
    assert req.anchor_type == "exact"
    assert req.keyword == "成人漫画"


def test_missing_pool_entry_falls_through_to_llm():
    """When the (url_category, anchor_type) cell isn't even in config, use LLM."""
    config = _config_with_pool({})  # no entry at all
    provider = MagicMock()
    provider.generate_candidates.return_value = ["热门漫画"]

    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=provider,
    )
    assert result == "热门漫画"


def test_llm_candidates_all_forbidden_returns_none():
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.return_value = list(FORBIDDEN_ANCHOR_TEXTS)

    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=provider,
    )
    assert result is None


def test_llm_candidates_all_too_long_returns_none():
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.return_value = [
        "热门漫画排行榜单",  # 8 chars OK
        "热门漫画排行榜单榜",  # 9 chars too long
    ]
    # Force the 8-char one to also be filtered by adding it to recent_texts
    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=["热门漫画排行榜单"],
        provider=provider,
    )
    assert result is None


def test_llm_candidates_with_unsafe_chars_are_filtered():
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.return_value = [
        "<script>",
        "‮漫画",          # bidi
        "热门\n漫画",     # control char
        "热门漫画",       # this one survives
    ]

    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=provider,
    )
    assert result == "热门漫画"


def test_llm_candidates_all_in_recent_texts_returns_none():
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.return_value = ["热门漫画", "本周热门"]

    result = resolve_anchor(
        url_category="hot",
        anchor_type="exact",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=["热门漫画", "本周热门"],
        provider=provider,
    )
    assert result is None


def test_provider_dependency_error_bubbles_up():
    """LLM errors must NOT be swallowed — Unit 8 needs to see them for retry."""
    config = _config_with_pool({"hot": {"exact": []}})
    provider = MagicMock()
    provider.generate_candidates.side_effect = DependencyError("429 burst")

    with pytest.raises(DependencyError):
        resolve_anchor(
            url_category="hot",
            anchor_type="exact",
            keyword="x",
            target_url="x",
            url_subject=None,
            config=config,
            main_domain="https://51acgs.com",
            recent_texts=[],
            provider=provider,
        )


def test_pool_hit_skips_provider_entirely():
    """If the pool yields a candidate, the LLM must not be called at all."""
    config = _config_with_pool({
        "home": {"branded": ["51漫画首页"]},
    })
    provider = MagicMock()
    provider.generate_candidates.return_value = ["should not appear"]

    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com",
        recent_texts=[],
        provider=provider,
    )
    assert result == "51漫画首页"
    provider.generate_candidates.assert_not_called()


# ── domain key tolerance ────────────────────────────────────────────────────


def test_main_domain_with_trailing_slash_still_finds_pool():
    config = _config_with_pool({"home": {"branded": ["51漫画首页"]}})
    result = resolve_anchor(
        url_category="home",
        anchor_type="branded",
        keyword="x",
        target_url="x",
        url_subject=None,
        config=config,
        main_domain="https://51acgs.com/",  # trailing slash
        recent_texts=[],
        provider=None,
    )
    assert result == "51漫画首页"
