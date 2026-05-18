"""Anchor-text language gate (R4 of plan 2026-05-14-001).

Pure-function helpers that decide whether an anchor text matches a row's
declared language. Uses a codepoint-set heuristic rather than ``detect_language``
because anchor surface forms are typically 2-4 characters — the keyword-list
scorer in :mod:`language_check` scores zero on them and falls into the
"unknown -> allow through" branch, defeating the gate.

Public entry: :func:`check_anchor_language`. Exemption order is:

1. ``link_kind`` not in ``{"main_domain", "target"}`` -> exempt.
   Auxiliary citations (Wiki, MDN, GitHub) legitimately use foreign-language
   names in any host article.
2. ``anchor`` is a member of ``branded_pool`` -> exempt.
   Latin brand names ("Apple", "Notion") in zh-CN articles are intentional.
3. ``row_language`` outside :data:`~backlink_publisher.language_check.SUPPORTED_LANGUAGES`
   -> exempt with no codepoint check (the gate cannot speak for non-enum
   languages; R3 contract).
4. Apply the per-language codepoint rule (see :data:`_LANGUAGE_RULES`).
"""

from __future__ import annotations

from backlink_publisher.linkcheck.language import SUPPORTED_LANGUAGES

__all__ = ["check_anchor_language"]


#: CJK Unified Ideographs BMP block. Extension A (U+3400..U+4DBF) and beyond
#: are deferred until a real-world false-negative surfaces (see plan §Scope).
_CJK_BMP_START, _CJK_BMP_END = 0x4E00, 0x9FFF

#: Cyrillic block.
_CYR_START, _CYR_END = 0x0400, 0x04FF

#: Hangul Syllables block. Jamo (U+1100..U+11FF) deferred to follow-up
#: (plan 2026-05-18-006 Unit 3 §Scope — widen on first real-world
#: false-negative).
_HANGUL_BMP_START, _HANGUL_BMP_END = 0xAC00, 0xD7AF

#: Link kinds whose anchor text is subject to R4. Anything else is exempt.
_GATED_KINDS = frozenset({"main_domain", "target"})


def _has_cjk(text: str) -> bool:
    return any(_CJK_BMP_START <= ord(c) <= _CJK_BMP_END for c in text)


def _has_cyrillic(text: str) -> bool:
    return any(_CYR_START <= ord(c) <= _CYR_END for c in text)


def _has_latin_letter(text: str) -> bool:
    return any(("A" <= c <= "Z") or ("a" <= c <= "z") for c in text)


def _has_hangul(text: str) -> bool:
    return any(_HANGUL_BMP_START <= ord(c) <= _HANGUL_BMP_END for c in text)


def _check_zh_cn(anchor: str) -> tuple[bool, str | None]:
    if _has_cjk(anchor):
        return True, None
    return False, "anchor missing CJK codepoint"


def _check_ru(anchor: str) -> tuple[bool, str | None]:
    if _has_cyrillic(anchor):
        return True, None
    return False, "anchor missing Cyrillic codepoint"


def _check_en(anchor: str) -> tuple[bool, str | None]:
    if not _has_latin_letter(anchor):
        return False, "anchor missing Latin letter"
    if _has_cjk(anchor):
        return False, "en anchor contains CJK codepoint"
    if _has_cyrillic(anchor):
        return False, "en anchor contains Cyrillic codepoint"
    return True, None


def _check_ko(anchor: str) -> tuple[bool, str | None]:
    """Plan 2026-05-18-006 Unit 3 R7 — ko anchor strict-mirror of en.

    Required: at least one Hangul Syllable codepoint. Forbidden: any CJK
    BMP codepoint (rejects mixed-script Hanja ko anchors like ``"金正恩"``),
    any Cyrillic codepoint. Latin letters / digits / punctuation are
    allowed (mixed ko + Latin brand mentions like ``"Apple 한국"`` pass).

    Mixed-script proper nouns (``"金正恩 인터뷰"``, ``"首爾"``) go via the
    branded_pool exemption at the call site (see module docstring step 2);
    Unit 3's rule does not modify the existing exemption order.
    """
    if not _has_hangul(anchor):
        return False, "anchor missing Hangul codepoint"
    if _has_cjk(anchor):
        return False, "ko anchor contains CJK codepoint"
    if _has_cyrillic(anchor):
        return False, "ko anchor contains Cyrillic codepoint"
    return True, None


_LANGUAGE_RULES = {
    "zh-CN": _check_zh_cn,
    "ru": _check_ru,
    "en": _check_en,
    "ko": _check_ko,
}


def check_anchor_language(
    anchor: str,
    row_language: str,
    link_kind: str,
    branded_pool: list[str],
) -> tuple[bool, str | None]:
    """Return ``(ok, reason)`` for the anchor against the row's language.

    ``ok=True`` means the anchor passes (either exempted or matched the
    codepoint rule). ``reason`` is a short tag the caller can use to compose
    a structured ``validation.errors`` entry.
    """
    if link_kind not in _GATED_KINDS:
        return True, None
    if anchor in branded_pool:
        return True, None
    if row_language not in SUPPORTED_LANGUAGES:
        return True, None
    rule = _LANGUAGE_RULES[row_language]
    return rule(anchor)
