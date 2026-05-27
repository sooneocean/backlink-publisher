"""Comment Outreach Queue — find, qualify, draft, and track manual comment
opportunities on already-indexed public pages.

This module is deliberately isolated from the publishing adapter registry and
never posts comments, automates login, or touches ``events.db``. It only
produces a qualified review queue and conservative draft suggestions for a
human to act on. See ``docs/plans/2026-05-27-005-feat-comment-outreach-queue-plan.md``.
"""

from __future__ import annotations

__all__: list[str] = []
