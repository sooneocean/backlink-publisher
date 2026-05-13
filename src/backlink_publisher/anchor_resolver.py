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

from .adapters.llm_anchor_provider import (
    LLMAnchorRequest,
    OpenAICompatibleProvider,
)
from .config import Config, get_anchor_pool_v2

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

_MIN_ANCHOR_LEN: int = 2
_MAX_ANCHOR_LEN: int = 8
_MIN_CJK_RATIO: float = 0.5


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
) -> str | None:
    """Pick one anchor text for one link slot. ``None`` means "exhausted".

    Source priority:
    1. Config-pinned typed pool for ``(main_domain, url_category, anchor_type)``.
       This is the cheap, deterministic path — no network, no LLM tokens.
    2. LLM provider, if configured. The provider returns up to 5 candidates;
       the same filter pipeline runs over each one. First survivor wins.

    ``rng`` is dependency-injected to make tests reproducible; production
    callers can leave it ``None`` to use module-level randomness.
    """
    rng = rng or random.Random()
    recent_set = set(recent_texts)

    # 1. Try the static pool first.
    pool = get_anchor_pool_v2(config, main_domain, url_category, anchor_type)
    pool_candidates = [w for w in pool if _passes_filters(w) and w not in recent_set]
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
        if _passes_filters(c) and c not in recent_set:
            return c
    return None


def _passes_filters(text: str) -> bool:
    """Return True iff ``text`` is a publishable anchor.

    Four checks, in order of cheapness:
    - Length must be 2-8 characters (brainstorm R25)
    - Must not be in the FORBIDDEN_ANCHOR_TEXTS deny-list
    - Must contain none of the unsafe character classes
    - Must be predominantly CJK Unified Ideographs (≥50% by char count)
    """
    if not isinstance(text, str):
        return False
    length = len(text)
    if length < _MIN_ANCHOR_LEN or length > _MAX_ANCHOR_LEN:
        return False
    if text in FORBIDDEN_ANCHOR_TEXTS:
        return False
    if _UNSAFE_ANCHOR_CHARS.search(text):
        return False
    cjk_count = len(_CJK_CHAR.findall(text))
    if cjk_count / length < _MIN_CJK_RATIO:
        return False
    return True
