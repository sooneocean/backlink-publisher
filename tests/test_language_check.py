"""Tests for language_check.detect_language and language_matches.

Plan reference: docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md
Unit 1 — R1 (language_matches bug fix) and R3 (unknown handling).
"""

from __future__ import annotations

import pytest

from backlink_publisher.language_check import (
    SUPPORTED_LANGUAGES,
    detect_language,
    language_matches,
)


# --- SUPPORTED_LANGUAGES constant ---


def test_supported_languages_contains_canonical_four_languages() -> None:
    # Plan 2026-05-18-006 Unit 1: ko added. ja / zh-TW deferred to follow-up
    # brainstorms (require harder detection algorithms — Hiragana/Katakana for
    # ja, 繁简 lexicon for zh-TW — that this PR explicitly does not template).
    assert SUPPORTED_LANGUAGES == frozenset({"zh-CN", "ru", "en", "ko"})


# --- detect_language: happy paths ---


def test_detect_language_english_body() -> None:
    text = "This is a test article about https://example.com and some content here."
    assert detect_language(text) == "en"


def test_detect_language_chinese_body() -> None:
    text = "这是一个关于人工智能的文章，我们在这里讨论一些技术细节。"
    assert detect_language(text) == "zh-CN"


def test_detect_language_russian_body() -> None:
    text = "Это статья о машинном обучении, и мы обсуждаем здесь некоторые детали."
    assert detect_language(text) == "ru"


def test_detect_language_unknown_for_zero_score() -> None:
    # No EN/ZH/RU hints anywhere — code blocks or pure punctuation.
    text = "```\n    \n  ===\n```"
    assert detect_language(text) == "unknown"


# --- language_matches: R1 contract (post-fix) ---


@pytest.mark.parametrize("known", ["zh-CN", "ru", "en"])
def test_language_matches_self(known: str) -> None:
    assert language_matches(known, known) is True


def test_language_matches_mismatch_en_vs_zh() -> None:
    """R1: this was the bug — previously returned True; now must return False."""
    assert language_matches("en", "zh-CN") is False


def test_language_matches_mismatch_zh_vs_en() -> None:
    assert language_matches("zh-CN", "en") is False


def test_language_matches_mismatch_ru_vs_en() -> None:
    assert language_matches("ru", "en") is False


def test_language_matches_mismatch_en_vs_ru() -> None:
    assert language_matches("en", "ru") is False


def test_language_matches_mismatch_zh_vs_ru() -> None:
    assert language_matches("zh-CN", "ru") is False


def test_language_matches_mismatch_ru_vs_zh() -> None:
    assert language_matches("ru", "zh-CN") is False


# --- language_matches: R3 unknown handling ---


@pytest.mark.parametrize("requested", ["zh-CN", "ru", "en"])
def test_language_matches_unknown_detected_passes(requested: str) -> None:
    """detected='unknown' is the escape valve — caller can't disprove."""
    assert language_matches("unknown", requested) is True


@pytest.mark.parametrize("detected", ["zh-CN", "ru", "en"])
def test_language_matches_unknown_requested_passes(detected: str) -> None:
    """Symmetric: if requested itself is unknown we also allow through."""
    assert language_matches(detected, "unknown") is True


def test_language_matches_both_unknown() -> None:
    assert language_matches("unknown", "unknown") is True


# --- Noise stripping: URLs, HTML tags, markdown link syntax (regression
# for the work-themed-link-count fix that inflated en-score by appending
# Latin-anchor "Further reading" paragraphs to zh-CN articles)


def test_detect_zh_body_with_latin_urls_does_not_misclassify_as_en() -> None:
    """A zh-CN body whose only Latin content is URL strings + HTML anchor
    tags must still detect as zh-CN. Pre-fix the substring-based EN_HINTS
    counter inflated on "a"/"i"/"in"/"on" matches inside URLs + attributes.
    """
    text = (
        '在论坛上看到有人推荐 <a href="https://51acgs.com/animate/14529" '
        'target="_blank" rel="noopener">51漫畫</a>，自己跟着看了一阵子。\n\n'
        '<a href="https://51acgs.com/animate" target="_blank" rel="noopener">'
        '51acgs</a> 是日常会扫一眼的页面。'
    )
    assert detect_language(text) == "zh-CN"


