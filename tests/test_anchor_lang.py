"""Tests for anchor_lang.check_anchor_language (Unit 2 of plan 2026-05-14-001).

Covers the R4 codepoint heuristic + branded-pool exemption + kind-scoping +
non-enum language behavior.
"""

from __future__ import annotations

import pytest

from backlink_publisher.anchor_lang import check_anchor_language


# --- zh-CN: happy path & branded carve-out ---


def test_zh_cn_main_domain_pass_with_cjk() -> None:
    ok, reason = check_anchor_language("苹果官网", "zh-CN", "main_domain", [])
    assert ok is True
    assert reason is None


def test_zh_cn_main_domain_branded_latin_passes_via_pool() -> None:
    ok, reason = check_anchor_language("Apple", "zh-CN", "main_domain", ["Apple"])
    assert ok is True
    assert reason is None


def test_zh_cn_main_domain_unbranded_latin_fails() -> None:
    ok, reason = check_anchor_language("Apple", "zh-CN", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing CJK codepoint"


def test_zh_cn_main_domain_generic_english_fails() -> None:
    ok, reason = check_anchor_language("learn more", "zh-CN", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing CJK codepoint"


def test_zh_cn_target_kind_subject_to_rule() -> None:
    """target is in _GATED_KINDS alongside main_domain."""
    ok, _ = check_anchor_language("Apple", "zh-CN", "target", [])
    assert ok is False


# --- kind exemption: supporting/extra/category/detail ---


@pytest.mark.parametrize("kind", ["supporting", "extra", "category", "detail"])
def test_kind_exempt_supporting_etc(kind: str) -> None:
    """Auxiliary citations (Wiki, MDN) in zh-CN articles must pass."""
    ok, reason = check_anchor_language("MDN", "zh-CN", kind, [])
    assert ok is True
    assert reason is None


# --- ru ---


def test_ru_main_domain_pass_with_cyrillic() -> None:
    ok, _ = check_anchor_language("Главная страница", "ru", "main_domain", [])
    assert ok is True


def test_ru_main_domain_latin_only_fails() -> None:
    ok, reason = check_anchor_language("home page", "ru", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing Cyrillic codepoint"


def test_ru_branded_latin_passes() -> None:
    ok, _ = check_anchor_language("Yandex", "ru", "main_domain", ["Yandex"])
    assert ok is True


# --- en: strict (any-Latin AND none-of CJK/Cyrillic) ---


def test_en_main_domain_pass_with_latin_only() -> None:
    ok, _ = check_anchor_language("Apple Store", "en", "main_domain", [])
    assert ok is True


def test_en_main_domain_punctuation_and_digits_allowed() -> None:
    ok, _ = check_anchor_language("Apple — Inc. iPhone 15", "en", "main_domain", [])
    assert ok is True


def test_en_main_domain_mixed_script_with_cjk_fails() -> None:
    """Mixed-script English anchors fail strict-en. Use branded_pool exemption."""
    ok, reason = check_anchor_language("在线 Apple 体验店", "en", "main_domain", [])
    assert ok is False
    assert reason == "en anchor contains CJK codepoint"


def test_en_main_domain_mixed_script_with_cyrillic_fails() -> None:
    ok, reason = check_anchor_language("Apple магазин", "en", "main_domain", [])
    assert ok is False
    assert reason == "en anchor contains Cyrillic codepoint"


def test_en_main_domain_no_latin_letter_fails() -> None:
    ok, reason = check_anchor_language("12345", "en", "main_domain", [])
    assert ok is False
    assert reason == "anchor missing Latin letter"


def test_en_branded_mixed_script_passes() -> None:
    """branded_pool exemption applies regardless of language."""
    ok, _ = check_anchor_language("在线 Apple", "en", "main_domain", ["在线 Apple"])
    assert ok is True


# --- empty anchor ---


def test_empty_anchor_fails_main_domain() -> None:
    ok, _ = check_anchor_language("", "zh-CN", "main_domain", [])
    assert ok is False


def test_empty_anchor_exempt_when_kind_exempt() -> None:
    ok, _ = check_anchor_language("", "zh-CN", "supporting", [])
    assert ok is True


# --- BMP boundary: Extension A NOT counted ---


def test_zh_cn_bmp_only_extension_a_not_counted() -> None:
    """U+3400 (Ext A) is OUT of the BMP block we check; must fail."""
    ok, _ = check_anchor_language("㐀", "zh-CN", "main_domain", [])
    assert ok is False


def test_zh_cn_extension_b_not_counted() -> None:
    """U+20000 (Ext B) is also out."""
    ok, _ = check_anchor_language("\U00020000", "zh-CN", "main_domain", [])
    assert ok is False


# --- non-enum row_language ---


def test_japanese_row_language_exempt_with_no_check() -> None:
    """row.language outside SUPPORTED_LANGUAGES is exempt (R3 contract)."""
    ok, reason = check_anchor_language("Tokyo", "ja", "main_domain", [])
    assert ok is True
    assert reason is None


def test_german_row_language_exempt() -> None:
    ok, _ = check_anchor_language("Berlin", "de", "main_domain", [])
    assert ok is True


def test_unknown_row_language_exempt() -> None:
    ok, _ = check_anchor_language("anything", "unknown", "main_domain", [])
    assert ok is True


# --- Plan 2026-05-18-006 Unit 3: ko anchor codepoint rule -------------------


class TestKoAnchorRule:
    """ko-strict-mirror-of-en: ≥1 Hangul Syllable, no CJK, no Cyrillic."""

    # Happy path

    def test_pure_hangul_passes(self) -> None:
        ok, reason = check_anchor_language("자세히 보기", "ko", "main_domain", [])
        assert ok is True
        assert reason is None

    def test_short_hangul_passes(self) -> None:
        ok, reason = check_anchor_language("한국어 학습", "ko", "target", [])
        assert ok is True
        assert reason is None

    def test_hangul_with_latin_brand_passes(self) -> None:
        """Real ko corpora frequently mix Latin brand names — must pass
        (≥1 Hangul + no CJK/Cyrillic, Latin allowed)."""
        ok, reason = check_anchor_language(
            "Apple 한국 출시", "ko", "main_domain", []
        )
        assert ok is True
        assert reason is None

    def test_hangul_with_digits_passes(self) -> None:
        ok, reason = check_anchor_language("iPhone 15 리뷰", "ko", "target", [])
        assert ok is True
        assert reason is None

    def test_hangul_with_punctuation_passes(self) -> None:
        ok, reason = check_anchor_language("자세히 보기 →", "ko", "main_domain", [])
        assert ok is True
        assert reason is None

    # Reject cases

    def test_no_hangul_fails_with_clear_reason(self) -> None:
        ok, reason = check_anchor_language("learn more", "ko", "main_domain", [])
        assert ok is False
        assert reason == "anchor missing Hangul codepoint"

    def test_pure_cjk_fails(self) -> None:
        """A Hanja proper noun without any Hangul fails the gate."""
        ok, reason = check_anchor_language("金正恩", "ko", "main_domain", [])
        assert ok is False
        assert reason == "anchor missing Hangul codepoint"

    def test_pure_cyrillic_fails(self) -> None:
        ok, reason = check_anchor_language("Привет", "ko", "main_domain", [])
        assert ok is False
        assert reason == "anchor missing Hangul codepoint"

    def test_hangul_with_hanja_fails_strict(self) -> None:
        """Strict rule: mixed Hangul + Hanja (CJK) is rejected by R7. Real
        ko publications mix Hanja for proper nouns — operators must add
        such anchors to branded_pool (next test verifies the carve-out)."""
        ok, reason = check_anchor_language(
            "金正恩 인터뷰", "ko", "main_domain", []
        )
        assert ok is False
        assert reason == "ko anchor contains CJK codepoint"

    def test_hangul_with_cyrillic_fails_strict(self) -> None:
        ok, reason = check_anchor_language(
            "Привет 안녕", "ko", "main_domain", []
        )
        assert ok is False
        assert reason == "ko anchor contains Cyrillic codepoint"

    def test_empty_anchor_fails(self) -> None:
        ok, reason = check_anchor_language("", "ko", "main_domain", [])
        assert ok is False
        assert reason == "anchor missing Hangul codepoint"

    # Branded pool carve-out (Hanja proper nouns)

    def test_hangul_hanja_anchor_in_branded_pool_passes(self) -> None:
        """Hanja-mixed ko anchor passes when it's in branded_pool — the
        operator-supplied list of proper nouns / branded mentions that
        legitimately use mixed-script in ko publications."""
        ok, reason = check_anchor_language(
            "金正恩 인터뷰", "ko", "main_domain", ["金正恩 인터뷰"]
        )
        assert ok is True
        assert reason is None

    def test_pure_hanja_anchor_in_branded_pool_passes(self) -> None:
        """Pure-Hanja branded anchors (e.g., 首爾) also pass via branded_pool."""
        ok, reason = check_anchor_language(
            "首爾", "ko", "target", ["首爾", "釜山"]
        )
        assert ok is True
        assert reason is None

    # Kind-scoping (R4 exemption order step 1)

    def test_ko_supporting_kind_exempt_from_codepoint_check(self) -> None:
        """Auxiliary citations (kind != main_domain/target) are exempt
        from any codepoint check — same as zh-CN/ru/en."""
        ok, reason = check_anchor_language(
            "Wikipedia", "ko", "supporting", []
        )
        assert ok is True
        assert reason is None

    # Hangul Jamo (out-of-v1 deferral verification)

    def test_pure_jamo_fails_per_v1_scope(self) -> None:
        """Hangul Jamo (U+1100..U+11FF) is explicitly out of v1 — only
        Hangul Syllables (U+AC00..U+D7AF) count. Real ko text rarely uses
        standalone Jamo; widen on first false-negative."""
        # U+1100 = ㄱ (Hangul Choseong Kiyeok)
        ok, _ = check_anchor_language("ᄀᄀ", "ko", "main_domain", [])
        assert ok is False

    # Existing language tests still pass (regression lock)

    def test_existing_zh_cn_anchor_path_unchanged(self) -> None:
        """Adding ko must not affect zh-CN routing."""
        ok, reason = check_anchor_language("苹果官网", "zh-CN", "main_domain", [])
        assert ok is True
        assert reason is None

    def test_existing_en_anchor_path_unchanged(self) -> None:
        """Adding ko must not affect en routing."""
        ok, reason = check_anchor_language("Apple Store", "en", "main_domain", [])
        assert ok is True
        assert reason is None
