"""GEO / AI-citation probing (Plan 2026-05-29-006).

This package houses the operator-invoked citation-probe seam: a
dispatch-by-name engine selector (``engines.dispatch_probe``) and the v1
Perplexity adapter (``perplexity.probe_perplexity``).

Design notes:
- **D1** — engines are selected through a plain module-level dict, NOT a
  ``register_probe()`` registry. A formal registry for one engine is
  speculative; dispatch-by-name keeps the "add Gemini with zero architecture
  change" goal without the ceremony, and stays decoupled from the publish
  registry (which mandates ``dofollow=``).
- **D8** — ``ProbeResult.raw_response`` is kept IN MEMORY ONLY. It is never
  persisted (upstream error bodies can echo ``Authorization``/``Bearer`` and
  answer text can carry PII). Only parsed, bounded fields are appended to
  ``events.db`` downstream (U2).
- **D9** — the adapter reuses ``generate-backlink-text``'s full credential
  guard chain (userinfo reject → normalize base → ``guard_llm_endpoint``)
  before any Bearer-carrying network call.
"""

from __future__ import annotations

from .engines import ProbeResult, dispatch_probe

__all__ = ["ProbeResult", "dispatch_probe"]
