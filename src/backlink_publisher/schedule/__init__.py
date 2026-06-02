"""Schedule package — decoupled from APScheduler for testability."""

from __future__ import annotations

from .engine import calc_next_available

__all__ = ["calc_next_available"]
