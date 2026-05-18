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


# ── Plan 2026-05-18-006 Unit 4: language-aware _passes_filters dispatch ─────


from backlink_publisher.anchor_resolver import (  # noqa: E402
    _RATIO_RULES,
    _formal_denominator,
)


class TestPassesFiltersZhCnBitExact:
    """Plan 2026-05-18-006 Unit 4 R13: zh-CN behavior bit-exact preserved.
    Every existing single-arg call site (markdown_utils.validate_zh_short_payload,
    plan_backlinks._build_branded_clean) defaults to language="zh-CN" and
    must produce identical outputs."""

    def test_default_arg_is_zh_cn(self) -> None:
        assert _passes_filters("苹果官网") is True
        assert _passes_filters("苹果官网", "zh-CN") is True

    def test_zh_cn_cjk_ratio_failure_below_50_percent(self) -> None:
        # "Apple网" — 6 chars, 1 CJK = 16% < 50% → fail
        assert _passes_filters("Apple网", "zh-CN") is False
        assert _passes_filters("Apple网") is False

    def test_zh_cn_pure_latin_fails(self) -> None:
        assert _passes_filters("Apple", "zh-CN") is False

    def test_zh_cn_pure_cjk_passes(self) -> None:
        assert _passes_filters("苹果", "zh-CN") is True

    def test_zh_cn_length_cap_unchanged(self) -> None:
        assert _passes_filters("一", "zh-CN") is False  # 1 < 2
        assert _passes_filters("一二三四五六七八九", "zh-CN") is False  # 9 > 8

    def test_zh_cn_forbidden_unchanged(self) -> None:
        assert _passes_filters("点击这里", "zh-CN") is False

    def test_zh_cn_unsafe_chars_unchanged(self) -> None:
        assert _passes_filters("苹果<a>", "zh-CN") is False


class TestPassesFiltersLanguageNormalization:
    """Language arg defensive normalization (R13 implementation contract)."""

    def test_whitespace_stripped(self) -> None:
        assert _passes_filters("苹果", " zh-CN ") is True

    def test_none_language_treated_as_zh_cn(self) -> None:
        assert _passes_filters("苹果", None) is True  # type: ignore[arg-type]

    def test_empty_string_language_treated_as_zh_cn(self) -> None:
        assert _passes_filters("苹果", "") is True


class TestPassesFiltersKoBranch:
    """ko-branch dispatch (preparatory only — no production call site in v1).

    Threshold 0.30 over R5 formal denominator (L+M categories), with NFC
    normalization at entry to handle macOS NFD-decomposed input.
    """

    def test_pure_hangul_short_anchor_passes(self) -> None:
        # "안녕" = 2 Hangul over denom=2 → ratio 1.0 ≥ 0.30
        assert _passes_filters("안녕", "ko") is True

    def test_hangul_majority_passes(self) -> None:
        # "안녕하세요" — 5 Hangul, denom=5 → ratio 1.0 ≥ 0.30 → pass
        assert _passes_filters("안녕하세요", "ko") is True

    def test_latin_dominant_fails_ko_ratio(self) -> None:
        # "Apple안" — len=6 ok, hangul=1, denom=6, ratio=1/6≈0.17 < 0.30 → fail
        assert _passes_filters("Apple안", "ko") is False

    def test_no_hangul_fails(self) -> None:
        # "Apple" — hangul=0 → fail
        assert _passes_filters("Apple", "ko") is False

    def test_pure_cjk_fails_ko_ratio(self) -> None:
        # "苹果" — Hangul count 0 → 0/2=0 < 0.30 → fail
        assert _passes_filters("苹果", "ko") is False

    def test_length_cap_rejects_long_ko_anchor(self) -> None:
        """zh-CN-tuned length cap (2-8) acknowledged as zh-CN-tuned; will be
        revisited alongside ko-localized templates. v1: ko anchors > 8 chars
        fail at length cap regardless of Hangul content."""
        # "Apple 한국 출시" — 11 chars > 8
        assert _passes_filters("Apple 한국 출시", "ko") is False

    def test_ko_short_anchor_passes(self) -> None:
        # "자세히" — 3 chars, all Hangul → ratio=1.0 → pass
        assert _passes_filters("자세히", "ko") is True

    def test_ko_forbidden_anchor_still_fails(self) -> None:
        # FORBIDDEN check applies across all languages
        assert _passes_filters("更多", "ko") is False

    def test_ko_unsafe_chars_still_fail(self) -> None:
        # Unsafe char check applies across all languages
        assert _passes_filters("안녕<a>", "ko") is False


