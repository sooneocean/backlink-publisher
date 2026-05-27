"""Cross-run publish idempotency.

An authoritative, durable, ACID dedup record keyed on
``(platform, account, canonicalized target_url)`` that gates the publish path so
the same logical backlink is not re-published across resumes, crashes, or
separate re-runs of the same plan.

Plan: ``docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md``
"""

from __future__ import annotations

from .store import (
    DedupKey,
    DedupRecord,
    DedupStore,
    IntentOutcome,
    State,
)

__all__ = [
    "DedupKey",
    "DedupRecord",
    "DedupStore",
    "IntentOutcome",
    "State",
]
