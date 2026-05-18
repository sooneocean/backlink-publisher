"""Language detection helpers for the backlink pipeline."""

from __future__ import annotations

import html as html_lib
import re
import unicodedata


#: Languages the gate semantically distinguishes. Anything outside this set is
#: treated as ``"unknown"`` for matching purposes (R3, see plan
#: ``docs/plans/2026-05-14-001-feat-mandatory-linkcheck-lang-gate-plan.md``).
#:
#: Canonical source of truth — :mod:`backlink_publisher.schema` imports from
#: here. Adding a new language is a one-line change in this file
#: (plan 2026-05-18-006 Unit 1: ko added; ja / zh-TW deferred to follow-ups).
SUPPORTED_LANGUAGES = frozenset({"zh-CN", "ru", "en", "ko"})


#: Patterns removed from text BEFORE language scoring.
#:
#: The EN_HINTS substring-counting heuristic over-counts on URLs ("a" in
#: "stackoverflow", "in" in "github"), HTML tag attributes (`target="_blank"`,
#: `rel="noopener"`, `<a href`), and Latin anchor texts (Wikipedia, MDN) that
#: are language-neutral by nature. Without stripping these, any zh-CN or ru
#: article that embeds a few Latin-domain links can score as en. Order matters:
#: strip markdown ``[text](url)`` first to preserve the visible anchor text,
#: then bare URLs, then any remaining HTML tags + attributes.
_NOISE_PATTERNS = (
    # Markdown anchor: `[visible](https://example.com)` → keep `visible`.
    # Must run BEFORE the HTML strip because `[...](...)` syntax isn't HTML.
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    # HTML tag with attributes — drop entirely (including URL-bearing
    # attrs like `href="https://..."`, plus `target="_blank"`,
    # `rel="noopener"`). Must run BEFORE the bare-URL strip so the greedy
    # `\S+` URL regex doesn't eat HTML attribute closers when a URL is
    # embedded inside an `href="..."` attribute.
    (re.compile(r"<[^>]+>"), ""),
    # Bare URL (http/https) outside any tag/markdown — drop entirely.
    (re.compile(r"https?://\S+"), ""),
)


def _strip_noise(text: str) -> str:
    """Remove URLs + HTML tags + markdown link syntax from ``text``.

    Returns the cleaned text. Visible anchor text from markdown links is
    preserved (it carries real language signal — Chinese anchor → Chinese
    counts as such; Latin anchor → Latin counts).
    """
    for pattern, repl in _NOISE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# --- Codepoint-first short-circuit (R5, Plan 2026-05-18-006 Unit 2) ----------

#: Hangul Syllables block. Jamo (U+1100..U+11FF) deferred to follow-up.
_HANGUL_RANGE = (0xAC00, 0xD7AF)
#: Cyrillic block (Russian + adjacent).
_CYRILLIC_RANGE = (0x0400, 0x04FF)
#: CJK Unified Ideographs BMP block (zh-CN; Extension A deferred).
_CJK_BMP_RANGE = (0x4E00, 0x9FFF)

#: Ratio threshold at which the script-first short-circuit fires. **0.30 is
#: uncalibrated v1 default** — corpus calibration spike against ~50 real ko
#: articles is deferred to post-merge per plan 2026-05-18-006
#: Deferred-to-Implementation. Pass-2 review acknowledged a Hanja-heavy
#: ko article (e.g. 80 Hangul + 200 Hanja over 330 L/M denom) can fall through
#: to keyword scoring; the word-boundary EN_HINTS fix in this same refactor
#: prevents the previously-silent en-misdetection path even when fallthrough
#: happens.
# TODO(ko-corpus-calibration): threshold=0.30 unvalidated against real ko
# articles; revise after spike runs against ~50 Naver Blog / Tistory / Korean
# news samples (deferred to post-merge per plan 2026-05-18-006).
_RATIO_THRESHOLD = 0.30


def _formal_denominator(text: str) -> int:
    """Count codepoints belonging to a writing system (Unicode L / M categories).

    Excludes whitespace, digits, punctuation, symbols (other than letter-like),
    and control codepoints — these are language-neutral noise that would
    otherwise dilute the per-script ratio used by the short-circuit.

    Plan 2026-05-18-006 Unit 2 R5: shared denominator definition between this
    detection short-circuit and Unit 4's ``anchor_resolver._passes_filters``
    ko-branch (when that lands).
    """
    return sum(1 for c in text if unicodedata.category(c)[0] in ("L", "M"))


def _count_in_range(text: str, lo: int, hi: int) -> int:
    """Count codepoints in ``text`` whose ord falls in ``[lo, hi]`` (inclusive)."""
    return sum(1 for c in text if lo <= ord(c) <= hi)


