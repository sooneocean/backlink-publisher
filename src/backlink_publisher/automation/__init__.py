"""Automation orchestrator package — full-automation runbook (Plan 2026-06-07).

Pipeline orchestration layer with health-aware gating, auto-recovery,
and observability integration.
"""

from .orchestrator import main

__all__ = ["main"]