def test_detect_zh_body_with_markdown_link_and_latin_anchors_stays_zh() -> None:
    """zh-CN body with an appended "延伸阅读" paragraph containing markdown
    links pointing at en.wikipedia.org / github.com etc must still detect
    as zh-CN. This is the exact shape the work-themed branch emits.
    """
    text = (
        "在论坛上看到有人推荐 51漫畫，自己跟着看了一阵子下来感觉不错。"
        "更新频率比较稳定，整理也算细致。\n\n"
        "延伸阅读：[Wikipedia](https://en.wikipedia.org), "
        "[MDN](https://developer.mozilla.org), "
        "[Stack Overflow](https://stackoverflow.com), "
        "[GitHub](https://github.com)。"
    )
    assert detect_language(text) == "zh-CN"


def test_detect_ru_body_with_latin_urls_stays_ru() -> None:
    """Symmetric: a Russian body with Latin URLs must still detect as ru."""
    text = (
        "Это статья о машинном обучении, мы обсуждаем здесь детали. "
        "<a href=\"https://github.com\">пример</a> и "
        "[Wikipedia](https://en.wikipedia.org) — внешние ссылки."
    )
    assert detect_language(text) == "ru"


def test_detect_en_body_unchanged_after_noise_strip() -> None:
    """Stripping URLs + HTML must not break English detection — the
    visible markdown anchor text + prose still carries the en signal."""
    text = (
        "This is an article about [GitHub](https://github.com) and "
        "<a href=\"https://example.com\">how</a> you can use it. "
        "The supporting links should not be the only signal."
    )
    assert detect_language(text) == "en"


def test_detect_pure_url_only_text_returns_unknown() -> None:
    """A body that is ENTIRELY noise (URLs + HTML, no prose) strips to
    empty and falls into the unknown branch — not silently mis-classified.
    """
    text = (
        '<a href="https://en.wikipedia.org">x</a> '
        '<a href="https://github.com">y</a>'
    )
    # After stripping HTML the visible text is "x y" — only 2 letter
    # codepoints, below the codepoint-short-circuit minimum-denom guard
    # (plan 2026-05-18-006 Unit 2: denom < 5 falls through to keyword
    # scoring), where neither ZH_HINTS nor RU_HINTS nor multi-char EN_HINTS
    # substrings match → all scores zero → unknown.
    assert detect_language(text) == "unknown"


# --- Plan 2026-05-18-006 Unit 2: ko language detection -----------------------


from backlink_publisher.language_check import (  # noqa: E402
    KO_HINTS,
    detect_language_from_html,
    detect_language_from_markdown,
)


class TestKoDetectionShortCircuit:
    """R5 codepoint-first short-circuit on Korean (Hangul Syllables) text."""

    def test_pure_hangul_short_circuits_to_ko(self) -> None:
        text = "안녕하세요 오늘은 한국어 기사를 작성합니다."
        assert detect_language_from_markdown(text) == "ko"

    def test_long_korean_article_is_ko(self) -> None:
        text = (
            "최근 인공지능 기술이 빠르게 발전하면서 우리 일상에 큰 변화를 "
            "가져오고 있습니다. 특히 자연어 처리 분야의 진보는 놀라운 수준입니다. "
            "오늘은 그중에서도 한국어 처리의 최신 동향을 살펴보겠습니다."
        )
        assert detect_language_from_markdown(text) == "ko"

    def test_ko_with_english_brand_names_still_ko(self) -> None:
        """A Korean article that mentions Latin brand names should detect as
        ko (Hangul ratio still dominates after _strip_noise)."""
        text = (
            "Apple 한국과 Samsung 갤럭시 사이의 경쟁이 치열합니다. "
            "최근 iPhone 15와 갤럭시 S24의 비교 리뷰가 화제입니다."
        )
        assert detect_language_from_markdown(text) == "ko"

    def test_ko_alias_works(self) -> None:
        """detect_language is a deprecated alias of detect_language_from_markdown."""
        text = "안녕하세요 한국어"
        assert detect_language(text) == "ko"
        assert detect_language(text) == detect_language_from_markdown(text)


