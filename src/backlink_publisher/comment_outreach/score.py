"""``comment qualify`` — score CommentTargets and emit conservative QualificationResults.

The decision ladder is **conservative by construction** (R5): a social-platform target, an
unknown/absent comment region (``comment_open`` in ``{null, false}``), or an explicitly
not-indexed page (``indexed=False``) can never reach ``accept`` — they fall to ``review`` or
``reject``. Every branch is explicit and appends a reason; there is **no silent ``else``** (a
silent fall-through is how the events projector once dropped successes — see the
projector-drift learning).

**Deliberate:** ``indexed=None`` (indexability *unknown* — discovery does not probe SERP
indexability, which is out of scope) is treated as a neutral compliance signal and does NOT
gate ``accept``. This is intentional: the module's objective is referral traffic / brand
mention / co-citation, **not** PageRank transfer, so a comment-open, on-topic page is worth a
manual brief even when its indexability was never confirmed. Only an explicit ``indexed=False``
(a page that reported itself non-indexable) is gated. ``test_qualify`` pins this behavior.

Signals and weights here are a **documented starter**. Where the real signal originates
(SERP indexability, ToS-derived ``link_allowed``, an authority feed) is deferred; weight /
threshold tuning is deferred. The conservatism of the ladder — not the precision of the
score — is the safety property this unit guarantees.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.comment_outreach import schema

qualify_logger = PipelineLogger("comment-qualify")

#: Platforms whose comment surfaces demand manual judgement and can never auto-accept (R5).
#: medium / blog / forum / other are ordinary web comment surfaces and remain eligible.
SOCIAL_PLATFORMS = {"x", "facebook", "linkedin", "reddit"}

#: Composite-score floor for an ``accept`` (only reached once the conservative gates pass).
ACCEPT_THRESHOLD = 60

#: Starter per-platform posting risk (0 = low, 100 = high). Documented, tunable later.
_PLATFORM_RISK = {"blog": 20, "forum": 40, "medium": 35, "other": 50,
                  "x": 80, "facebook": 80, "linkedin": 70, "reddit": 75}

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _relevance(topic: str, title: str, summary: str) -> int:
    """Token overlap of *topic* against the page title + summary, 0-100. Starter signal."""
    topic_tokens = set(_WORD_RE.findall(topic.lower()))
    if not topic_tokens:
        return 0
    hay = f"{title} {summary}".lower()
    hits = sum(1 for t in topic_tokens if t in hay)
    return round(100 * hits / len(topic_tokens))


def _authority(target: dict[str, Any]) -> int:
    rank = target.get("domain_rank_signal")
    if isinstance(rank, int) and not isinstance(rank, bool) and 0 <= rank <= 100:
        return rank
    return 50  # neutral when no signal supplied


def _compliance(indexed: Optional[bool], comment_open: Optional[bool], link_allowed: Optional[bool]) -> int:
    """How many known-good compliance signals are present, 0-100. Unknowns are neutral,
    explicit negatives subtract — conservative when a field is False, not just absent."""
    score = 50
    score += 15 if indexed is True else (-20 if indexed is False else 0)
    score += 15 if comment_open is True else (-20 if comment_open is False else 0)
    score += 10 if link_allowed is True else (-10 if link_allowed is False else 0)
    return max(0, min(100, score))


def _anchor_risk(anchor_text: Optional[str], topic: str) -> int:
    """Anchor-spam risk, 0-100. Exact-match-to-topic is the classic footprint; no planned
    anchor is zero risk."""
    if not anchor_text or not anchor_text.strip():
        return 0
    a = anchor_text.strip().lower()
    if a == topic.strip().lower():
        return 90  # exact-match anchor — high footprint risk
    topic_tokens = set(_WORD_RE.findall(topic.lower()))
    anchor_tokens = set(_WORD_RE.findall(a))
    if topic_tokens and anchor_tokens <= topic_tokens:
        return 60  # anchor is a subset of the money keywords
    return 30


def score_target(target: dict[str, Any]) -> dict[str, Any]:
    """Compute a :class:`QualificationResult` dict for one validated CommentTarget."""
    platform = target.get("platform")
    comment_open = target.get("comment_open")
    link_allowed = target.get("link_allowed")
    indexed = target.get("indexed")
    topic = target.get("topic") or ""

    relevance = _relevance(topic, target.get("page_title") or "", target.get("thread_summary") or "")
    authority = _authority(target)
    compliance = _compliance(indexed, comment_open, link_allowed)
    anchor_risk = _anchor_risk(target.get("anchor_text"), topic)
    platform_risk = _PLATFORM_RISK.get(platform, 50)

    score = round(
        0.30 * relevance + 0.25 * authority + 0.25 * compliance
        + 0.10 * (100 - anchor_risk) + 0.10 * (100 - platform_risk)
    )
    score = max(0, min(100, score))

    reasons: list[str] = []
    # --- Conservative decision ladder (explicit branches, no silent else) ---
    if platform in SOCIAL_PLATFORMS:
        decision, action = "review", "skip"
        reasons.append(f"platform '{platform}' is social — manual judgement required, never auto-accept")
    elif comment_open is False:
        decision, action = "reject", "skip"
        reasons.append("no comment region detected (comment_open=false)")
    elif comment_open is None:
        decision, action = "review", "skip"
        reasons.append("comment availability unknown (comment_open=null) — verify before commenting")
    elif indexed is False:
        decision, action = "review", "skip"
        reasons.append("page reported not indexed — low referral value, verify")
    elif score < ACCEPT_THRESHOLD:
        decision, action = "review", "skip"
        reasons.append(f"composite score {score} below accept threshold {ACCEPT_THRESHOLD}")
    else:
        decision, action = "accept", "manual_comment_brief"
        reasons.append(f"indexed + comment region open + score {score} >= {ACCEPT_THRESHOLD}")

    # --- Link / anchor policy (conservative defaults) ---
    if link_allowed is False:
        link_policy = "no-link"
        reasons.append("link_allowed=false — no-link policy")
    elif link_allowed is None:
        link_policy = "no-link"
        reasons.append("link permission unknown — defaulting to no-link")
    else:
        link_policy = "single-link-ok"
    anchor_policy = "branded-only"

    reasons.append(
        f"signals relevance={relevance} authority={authority} compliance={compliance} "
        f"anchor_risk={anchor_risk} platform_risk={platform_risk}"
    )

    return {
        "target_id": target.get("id", ""),
        "score": score,
        "decision": decision,
        "action": action,
        "reasons": reasons,
        "signals": {
            "relevance_score": relevance,
            "authority_score": authority,
            "compliance_score": compliance,
            "anchor_risk_score": anchor_risk,
            "platform_risk_score": platform_risk,
            "indexed": indexed,
            "comment_open": comment_open,
            "link_allowed": link_allowed,
        },
        "link_policy": link_policy,
        "anchor_policy": anchor_policy,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def qualify_targets(source: Optional[TextIO] = None, dest: Optional[TextIO] = None) -> dict[str, int]:
    """Read CommentTarget JSONL, emit QualificationResult JSONL. Always exit-0 semantics:
    invalid input rows are surfaced via RECON (not silently dropped) and skipped."""
    rows = read_jsonl(source, strict=False)
    results: list[dict[str, Any]] = []
    rejected = 0
    counts = {"accept": 0, "review": 0, "reject": 0}
    for idx, target in enumerate(rows, start=1):
        errors = schema.validate_comment_target(target)
        if errors:
            rejected += 1
            qualify_logger.recon("comment_qualify_skip", row=idx, id=target.get("id"), reasons=errors)
            continue
        result = score_target(target)
        counts[result["decision"]] += 1
        results.append(result)

    write_jsonl(results, dest)
    qualify_logger.recon("comment_qualify_summary", qualified=len(results), rejected=rejected, decisions=counts)
    return {"qualified": len(results), "rejected": rejected}
