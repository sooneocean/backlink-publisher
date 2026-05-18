"""Resolve a scheduler decision into concrete anchor text.

The scheduler (Unit 4) decides a *type* per link slot. This module decides the
actual *text* — drawing from the config-pinned typed pool when one exists for
the (url_category, anchor_type) cell, and falling back to LLM-generated
candidates when the pool is empty. Either way, every candidate runs through
``_passes_filters`` before reaching the caller; the filter is the load-bearing
output sanitization for both SEO quality (no "点击这里"-style placeholder
anchors) and security (no ``<script>``, bidi reorder attacks, or control
characters surviving into the rendered HTML).

Failure semantics: ``resolve_anchor`` returns ``None`` (not raises) when no
acceptable candidate emerges from either source. The caller — Unit 8's
validator/degrade pipeline — translates ``None`` into a retry or degrade
action. We do raise upward when the LLM provider itself errors, because that
is operationally distinct from "exhausted candidates" and the pipeline needs
to see the difference (a 429 storm should retry the provider; an empty
candidate list after filters should trigger degrade).
"""

from __future__ import annotations

import logging
import random
import re
import unicodedata
from typing import Callable

from backlink_publisher.publishing.adapters.llm_anchor_provider import (
    LLMAnchorRequest,
    OpenAICompatibleProvider,
)
from backlink_publisher.config import Config, get_anchor_pool_v2

_log = logging.getLogger(__name__)

# Anchor texts that look like spam to search engines — these phrases convey
# zero search intent and Penguin pattern detection treats them as link-farm
# tells. Inherited from the existing project convention and the brainstorm
# requirement R24. New centralised constant lives here because no other module
# previously enumerated them; if a second consumer needs the list later,
# promote to a shared module then.
FORBIDDEN_ANCHOR_TEXTS: frozenset[str] = frozenset({
    "点击这里",
    "看这里",
    "更多",
    "官网",
    "入口",
    "这个网站",
    "相关页面",
    "了解更多",
})

# Character classes rejected from any anchor text — a stricter superset of the
# legacy ``config._UNSAFE_IN_ANCHOR`` regex. The legacy regex only blocked
# Markdown/HTML breakage; this one also blocks security-relevant inputs that
# could survive markdown-it rendering or bidi-reorder the visible anchor:
#
#   \x00-\x1f, \x7f       ASCII control chars
#   U+200B-U+200F         zero-width joiners / direction marks
#   U+202A-U+202E         legacy bidi overrides (RLO/LRO)
#   U+2066-U+2069         isolate-direction overrides
#   <>"'`[]()\\           HTML/Markdown structural punctuation
#   \n\r                  newlines (would break inline anchor rendering)
_UNSAFE_ANCHOR_CHARS = re.compile(
    "["
    "\x00-\x1f\x7f"
    "​-‏"
    "‪-‮"
    "⁦-⁩"
    "<>\"'`\\[\\]()\\\\"
    "\n\r"
    "]"
)

# CJK Unified Ideographs — the bulk of common simplified Chinese. We require
# anchor text to be PREDOMINANTLY (≥50%) CJK so the resolver doesn't surface
# transliterations or English brand strings the scheduler would mis-bucket as
# Chinese anchor text.
_CJK_CHAR = re.compile(r"[一-鿿]")

# Hangul Syllables block — used by the ko branch of _passes_filters. Plan
# 2026-05-18-006 Unit 4 R13: only Syllables (U+AC00..U+D7AF); Jamo
# (U+1100..U+11FF) deferred to a follow-up brainstorm.
_HANGUL_BMP_START, _HANGUL_BMP_END = 0xAC00, 0xD7AF

_MIN_ANCHOR_LEN: int = 2
_MAX_ANCHOR_LEN: int = 8
_MIN_CJK_RATIO: float = 0.5

#: Hangul ratio threshold for the ko branch. Lower than zh-CN's 0.5 because
#: real-world ko anchors normatively mix Latin brand names (Apple, iPhone)
#: and the strict 0.5 threshold would false-reject ``"Apple 한국 출시"``
#: (Hangul ratio ≈ 0.30 over L+M denominator). Uncalibrated v1 default;
#: corpus calibration spike deferred (plan 2026-05-18-006
#: Deferred-to-Implementation).
# TODO(ko-corpus-calibration): threshold=0.30 unvalidated against real ko
# anchor corpora; revise after spike runs against ~50 Naver Blog / Tistory
# samples (deferred to post-merge per plan 2026-05-18-006).
_MIN_KO_HANGUL_RATIO: float = 0.30