class TestCodepointShortCircuitSemantics:
    """R5 short-circuit: highest-ratio script wins; denom < 5 falls through."""

    def test_min_denom_guard_falls_through(self) -> None:
        """Degenerate 2-letter inputs don't trigger short-circuit."""
        assert detect_language_from_markdown("x y") == "unknown"

    def test_empty_string_unknown(self) -> None:
        assert detect_language_from_markdown("") == "unknown"

    def test_cyrillic_short_circuits_to_ru(self) -> None:
        # Cyrillic-dominant text — must still short-circuit ru even with the
        # new ko / en branches added.
        text = "Это статья о машинном обучении и искусственном интеллекте."
        assert detect_language_from_markdown(text) == "ru"

    def test_cjk_short_circuits_to_zh_cn(self) -> None:
        # CJK-dominant (Chinese) text
        text = "这是一篇关于人工智能技术的文章我们在这里讨论一些技术细节。"
        assert detect_language_from_markdown(text) == "zh-CN"

    def test_latin_short_circuits_to_en(self) -> None:
        # Latin-dominant (English) text
        text = "This is a test article about machine learning and we discuss some details."
        assert detect_language_from_markdown(text) == "en"


class TestEnHintsWordBoundary:
    """R5 EN_HINTS word-boundary fix — short stopwords like 'a', 'i', 'to'
    must not match as substrings inside Latin brand names that appear in
    non-English articles. Tested via the keyword-scoring fallthrough path
    (Hangul ratio below threshold)."""

    def test_brand_only_text_does_not_score_en_falsely(self) -> None:
        """A small text whose visible content is only Latin brand names —
        no real English words. With word-boundary EN_HINTS, this scores zero
        for en hints. With ko hint matching, ko score is also 0. Result:
        unknown (not en)."""
        # The codepoint short-circuit fires here (Latin ratio 1.0 over 17 L/M
        # codepoints — "AppleiPadYouTube" = 17 letters, well above the
        # minimum denom guard). The short-circuit DOES return en — but that
        # is "this is a Latin-script text" not "this is English prose." The
        # word-boundary fix matters when the short-circuit doesn't fire
        # (test below).
        text = "Apple iPad YouTube"
        # Short-circuit fires: Latin ratio = 1.0 → "en"
        assert detect_language_from_markdown(text) == "en"

    def test_mostly_korean_with_brand_mentions_still_ko(self) -> None:
        """The key case: a ko article with some Latin brand mentions. Old
        behavior (no word boundary, substring matching) would over-count
        'a', 'i', 'in', 'on' inside 'Apple', 'iPad' and risk classifying as
        en. New behavior: codepoint short-circuit picks ko first."""
        text = (
            "한국어 기사에서 Apple iPad와 YouTube를 언급합니다. "
            "기술 분야의 최신 동향을 살펴봅시다."
        )
        assert detect_language_from_markdown(text) == "ko"


