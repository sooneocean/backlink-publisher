"""Tests for backlink_publisher.anchor.preflight.

Plan 2026-05-18-006 Unit 7 R14 + R8 + Threat Model anti-injection on
operator-supplied TOML config.
"""

from __future__ import annotations

import unicodedata

import pytest

from backlink_publisher.anchor.preflight import (
    preflight_ko_target_pools,
    validate_anchor_pool_entry,
)


class TestValidateAnchorPoolEntryHappyPath:
    """Plain-valid entries pass."""

    def test_simple_hangul_passes_with_require_hangul(self) -> None:
        ok, reason = validate_anchor_pool_entry("자세히 보기", require_hangul=True)
        assert ok is True
        assert reason is None

    def test_short_hangul_passes(self) -> None:
        ok, _ = validate_anchor_pool_entry("한국어", require_hangul=True)
        assert ok is True

    def test_mixed_hangul_latin_passes(self) -> None:
        ok, _ = validate_anchor_pool_entry("Apple 한국 출시", require_hangul=True)
        assert ok is True

    def test_pure_latin_passes_without_hangul_requirement(self) -> None:
        """branded_pool path (require_hangul=False) — Latin brand names OK."""
        ok, _ = validate_anchor_pool_entry("Apple", require_hangul=False)
        assert ok is True

    def test_pure_hanja_passes_without_hangul_requirement(self) -> None:
        """branded_pool path — Hanja proper nouns (首爾, 釜山) OK."""
        ok, _ = validate_anchor_pool_entry("首爾", require_hangul=False)
        assert ok is True


class TestValidateAnchorPoolEntryHangulRequirement:
    """require_hangul=True enforces Hangul Syllable presence."""

    def test_no_hangul_fails_when_required(self) -> None:
        ok, reason = validate_anchor_pool_entry("Apple", require_hangul=True)
        assert ok is False
        assert "Hangul Syllable" in reason

    def test_pure_hanja_fails_when_hangul_required(self) -> None:
        ok, reason = validate_anchor_pool_entry("首爾", require_hangul=True)
        assert ok is False
        assert "Hangul Syllable" in reason

    def test_empty_string_fails(self) -> None:
        ok, reason = validate_anchor_pool_entry("", require_hangul=True)
        assert ok is False
        assert "empty" in reason

    def test_whitespace_only_fails(self) -> None:
        ok, reason = validate_anchor_pool_entry("   \n\t", require_hangul=True)
        assert ok is False
        assert "empty" in reason

    def test_zero_width_only_fails(self) -> None:
        """All-zero-width string strips to empty → empty error."""
        zw_only = "​‌‍﻿"  # ZWSP ZWNJ ZWJ BOM
        ok, reason = validate_anchor_pool_entry(zw_only, require_hangul=True)
        assert ok is False
        assert "empty" in reason


class TestValidateAnchorPoolEntryNfcNormalization:
    """NFC normalization at entry — NFD Hangul recomposed before Hangul
    presence check."""

    def test_nfd_decomposed_hangul_passes(self) -> None:
        """NFD: 자 → ㅈ + ㅏ (Jamo outside Syllables block). After NFC
        recompose, ≥1 Hangul Syllable codepoint."""
        nfd = unicodedata.normalize("NFD", "자세히 보기")
        # Sanity: NFD form has no Syllable codepoints
        assert all(not (0xAC00 <= ord(c) <= 0xD7AF) for c in nfd)
        ok, _ = validate_anchor_pool_entry(nfd, require_hangul=True)
        assert ok is True


class TestValidateAnchorPoolEntrySanitize:
    """Sanitize rule rejects HTML metachars, controls, Bidi, zero-width
    chars surviving the outer strip."""

    @pytest.mark.parametrize("forbidden", ["<", ">", '"', "'", "&"])
    def test_html_metacharacter_rejected(self, forbidden: str) -> None:
        ok, reason = validate_anchor_pool_entry(
            f"자세히{forbidden}보기", require_hangul=True
        )
        assert ok is False
        assert "HTML metacharacter" in reason

    def test_script_tag_attempt_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(
            "<script>alert(1)</script>", require_hangul=False
        )
        assert ok is False
        assert "HTML metacharacter" in reason

    def test_null_byte_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(
            "자세히\x00보기", require_hangul=True
        )
        assert ok is False
        assert "NUL" in reason

    def test_c0_control_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(
            "자세히\x01보기", require_hangul=True
        )
        assert ok is False
        assert "C0 control" in reason

    def test_del_control_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(
            "자세히\x7f보기", require_hangul=True
        )
        assert ok is False
        assert "C0 control" in reason

    @pytest.mark.parametrize(
        "bidi_char",
        ["‪", "‫", "‬", "‭", "‮",
         "⁦", "⁧", "⁨", "⁩"],
    )
    def test_bidi_formatting_control_rejected(self, bidi_char: str) -> None:
        ok, reason = validate_anchor_pool_entry(
            f"자세히{bidi_char}보기", require_hangul=True
        )
        assert ok is False
        assert "Bidi" in reason

    def test_interior_zero_width_rejected(self) -> None:
        """Outer-stripped zero-width is fine; INTERIOR zero-width (mid-anchor,
        post-strip) is a phishing/invisibility hazard → reject."""
        ok, reason = validate_anchor_pool_entry(
            "자세히​보기", require_hangul=True
        )
        assert ok is False
        assert "zero-width" in reason