def _formal_denominator(text: str) -> int:
    """Count codepoints belonging to a writing system (Unicode L / M categories).

    Plan 2026-05-18-006 Unit 4 R13 — shared with the R5 codepoint
    short-circuit in :mod:`backlink_publisher.linkcheck.language`. Excludes
    whitespace, digits, punctuation, and control codepoints so the per-script
    ratio reflects real text density, not noise.
    """
    return sum(1 for c in text if unicodedata.category(c)[0] in ("L", "M"))


def _passes_zh_cn_ratio(text: str) -> bool:
    """Existing zh-CN CJK-ratio check — bit-exact preserved (R13).

    Denominator is ``len(text)`` (includes whitespace, digits, punctuation)
    NOT the R5 formal denominator. This preserves the legacy behavior locked
    in by every existing zh-CN ``_passes_filters`` test on the corpus.
    """
    cjk_count = len(_CJK_CHAR.findall(text))
    return cjk_count / len(text) >= _MIN_CJK_RATIO


def _passes_ko_ratio(text: str) -> bool:
    """ko Hangul-ratio check (plan 2026-05-18-006 Unit 4 R13).

    Applies NFC normalization at entry — macOS NFD-decomposed Hangul defeats
    the ``U+AC00..U+D7AF`` range check by splitting syllables into Jamo
    codepoints outside the BMP Syllables block. Denominator is the R5 formal
    denominator (Unicode L+M categories) so Latin/digit/punctuation noise
    doesn't inflate the divisor.

    **Preparatory-only in v1**: no production caller invokes ``_passes_filters``
    with ``language="ko"`` because Unit 7's scheduler activation is reverted
    (pass-2 P0). Exercised exclusively by unit tests until ko-localized
    short-form templates ship.
    """
    text = unicodedata.normalize("NFC", text)
    denom = _formal_denominator(text)
    if denom == 0:
        return False
    hangul_count = sum(1 for c in text if _HANGUL_BMP_START <= ord(c) <= _HANGUL_BMP_END)
    return hangul_count / denom >= _MIN_KO_HANGUL_RATIO


#: Per-language ratio rules. Mirrors :data:`anchor_lang._LANGUAGE_RULES` —
#: same ``language → callable`` shape. Cross-extend both registries when
#: adding a new language. Currently only zh-CN + ko (Unit 4 v1 scope); ru/en
#: not in v1 (pass-2 scope-guardian: adding ru/en filter dispatch is scope
#: creep beyond ko-first).
_RATIO_RULES: dict[str, Callable[[str], bool]] = {
    "zh-CN": _passes_zh_cn_ratio,
    "ko": _passes_ko_ratio,
}


def resolve_anchor(
    *,
    url_category: str,
    anchor_type: str,
    keyword: str,
    target_url: str,
    url_subject: str | None,
    config: Config,
    main_domain: str,
    recent_texts: list[str],
    provider: OpenAICompatibleProvider | None,
    rng: random.Random | None = None,
    language: str = "zh-CN",
) -> str | None:
    """Pick one anchor text for one link slot. ``None`` means "exhausted".

    Source priority:
    1. Config-pinned typed pool for ``(main_domain, url_category, anchor_type)``.
       This is the cheap, deterministic path — no network, no LLM tokens.
    2. LLM provider, if configured. The provider returns up to 5 candidates;
       the same filter pipeline runs over each one. First survivor wins.

    ``rng`` is dependency-injected to make tests reproducible; production
    callers can leave it ``None`` to use module-level randomness.

    ``language`` (plan 2026-05-18-006 Unit 4 R13) selects the per-language
    ratio rule in :func:`_passes_filters`. Default ``"zh-CN"`` preserves
    legacy single-arg callers. ko routing is preparatory-only in v1.
    """
    rng = rng or random.Random()
    recent_set = set(recent_texts)

    # 1. Try the static pool first.
    pool = get_anchor_pool_v2(config, main_domain, url_category, anchor_type)
    pool_candidates = [
        w for w in pool if _passes_filters(w, language) and w not in recent_set
    ]
    if pool_candidates:
        return rng.choice(pool_candidates)

    # 2. Fall back to LLM if available.
    if provider is None:
        return None

    request = LLMAnchorRequest(
        url_category=url_category,
        anchor_type=anchor_type,
        keyword=keyword,
        target_url=target_url,
        url_subject=url_subject,
        n=5,
    )
    candidates = provider.generate_candidates(request)
    for c in candidates:
        if _passes_filters(c, language) and c not in recent_set:
            return c
    return None