def _count_latin_letters(text: str) -> int:
    """Count Basic Latin letters (A-Z, a-z). Latin-1 Supplement excluded —
    conservative default; widening can come later if false-negatives surface."""
    return sum(1 for c in text if ("A" <= c <= "Z") or ("a" <= c <= "z"))


def _codepoint_short_circuit(text: str) -> str | None:
    """R5 codepoint-first detection. Returns the dominant script's language
    when its ratio over the formal denominator is at least :data:`_RATIO_THRESHOLD`,
    else ``None`` so callers fall through to keyword scoring.

    Returns one of ``"zh-CN"``, ``"ru"``, ``"en"``, ``"ko"``, or ``None``.

    The dominant-script tiebreak (max ratio wins) means a mixed-script article
    that is 70% Hangul + 25% Hanja returns ``"ko"`` (Hangul wins on ratio,
    above threshold); a 25% Hangul + 60% Hanja article returns ``"zh-CN"``
    (CJK wins) — pass-2-acknowledged edge case for ja/Hanja-heavy ko content,
    deferred to corpus calibration.
    """
    denom = _formal_denominator(text)
    #: Minimum denominator guard: degenerate inputs (HTML/URL noise only,
    #: < 5 letter-or-mark codepoints) fall through to keyword scoring where
    #: they correctly return ``"unknown"``. Without this guard, a 2-char
    #: HTML body like ``<a>x</a> <a>y</a>`` (post-strip ``"x y"``) would
    #: short-circuit to ``"en"`` despite carrying no real language signal.
    if denom < 5:
        return None
    counts = {
        "ko": _count_in_range(text, *_HANGUL_RANGE),
        "ru": _count_in_range(text, *_CYRILLIC_RANGE),
        "zh-CN": _count_in_range(text, *_CJK_BMP_RANGE),
        "en": _count_latin_letters(text),
    }
    best_lang = max(counts, key=lambda k: counts[k])
    if counts[best_lang] == 0:
        return None
    if counts[best_lang] / denom >= _RATIO_THRESHOLD:
        return best_lang
    return None


# --- Keyword hints (backstop scorer when short-circuit doesn't fire) --------

# Simple keyword-based language hints (no external dependency)
# This is a rough heuristic — good enough for validation purposes.

ZH_HINTS = [
    "的", "是", "在", "了", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "它", "们", "里", "那", "个", "么", "什么", "怎么", "为什么",
]

RU_HINTS = [
    "и", "в", "не", "на", "я", "с", "что", "он", "к", "а",
    "то", "она", "так", "по", "но", "его", "для", "нет", "из",
    "это", "как", "у", "же", "за", "что", "если", "может",
    "также", "только", "уже", "всё", "все", "где", "ещё",
]

EN_HINTS = [
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
    "or", "an", "will", "my", "one", "all", "would", "there", "their",
    "what", "so", "up", "out", "if", "about", "who", "get", "which", "go",
]

#: Korean particles + endings. Backstop for the codepoint short-circuit when
#: a ko article has Hangul ratio < 0.30 (Hanja-heavy newspaper / academic
#: text). Plan 2026-05-18-006 Unit 2 R6: ≥30 entries — high-frequency
#: particles and sentence endings unambiguously identifying Korean.
KO_HINTS = [
    "는", "이", "가", "을", "를", "의", "에", "에서", "으로", "와",
    "과", "도", "만", "까지", "부터", "한다", "했다", "한다고", "입니다",
    "그리고", "하지만", "그러나", "그래서", "때문에", "위해",
    "있다", "없다", "같다", "보다", "처럼",
    "처음", "마지막", "다음", "함께", "또한",
]


def _score_language(text: str, hints: list[str], use_word_boundary: bool = False) -> int:
    """Count occurrences of language hints in ``text``.

    Plan 2026-05-18-006 Unit 2 R5: ``use_word_boundary=True`` for Latin hints
    (EN_HINTS) prevents short stopwords like ``"a"``, ``"i"``, ``"it"``,
    ``"to"`` from matching as substrings inside Latin brand names that
    legitimately appear in non-English articles (``Apple``, ``iPad``,
    ``YouTube``). Non-Latin hint sets (ZH/RU/KO) keep substring counting —
    their codepoints don't have the ambiguous-substring problem.
    """
    score = 0
    lower = text.lower()
    if use_word_boundary:
        for hint in hints:
            hint_lower = hint.lower()
            score += len(re.findall(rf"\b{re.escape(hint_lower)}\b", lower))
    else:
        for hint in hints:
            score += lower.count(hint.lower())
    return score


# --- Public detection entry points ------------------------------------------


