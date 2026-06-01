"""GEO probe-engine dispatch-by-name (Plan 2026-05-29-006 Unit 3, D1).

A probe engine takes ``(query, cfg)`` and returns a :class:`ProbeResult`. The
engine seam is a **plain module-level dict**, not a ``register_probe()``-
populated registry (D1/SG1): the publish ``register()`` mandates ``dofollow=``
and publish-specific kwargs (coupling unrelated contracts), and a formal
registry for v1's single engine is speculative ceremony. Dispatch-by-name keeps
the "add Gemini with zero architecture change" goal — a second engine is one
dict entry.

``raw_response`` is kept IN MEMORY ONLY (D8) — never persisted downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backlink_publisher._util.errors import UsageError
from backlink_publisher.config.types import GeoProbeConfig

#: Allowed ``ProbeResult.outcome`` values. ``ok`` = answer with parsed source
#: URLs; ``refused`` = a recognizable engine refusal (NOT an error); ``absent``
#: = answered but no creditable citations / empty content; ``parse_error`` =
#: the response could not be parsed (malformed/oversize) — kept as a distinct
#: outcome so the caller can tell "engine answered nothing" from "we could not
#: read the engine's answer". None of these raise out of the adapter.
PROBE_OUTCOMES: frozenset[str] = frozenset(
    {"ok", "refused", "absent", "parse_error"}
)


@dataclass
class ProbeResult:
    """One engine probe outcome.

    ``raw_response`` holds the parsed upstream JSON (or any debug object) and is
    **in-memory only** (D8): callers must never serialize it into ``events.db``
    or any other at-rest store, because upstream error bodies can echo
    ``Authorization``/``Bearer`` headers and answer/query text can carry PII.
    Only the bounded parsed fields (``answer_text``, ``source_urls``,
    ``outcome``) are safe to persist.
    """

    answer_text: str
    source_urls: list[str]
    raw_response: Any  # in-memory only — see D8; never persisted.
    outcome: str  # one of PROBE_OUTCOMES

    def __post_init__(self) -> None:
        if self.outcome not in PROBE_OUTCOMES:
            # Programmer error inside an adapter — surface loudly in tests, not
            # to the operator mid-batch.
            raise ValueError(
                f"ProbeResult.outcome must be one of "
                f"{sorted(PROBE_OUTCOMES)}, got {self.outcome!r}"
            )


#: A probe engine: ``(query, cfg) -> ProbeResult``. Lazy import of the adapter
#: avoids importing ``requests`` at package-import time for callers that only
#: need the dispatch surface (e.g. ``--dry-run``).
ProbeEngine = Callable[[str, GeoProbeConfig], ProbeResult]


def _perplexity(query: str, cfg: GeoProbeConfig) -> ProbeResult:
    # Imported lazily so the adapter's ``requests`` dependency is only loaded on
    # an actual probe call, mirroring the rest of the codebase's lazy-import
    # convention inside CLI/engine seams.
    from .perplexity import probe_perplexity

    return probe_perplexity(query, cfg)


#: The dispatch table. One entry per supported engine (v1 = Perplexity only).
#: Adding Gemini/ChatGPT later is a single line here + a new adapter module.
_ENGINES: dict[str, ProbeEngine] = {
    "perplexity": _perplexity,
}


def known_engines() -> tuple[str, ...]:
    """Return the sorted tuple of dispatchable engine names (for error text)."""
    return tuple(sorted(_ENGINES))


def dispatch_probe(
    engine_name: str, query: str, cfg: GeoProbeConfig
) -> ProbeResult:
    """Route ``query`` to the named engine adapter.

    Unknown engine → :class:`UsageError` (exit 1). This is a *closed-set
    operator argument* error — the operator named an engine the build does not
    ship — which is the same class the codebase uses for closed-set CLI args
    (``argparse-choices-vs-usage-error`` learning: post-parse ``UsageError``,
    never ``choices=``). It is NOT a ``DependencyError`` (exit 3, "go install /
    re-bind something") because no external precondition is missing — the name
    is simply not a member of the dispatch set.
    """
    engine = _ENGINES.get(engine_name)
    if engine is None:
        raise UsageError(
            f"unknown GEO probe engine {engine_name!r}; "
            f"supported engines: {', '.join(known_engines())}"
        )
    return engine(query, cfg)