def _passes_filters(text: str, language: str = "zh-CN") -> bool:
    """Return True iff ``text`` is a publishable anchor.

    Five checks, in order of cheapness:
    - ``text`` must be a string
    - Length must be 2-8 characters (brainstorm R25)
    - Must not be in the FORBIDDEN_ANCHOR_TEXTS deny-list
    - Must contain none of the unsafe character classes
    - Language-specific ratio check via :data:`_RATIO_RULES` dispatch

    Plan 2026-05-18-006 Unit 4 R13: ``language`` defaults to ``"zh-CN"`` so
    every existing single-arg call site preserves bit-exact behavior. Other
    languages dispatch through :data:`_RATIO_RULES`; languages not in the
    dict (ru/en in v1) skip the ratio check entirely — the language baseline
    checks (length, deny-list, unsafe chars) still apply but no script-ratio
    filter runs. ko is the only non-zh-CN entry in v1; the ko branch is
    preparatory-only (no production caller per pass-2 P0 revert of scheduler
    activation).
    """
    if not isinstance(text, str):
        return False
    # Normalize language arg defensively — production callers pass canonical
    # strings but tests/fixtures may include accidental whitespace.
    language = language.strip() if language else "zh-CN"
    length = len(text)
    if length < _MIN_ANCHOR_LEN or length > _MAX_ANCHOR_LEN:
        return False
    if text in FORBIDDEN_ANCHOR_TEXTS:
        return False
    if _UNSAFE_ANCHOR_CHARS.search(text):
        return False
    # Language-specific ratio dispatch. Languages absent from _RATIO_RULES
    # (ru/en in v1) skip the ratio check — the baseline checks above still
    # apply. Pre-Unit-4 single-arg callers default to "zh-CN" and exercise
    # the existing CJK ratio path unchanged.
    ratio_check = _RATIO_RULES.get(language)
    if ratio_check is None:
        return True
    return ratio_check(text)


# ─── Work-themed anchor filter (Plan 2026-05-13-004 Unit 4) ─────────────────
#
# Stricter character blacklist than ``_UNSAFE_ANCHOR_CHARS`` (adds C1 controls,
# fullwidth ASCII variants, BOM/ZWNBSP) but RELAXES the length cap to 30 chars
# and DROPS the CJK-ratio requirement. Work titles may legitimately be ASCII
# (English anime titles, romanised game names) and the template+title combo
# routinely exceeds 8 chars.
#
# Blocks fullwidth `< > & " '` (U+FF1C/U+FF1E/U+FF06/U+FF02/U+FF07) which the
# legacy regex misses — those would survive HTML-escape if a sanitizer only
# normalises ASCII variants and would let an attacker inject visible content
# that looks like markup once a downstream renderer normalises Unicode.

_WORK_UNSAFE_ANCHOR_CHARS = re.compile(
    "["
    "\x00-\x1f\x7f-\x9f"            # C0 + C1 control chars
    "​-‏"                 # zero-width joiners + direction marks
    "‪-‮"                 # legacy bidi overrides (RLO/LRO)
    "⁦-⁩"                 # isolate-direction overrides
    "﻿"                        # BOM / ZWNBSP
    "＜＞＆＂＇"  # fullwidth < > & " '
    "<>\"'`\\[\\]()\\\\"            # ASCII structural punctuation
    "\n\r"                          # raw newlines
    "]"
)

_WORK_MIN_ANCHOR_LEN: int = 2
_WORK_MAX_ANCHOR_LEN: int = 30


def _passes_work_anchor_filter(text: str) -> bool:
    """Return True iff ``text`` is publishable as a work-themed anchor.

    Differences from :func:`_passes_filters`:
    - length 2–30 (not 2–8) — accommodates `{title} 推荐`-style templates
    - no CJK ratio (work titles may be pure ASCII)
    - blocks the fullwidth ASCII punctuation variants too
    """
    if not isinstance(text, str):
        return False
    length = len(text)
    if length < _WORK_MIN_ANCHOR_LEN or length > _WORK_MAX_ANCHOR_LEN:
        return False
    if text in FORBIDDEN_ANCHOR_TEXTS:
        return False
    if _WORK_UNSAFE_ANCHOR_CHARS.search(text):
        return False
    return True