def detect_language_from_markdown(text: str) -> str:
    """Detect the language of a markdown / plain-text source.

    Plan 2026-05-18-006 Unit 2 R4 + R5: two-stage detection. First the
    codepoint-first short-circuit (R5) returns the dominant script's
    language if its ratio meets the threshold; this catches the common
    case cleanly (a real Korean article has ≈100% Hangul ratio).

    Only when no script dominates does keyword scoring fall through —
    and at that point EN_HINTS uses word-boundary matching to avoid
    inflating en-score on Latin substrings inside non-English articles.

    Returns one of: ``"zh-CN"``, ``"ru"``, ``"en"``, ``"ko"``, ``"unknown"``.
    """
    text = _strip_noise(text)
    short_circuit = _codepoint_short_circuit(text)
    if short_circuit is not None:
        return short_circuit
    # No script dominates — fall through to keyword scoring.
    zh_score = _score_language(text, ZH_HINTS)
    ru_score = _score_language(text, RU_HINTS)
    en_score = _score_language(text, EN_HINTS, use_word_boundary=True)
    ko_score = _score_language(text, KO_HINTS)
    scores = {"zh-CN": zh_score, "ru": ru_score, "en": en_score, "ko": ko_score}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    return best


def detect_language_from_html(html: str) -> str:
    """Detect the language of an HTML source.

    Plan 2026-05-18-006 Unit 2 R4 — four-step pipeline.
    **Ordering matters**: entities are decoded BEFORE stripping ``<script>``
    and ``<style>`` block contents, so encoded ``&lt;script&gt;...&lt;/script&gt;``
    payloads (common in WordPress / Blogger exports that double-encode through
    TinyMCE-style editors) cannot survive the decode and then poison the
    keyword scorer with their decoded body contents.

    1. ``html.unescape(html)`` — decode HTML entities (numeric refs +
       encoded tag markers).
    2. Strip ``<script>...</script>`` and ``<style>...</style>`` block
       contents, not just the tag markers. JS source bodies routinely carry
       English stopwords (``"the"``, ``"to"``, ``"of"`` as identifiers, strings,
       comments) that poison the keyword scorer.
    3. Strip remaining tag markers via stdlib regex. Self-contained — does
       **not** reuse ``markdown_utils._strip_html`` (which assumes
       HTML-encoded input, but step 1 has already decoded).
    4. Delegate to :func:`detect_language_from_markdown` (which also strips
       bare URLs and markdown link syntax via :data:`_NOISE_PATTERNS`).
    """
    # Step 1: decode HTML entities (numeric refs + encoded tag markers)
    text = html_lib.unescape(html)
    # Step 2: strip <script> and <style> block contents (DOTALL so the body
    # spans newlines; case-insensitive so <SCRIPT> / <Style> match too)
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        "",
        text,
        flags=re.S | re.I,
    )
    # Step 3: strip remaining tag markers (self-contained — see docstring)
    text = re.sub(r"<[^>]+>", "", text)
    # Step 4: delegate to markdown variant
    return detect_language_from_markdown(text)


def detect_language(text: str) -> str:
    """Deprecated alias of :func:`detect_language_from_markdown`.

    Preserved for backward compatibility — existing callers in
    :mod:`validate_backlinks`, :mod:`plan_backlinks`, and the test suite
    call this name directly. Plan 2026-05-18-006 Unit 2 R11: alias stays
    in v1; removal is a follow-up PR with its own grep + count audit.

    For new code, prefer :func:`detect_language_from_markdown` (explicit
    source format) or :func:`detect_language_from_html` (when input is HTML).
    """
    return detect_language_from_markdown(text)


def language_matches(detected: str, requested: str) -> bool:
    """Check if the detected language matches the requested language.

    Contract (R1, see plan 2026-05-14-001):
    - ``"unknown"`` on either side is the escape valve — returns True (the
      caller can't disprove a mismatch when one side is undetermined).
    - Two known, equal languages match.
    - Two known, different languages do NOT match — return False so the
      validate-time gate (R2) can fail the row.

    Languages outside :data:`SUPPORTED_LANGUAGES` are coerced to ``"unknown"``
    semantics: the gate cannot speak for them, so they pass.

    Plan 2026-05-18-006 R6b: this function is already generic over the
    frozenset; adding ``"ko"`` to :data:`SUPPORTED_LANGUAGES` automatically
    extends ``ko ↔ ko`` match and ``ko ↔ {en, zh-CN, ru}`` mismatch behavior
    with no body changes here.
    """
    if detected == "unknown" or requested == "unknown":
        return True
    if detected not in SUPPORTED_LANGUAGES or requested not in SUPPORTED_LANGUAGES:
        # Treat out-of-enum values as unknown — same "can't disprove" branch.
        return True
    return detected == requested