class TestValidateAnchorPoolEntryNonString:
    def test_none_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(None, require_hangul=True)  # type: ignore[arg-type]
        assert ok is False
        assert "must be a string" in reason

    def test_integer_rejected(self) -> None:
        ok, reason = validate_anchor_pool_entry(123, require_hangul=True)  # type: ignore[arg-type]
        assert ok is False
        assert "must be a string" in reason


# ── Preflight integration over a config view ────────────────────────────────


def _targets_view(*entries):
    """Build the (target_url, language, anchor_keywords, branded_pool)
    iterable expected by preflight_ko_target_pools."""
    return list(entries)


class TestPreflightKoTargetPools:
    def test_ko_target_with_valid_pools_passes(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                ("https://example.com/", "ko", ["자세히 보기", "한국어 학습"], ["Apple"]),
            ),
        )
        assert errors == []

    def test_zh_cn_target_is_not_validated(self) -> None:
        """Non-ko targets pass through — pre-flight is ko-scoped in v1."""
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                ("https://example.com/", "zh-CN", [], []),  # empty pools, but zh-CN
            ),
        )
        assert errors == []

    def test_ko_target_with_empty_anchor_keywords_fails(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                ("https://example.com/", "ko", [], []),
            ),
        )
        assert len(errors) == 1
        assert "anchor_keywords is empty" in errors[0]
        assert "https://example.com/" in errors[0]

    def test_ko_target_with_no_hangul_anchor_keyword_fails(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                ("https://example.com/", "ko", ["Apple", "iPad"], []),
            ),
        )
        assert any("no entry containing a Hangul Syllable" in e for e in errors)

    def test_ko_target_with_malicious_anchor_keyword_entry_fails(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                (
                    "https://example.com/",
                    "ko",
                    ["자세히 보기", "<script>alert(1)</script>", "한국어"],
                    [],
                ),
            ),
        )
        # The middle entry is rejected by sanitize
        assert any("anchor_keywords[1]" in e and "HTML metacharacter" in e for e in errors)
        # But the target still has Hangul entries — no "no entry containing Hangul" error
        assert not any("no entry containing a Hangul Syllable" in e for e in errors)

    def test_ko_target_with_malicious_branded_pool_entry_fails(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                (
                    "https://example.com/",
                    "ko",
                    ["자세히 보기"],
                    ["Apple", "<iframe>"],
                ),
            ),
        )
        assert any("branded_pool[1]" in e and "HTML metacharacter" in e for e in errors)

    def test_branded_pool_does_not_require_hangul(self) -> None:
        """branded_pool is the exemption list — Latin / Hanja entries pass."""
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                (
                    "https://example.com/",
                    "ko",
                    ["자세히 보기"],
                    ["Apple", "Notion", "首爾", "金正恩"],
                ),
            ),
        )
        assert errors == []

    def test_multiple_ko_targets_aggregate_errors(self) -> None:
        errors = preflight_ko_target_pools(
            config=None,
            targets_view=_targets_view(
                ("https://a.example/", "ko", [], []),  # empty anchor_keywords
                ("https://b.example/", "ko", ["자세히"], ["<script>"]),  # bad branded
                ("https://c.example/", "ko", ["자세히"], []),  # OK
            ),
        )
        # 2 errors: a.example empty anchor_keywords + b.example bad branded
        a_error = [e for e in errors if "https://a.example/" in e]
        b_error = [e for e in errors if "https://b.example/" in e]
        c_error = [e for e in errors if "https://c.example/" in e]
        assert len(a_error) == 1
        assert "anchor_keywords is empty" in a_error[0]
        assert len(b_error) == 1
        assert "branded_pool[0]" in b_error[0]
        assert len(c_error) == 0
