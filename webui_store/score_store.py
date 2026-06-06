"""Persistent store for per-publish score records.

Schema (dict keyed by ``score_id``)::

    {
      "<score_id>": {
        "score_id": str,
        "target_url": str,
        "target_url_hash": str,
        "channel": str,
        "platform_weight": float,
        "dofollow_multiplier": float,
        "survival_bonus": float,
        "score": float,              # base(1) * platform_weight * dofollow_multiplier * survival_bonus
        "base_score": float,         # always 1.0 in v1
        "published_at": str,         # ISO-8601
        "rechecked_at": str | None,
        "status": "initial" | "survival_confirmed" | "survival_lost"
      }
    }
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .base import JsonStore


# Keep in sync with webui_store/__init__.py __all__
__all__ = ["ScoreStore", "compute_score"]


# ── Scoring formula (R10) ──────────────────────────────────────────────

_DEFAULT_WEIGHTS: dict[str, float] = {
    "True": 1.0,       # dofollow=True
    "False": 0.3,      # dofollow=False
    "uncertain": 0.5,  # dofollow="uncertain"
}


def compute_score(
    *,
    platform_weight: float | None = None,
    dofollow_multiplier: float | None = None,
    survival_bonus: float = 1.0,
    base: float = 1.0,
) -> float:
    """Compute a score from its components.

    All multipliers default to 1.0 so a bare call returns *base*.
    """
    w = platform_weight if platform_weight is not None else 1.0
    d = dofollow_multiplier if dofollow_multiplier is not None else 1.0
    return base * w * d * survival_bonus


def platform_weight_from_dofollow(dofollow: bool | str) -> float:
    """Map a dofollow value to its numeric weight."""
    key = str(dofollow) if isinstance(dofollow, str) else str(dofollow)
    return _DEFAULT_WEIGHTS.get(key, 1.0)


def _url_hash(url: str) -> str:
    normalized = url.strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _make_score_id(target_url: str, channel: str) -> str:
    return f"{_url_hash(target_url)}:{channel}"


class ScoreStore(JsonStore):
    """JSON store for per-publish score records."""

    def __init__(self, path) -> None:
        super().__init__(path, default_factory=dict)

    # ── Record helpers ─────────────────────────────────────────────────

    def record_publish(
        self,
        target_url: str,
        channel: str,
        platform_weight: float | None = None,
        dofollow_multiplier: float | None = None,
    ) -> str:
        """Record a score for a successful publish. Returns ``score_id``."""
        score_id = _make_score_id(target_url, channel)
        now = datetime.now(timezone.utc).isoformat()
        score_val = compute_score(
            platform_weight=platform_weight,
            dofollow_multiplier=dofollow_multiplier,
        )

        def _upsert(data: dict) -> dict:
            data[score_id] = {
                "score_id": score_id,
                "target_url": target_url,
                "target_url_hash": _url_hash(target_url),
                "channel": channel,
                "platform_weight": platform_weight if platform_weight is not None else 1.0,
                "dofollow_multiplier": dofollow_multiplier if dofollow_multiplier is not None else 1.0,
                "survival_bonus": 1.0,
                "score": score_val,
                "base_score": 1.0,
                "published_at": now,
                "rechecked_at": None,
                "status": "initial",
            }
            return data

        self.update(_upsert)
        return score_id

    def update_survival(self, score_id: str, alive: bool) -> float | None:
        """Update survival bonus after a recheck. Returns the new score or None."""

        def _update(data: dict) -> dict:
            rec = data.get(score_id)
            if rec is None:
                return data
            rec["survival_bonus"] = 1.2 if alive else 0.0
            rec["status"] = "survival_confirmed" if alive else "survival_lost"
            rec["rechecked_at"] = datetime.now(timezone.utc).isoformat()
            rec["score"] = compute_score(
                platform_weight=rec["platform_weight"],
                dofollow_multiplier=rec["dofollow_multiplier"],
                survival_bonus=rec["survival_bonus"],
                base=rec["base_score"],
            )
            return data

        result = self.update(_update)
        rec = result.get(score_id)
        return rec["score"] if rec else None

    # ── Aggregation helpers ────────────────────────────────────────────

    def get_total_score(self) -> float:
        """Sum of all score records."""
        data = self.load()
        return sum(r["score"] for r in data.values())

    def get_channel_breakdown(self) -> dict[str, dict[str, float | int]]:
        """Per-channel aggregates: count, total."""
        data = self.load()
        breakdown: dict[str, dict[str, float | int]] = {}
        for rec in data.values():
            ch = rec["channel"]
            if ch not in breakdown:
                breakdown[ch] = {"count": 0, "total": 0.0}
            breakdown[ch]["count"] += 1
            breakdown[ch]["total"] += rec["score"]
        return breakdown

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Most recently published score records."""
        data = self.load()
        sorted_recs = sorted(
            data.values(),
            key=lambda r: r["published_at"],
            reverse=True,
        )
        return sorted_recs[:limit]

    # ── Backfill ────────────────────────────────────────────────────────

    def backfill_from_history(
        self,
        history_store,
        platform_weight_fn=None,
        dofollow_mult_fn=None,
    ) -> int:
        """Scan *history_store* for successful publishes and record scores.

        Skips already-recorded (score_id exists). Returns count of new
        records created.
        """
        count = 0
        existing = self.load()

        for item in history_store.load():
            target_url = item.get("target_url", "")
            channel = item.get("channel", "") or item.get("platform", "")
            if not target_url or not channel:
                continue

            score_id = _make_score_id(target_url, channel)
            if score_id in existing:
                continue

            status = item.get("status", "")
            if status not in ("published", "drafted"):
                continue

            pw = (platform_weight_fn(channel) if platform_weight_fn else 1.0)
            dm = (dofollow_mult_fn(channel) if dofollow_mult_fn else 1.0)
            self.record_publish(target_url, channel, pw, dm)
            count += 1

        return count
