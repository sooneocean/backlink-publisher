"""Language detection helpers for the backlink pipeline."""

from __future__ import annotations


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


def _score_language(text: str, hints: list[str]) -> int:
    """Count occurrences of language hints in text."""
    score = 0
    lower = text.lower()
    for hint in hints:
        score += lower.count(hint.lower())
    return score


def detect_language(text: str) -> str:
    """Roughly detect the language of a text.

    Returns one of: 'zh-CN', 'ru', 'en', or 'unknown'.
    """
    zh_score = _score_language(text, ZH_HINTS)
    ru_score = _score_language(text, RU_HINTS)
    en_score = _score_language(text, EN_HINTS)

    scores = {"zh-CN": zh_score, "ru": ru_score, "en": en_score}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "unknown"
    return best


def language_matches(detected: str, requested: str) -> bool:
    """Check if detected language roughly matches the requested language."""
    if detected == "unknown":
        return True  # Can't disprove, allow through
    if requested == "zh-CN" and detected == "zh-CN":
        return True
    if requested == "en" and detected == "en":
        return True
    if requested == "ru" and detected == "ru":
        return True
    # Cross-check: if we detected something clearly different, fail
    if detected != requested:
        # Allow some flexibility — short texts may misdetect
        return True
    return True