class TestDetectLanguageFromHtml:
    """R4 4-step pipeline: unescape → strip script/style block bodies →
    strip remaining tag markers → delegate to markdown variant."""

    def test_simple_html_korean_body(self) -> None:
        html = "<p>안녕하세요 오늘은 한국어 기사를 작성합니다.</p>"
        assert detect_language_from_html(html) == "ko"

    def test_html_with_script_body_english_stopwords_stays_ko(self) -> None:
        """Adversarial: ko visible body with <script> body that contains
        English stopwords (JS source poison) must still detect as ko —
        Step 2 strips the script body before scoring."""
        html = (
            "<p>안녕하세요 한국어 기사입니다. 오늘은 흥미로운 주제를 다룹니다.</p>"
            "<script>const w = 'the of and to in that an at it on with';</script>"
        )
        assert detect_language_from_html(html) == "ko"

    def test_html_with_style_body_english_words_stays_ko(self) -> None:
        """Adversarial: <style> bodies often carry English keywords
        (font-family names, comments). Must not poison detection."""
        html = (
            "<p>안녕하세요 한국어 기사입니다. 흥미로운 내용입니다.</p>"
            "<style>body { font-family: 'the times new roman', serif; }</style>"
        )
        assert detect_language_from_html(html) == "ko"

    def test_html_numeric_entity_hangul(self) -> None:
        """Adversarial: ko text encoded as numeric HTML entities. Step 1
        (unescape) must decode before scoring."""
        # &#54620;&#44397;&#50612; = 한국어 (Korean: "Korean language")
        html = (
            "<p>오늘 주제는 &#54620;&#44397;&#50612;입니다.</p>"
            "<p>흥미로운 분야의 최신 동향입니다.</p>"
        )
        assert detect_language_from_html(html) == "ko"

    def test_html_encoded_script_with_stopwords(self) -> None:
        """Adversarial: encoded ``&lt;script&gt;...&lt;/script&gt;`` payloads.
        Step 1 decodes ``&lt;`` and ``&gt;`` into ``<`` and ``>`` BEFORE
        step 2 strips script blocks — so the decoded payload's body content
        is removed, not surviving into the keyword scorer."""
        html = (
            "<p>안녕하세요 한국어 기사입니다. 흥미로운 주제입니다.</p>"
            "&lt;script&gt;the the the of and to in that&lt;/script&gt;"
        )
        assert detect_language_from_html(html) == "ko"

    def test_html_with_links_visible_text_preserved(self) -> None:
        """<a href=...> tags are stripped, but visible anchor text remains
        and contributes to detection."""
        html = (
            "<p>오늘 주제는 흥미로운 한국 기술입니다.</p>"
            '<a href="https://example.com" target="_blank" rel="noopener">'
            "자세히 보기</a> 페이지를 참조하세요."
        )
        assert detect_language_from_html(html) == "ko"

    def test_html_pure_english_body(self) -> None:
        """Sanity: HTML containing English content still detects as en."""
        html = (
            "<p>This is a test article about machine learning techniques. "
            "We discuss the basics and applications in this post.</p>"
        )
        assert detect_language_from_html(html) == "en"

    def test_html_empty_returns_unknown(self) -> None:
        """Empty or tag-only HTML strips to empty and returns unknown."""
        assert detect_language_from_html("") == "unknown"
        assert detect_language_from_html("<p></p>") == "unknown"

    def test_html_alias_via_detect_language_consistent(self) -> None:
        """detect_language (alias) operates on stripped text; given the
        same already-stripped input, the from_markdown and from_html paths
        agree on plain-text content."""
        text = "안녕하세요 한국어 기사입니다."
        assert detect_language(text) == detect_language_from_markdown(text)


class TestKoHintsConstantShape:
    """KO_HINTS list shape — backstop for the codepoint short-circuit on
    Hanja-heavy ko articles (where Hangul ratio < threshold)."""

    def test_ko_hints_has_at_least_30_entries(self) -> None:
        # Plan 2026-05-18-006 Unit 2 R6: ≥30 high-frequency Korean particles.
        assert len(KO_HINTS) >= 30

    def test_ko_hints_are_all_hangul(self) -> None:
        # Sanity: every entry must contain at least one Hangul Syllable.
        for hint in KO_HINTS:
            assert any(0xAC00 <= ord(c) <= 0xD7AF for c in hint), (
                f"KO_HINTS entry {hint!r} has no Hangul Syllable codepoint"
            )


class TestExistingDetectionUnchanged:
    """Regression suite: the new code path must reproduce existing
    detection outputs for the 3 pre-existing languages."""

    def test_zh_cn_with_latin_brand_urls_still_zh(self) -> None:
        """Pre-existing regression test from work-themed branch — must
        still pass after the Unit 2 refactor."""
        text = (
            '在论坛上看到有人推荐 <a href="https://51acgs.com/animate/14529" '
            'target="_blank" rel="noopener">51漫畫</a>，自己跟着看了一阵子。\n\n'
            '<a href="https://51acgs.com/animate" target="_blank" rel="noopener">'
            '51acgs</a> 是日常会扫一眼的页面。'
        )
        assert detect_language(text) == "zh-CN"

    def test_ru_with_latin_urls_still_ru(self) -> None:
        text = (
            "Это статья о машинном обучении, мы обсуждаем здесь детали. "
            "<a href=\"https://github.com\">пример</a> и "
            "[Wikipedia](https://en.wikipedia.org) — внешние ссылки."
        )
        assert detect_language(text) == "ru"
