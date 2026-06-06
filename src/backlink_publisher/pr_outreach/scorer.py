"""Relevance scorer for PR opportunities.

Scores an opportunity headline+summary against the operator's target
keyword/topic pools (from config ``[targets.*].branded_pool`` and
``[targets.*].exact_pool``).

Score range: 0–100.  Returns a float; higher = more relevant.
"""

from __future__ import annotations

import re
from typing import Any


def _tokenize(text: str) -> set[str]:
    """Lowercase alpha/digit tokens, length >= 2."""
    return {t for t in re.split(r"[^a-z0-9一-鿿]+", text.lower()) if len(t) >= 2}


def score_opportunity(
    opportunity: dict[str, Any],
    topic_tokens: set[str],
) -> float:
    """Return a relevance score (0–100) for ``opportunity`` against ``topic_tokens``.

    Scoring formula:
    - Each matched token in headline contributes 10 points (max 50).
    - Each matched token in summary contributes 5 points (max 50).
    - Total capped at 100.
    """
    if not topic_tokens:
        return 0.0

    headline = str(opportunity.get("headline") or "")
    summary = str(opportunity.get("summary") or "")

    ht = _tokenize(headline)
    st = _tokenize(summary)

    headline_score = min(len(ht & topic_tokens) * 10, 50)
    summary_score = min(len(st & topic_tokens) * 5, 50)
    return float(min(headline_score + summary_score, 100))


def build_topic_tokens(config_targets: dict[str, Any]) -> set[str]:
    """Extract a flat token set from all targets' branded/exact pools."""
    tokens: set[str] = set()
    for _name, target in config_targets.items():
        if not isinstance(target, dict):
            continue
        for pool_key in ("branded_pool", "exact_pool"):
            for term in target.get(pool_key) or []:
                tokens.update(_tokenize(str(term)))
    return tokens