class TestPassesFiltersKoNfcNormalization:
    """ko ratio check NFC-normalizes input — guards against macOS-NFD
    inputs that split Hangul Syllables into Jamo outside U+AC00..U+D7AF."""

    def test_nfc_normalized_hangul_passes(self) -> None:
        # 가 = U+AC00 (single codepoint, NFC-composed)
        assert _passes_filters("가나다", "ko") is True

    def test_nfd_decomposed_hangul_recomposed_passes(self) -> None:
        """NFD: 가 → ㄱ + ㅏ (two Jamo codepoints in U+1100..U+11FF, NOT in
        the Syllables block). After NFC normalize at ratio-check entry,
        recomposed to single Syllable → passes."""
        import unicodedata
        nfd = unicodedata.normalize("NFD", "가나다")
        # Verify the NFD form really lacks Syllable codepoints
        nfd_syllable_count = sum(
            1 for c in nfd if 0xAC00 <= ord(c) <= 0xD7AF
        )
        assert nfd_syllable_count == 0
        # The function should recompose to NFC and accept
        assert _passes_filters(nfd, "ko") is True


class TestPassesFiltersOtherLanguages:
    """Languages NOT in _RATIO_RULES (ru/en in v1) skip the ratio check.
    Baseline checks (length, FORBIDDEN, unsafe) still apply."""

    def test_ru_skips_ratio_check_pass(self) -> None:
        assert _passes_filters("Привет", "ru") is True

    def test_ru_length_cap_still_applies(self) -> None:
        assert _passes_filters("П", "ru") is False  # 1 < 2

    def test_en_skips_ratio_check_pass(self) -> None:
        assert _passes_filters("Apple", "en") is True

    def test_en_forbidden_still_applies(self) -> None:
        # FORBIDDEN_ANCHOR_TEXTS is zh-CN-content but the check is
        # language-agnostic; en anchor matching FORBIDDEN still fails.
        assert _passes_filters("点击这里", "en") is False

    def test_unsupported_language_skips_ratio_check(self) -> None:
        # ja not in _RATIO_RULES → ratio check skipped, baseline ok
        assert _passes_filters("こんにち", "ja") is True


class TestRatioRulesShape:
    """_RATIO_RULES registry shape — mirrors anchor_lang._LANGUAGE_RULES."""

    def test_v1_has_only_zh_cn_and_ko(self) -> None:
        assert set(_RATIO_RULES.keys()) == {"zh-CN", "ko"}

    def test_zh_cn_rule_is_callable(self) -> None:
        zh_rule = _RATIO_RULES["zh-CN"]
        assert callable(zh_rule)
        assert zh_rule("苹果") is True

    def test_ko_rule_is_callable(self) -> None:
        ko_rule = _RATIO_RULES["ko"]
        assert callable(ko_rule)
        assert ko_rule("안녕") is True


class TestFormalDenominator:
    """Formal denominator helper (shared with R5 codepoint short-circuit
    in linkcheck.language)."""

    def test_letters_counted(self) -> None:
        assert _formal_denominator("hello") == 5

    def test_hangul_counted(self) -> None:
        assert _formal_denominator("안녕하세요") == 5

    def test_cjk_counted(self) -> None:
        assert _formal_denominator("苹果") == 2

    def test_digits_excluded(self) -> None:
        assert _formal_denominator("hello123") == 5

    def test_whitespace_excluded(self) -> None:
        assert _formal_denominator("hello world") == 10

    def test_punctuation_excluded(self) -> None:
        assert _formal_denominator("hello, world!") == 10

    def test_empty_string_zero(self) -> None:
        assert _formal_denominator("") == 0

    def test_punctuation_only_zero(self) -> None:
        assert _formal_denominator("!@#$%") == 0


class TestResolveAnchorLanguageKwarg:
    """resolve_anchor accepts language kwarg and forwards to _passes_filters."""

    def test_default_language_is_zh_cn(self) -> None:
        """Calls without language kwarg default to zh-CN — bit-exact for
        existing call sites."""
        config = _config_with_pool({"home": {"branded": ["苹果"]}})
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
        assert result == "苹果"

    def test_language_kwarg_filters_ko_pool(self) -> None:
        """resolve_anchor with language='ko' applies ko ratio rule. zh-CN
        candidate in the pool is filtered out (0 Hangul); ko candidate passes."""
        config = _config_with_pool({"home": {"branded": ["안녕", "苹果"]}})
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
            language="ko",
        )
        # "苹果" fails ko (0 Hangul); "안녕" passes (ratio 1.0)
        assert result == "안녕"
