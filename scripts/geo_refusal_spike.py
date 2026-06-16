#!/usr/bin/env python3
"""GEO citation refusal-spike — feature-level value gate (Plan 2026-05-29-006 U4).

When you need this
------------------
This is the **go/no-go pre-flight for the whole GEO measurement half** (Phase B
of the plan), run BEFORE building or enabling any production probing. It answers
one question per target and one for the feature as a whole:

  *Does an AI answer engine (Perplexity) actually cite this target's site when
  answering about its topic?*

A target that the engine either (a) refuses to answer about, or (b) happily
answers about but **never cites**, is worthless for GEO measurement. For an
adult / ACG portfolio the realistic outcome is "answered but never cited", and
if EVERY target lands there the eligible set is empty — at which point the plan
says ship Phase C (deterministic content levers) only and defer Phase B (review
A1 / D12).

What it measures (review A2 — CITATION refusal, not GENERATION refusal)
-----------------------------------------------------------------------
Only the Markdown-table + exit-code SHAPE is shared with
``scripts/llm_rejection_spike.py``; the SEMANTICS are different. That script
measures whether an LLM will *generate* anchor text (content generation). This
script measures whether an AI answer engine will *cite* a target site
(answer/citation behavior). A target Perplexity answers-about-but-never-cites is
exactly as worthless for GEO as one it refuses outright — both are excluded.

Each ``(target, query)`` probe is classified into THREE buckets:

- ``refused`` — ``ProbeResult.outcome == "refused"``, OR a transport / auth /
  adapter error (which this script counts as a refusal and never lets propagate
  — see the never-raises note below).
- ``absent`` — the engine answered, but the target site is NOT among the
  returned ``source_urls``.
- ``cited`` — the engine answered AND the target host appears in
  ``source_urls``.

NOTE on crediting: this spike uses a deliberately SIMPLE host-match via
``canonicalize_url`` (compare canonical hosts). The full hallucination-resistant
credit gate (redirect unwrapping, article-path joins, ``possibly_cited_unresolved``
bucketing) is Unit 5 — this pre-flight only needs a coarse "did the host show up
at all" signal to make the go/no-go call.

Verdict logic
-------------
Per target (A2): a target is **excluded** when EITHER
  - the refused-rate over its probes is high (>= ``--refused-threshold``), OR
  - the cited-rate among its ANSWERED (non-refused) probes is ~0
    (<= ``--cited-floor``) — answering-but-never-citing is equally worthless.
Otherwise it is **included** (GEO-eligible).

Aggregate (D12 — feature-level value gate): if the set of GEO-eligible
(non-excluded) targets is empty, the overall verdict recommends shipping Phase C
(deterministic levers) only and deferring Phase B (measurement). The exit code
mirrors ``llm_rejection_spike.py``'s threshold/exit convention and stays within
the project's 0-6 exit-code contract:

  - exit 0 — a viable GEO-eligible target set exists (build/enable Phase B).
  - exit 1 — the eligible set is empty/near-empty (ship Phase C only, defer B).

(exit 1 here is the same advisory "gate did not pass" sense as
``llm_rejection_spike.py`` exiting 1 when its rejection rate exceeds threshold —
not an ``UsageError``; missing config still exits 2 below.)

Usage
-----
    # one-off run with your normal config (requires [geo.probe_provider])
    python scripts/geo_refusal_spike.py

    # provide the GEO key via env var instead of config.toml
    BACKLINK_GEO_API_KEY=... python scripts/geo_refusal_spike.py

    # tighter or looser per-target gates
    python scripts/geo_refusal_spike.py --refused-threshold 0.4 --cited-floor 0.0
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make the package importable when running the script directly from the repo.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from backlink_publisher._util.url import (  # noqa: E402
    canonicalize_url,
    safe_hostname,
)
from backlink_publisher._util.errors import PipelineError  # noqa: E402
from backlink_publisher.config import load_config  # noqa: E402
from backlink_publisher.config.types import Config, GeoProbeConfig  # noqa: E402
from backlink_publisher.geo import dispatch_probe  # noqa: E402

# v1 ships a single engine; the dispatch seam (U3) routes by name.
_ENGINE = "perplexity"

# Default per-target gates. A target is excluded when its refused-rate meets/
# exceeds _DEFAULT_REFUSED_THRESHOLD, OR its cited-rate among answered probes is
# at/below _DEFAULT_CITED_FLOOR (answering-but-never-citing is worthless — A2).
_DEFAULT_REFUSED_THRESHOLD = 0.5
_DEFAULT_CITED_FLOOR = 0.0


def _canonical_host(url: str) -> str | None:
    """Return the canonical (lowercased, www-agnostic) host of ``url`` or None.

    Uses ``canonicalize_url`` for normalization (scheme/host lowercasing, port
    and utm stripping) then extracts the host. ``www.`` is folded so that
    ``https://www.example.com`` and ``https://example.com`` host-match — this is
    the SIMPLE host-match the spike relies on; the full credit gate is U5.
    """
    if not url:
        return None
    host = safe_hostname(canonicalize_url(url))
    if not host:
        return None
    # Explicit str binding so mypy doesn't widen the Any from safe_hostname
    # back through the return (no-any-return).
    normalized: str = host.lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


@dataclass
class ProbeOutcome:
    """One ``(target, query)`` probe classified into a single bucket."""

    query: str
    bucket: str  # one of "refused" / "absent" / "cited"
    detail: str  # short tag for the table (refusal reason / matched host / etc.)


@dataclass
class TargetReport:
    """Per-target aggregate over its query probes + exclude/include verdict."""

    target: str  # main_domain key (trailing slash stripped)
    outcomes: list[ProbeOutcome] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def refused(self) -> int:
        return sum(1 for o in self.outcomes if o.bucket == "refused")

    @property
    def absent(self) -> int:
        return sum(1 for o in self.outcomes if o.bucket == "absent")

    @property
    def cited(self) -> int:
        return sum(1 for o in self.outcomes if o.bucket == "cited")

    @property
    def answered(self) -> int:
        """Probes the engine actually answered (absent + cited; not refused)."""
        return self.absent + self.cited

    @property
    def refused_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.refused / self.total

    @property
    def cited_rate_answered(self) -> float:
        """Cited-rate among ANSWERED probes (A2). 0.0 when nothing answered."""
        if self.answered == 0:
            return 0.0
        return self.cited / self.answered

    def verdict(self, refused_threshold: float, cited_floor: float) -> tuple[bool, str]:
        """Return ``(excluded, reason)``.

        Excluded when EITHER the refused-rate is high OR the cited-rate among
        answered probes is at/below the floor (review A2). A target with no
        configured queries is excluded as "no probes" — it cannot be shown
        eligible.
        """
        if self.total == 0:
            return True, "no probe queries configured"
        if self.refused_rate >= refused_threshold:
            return (
                True,
                f"high refusal ({self.refused}/{self.total} = "
                f"{self.refused_rate * 100:.0f}%)",
            )
        if self.cited_rate_answered <= cited_floor:
            # Answered-about but never (or barely) cited — worthless for GEO.
            return (
                True,
                f"never cited ({self.cited}/{self.answered} answered cited)",
            )
        return (
            False,
            f"cited {self.cited}/{self.answered} answered "
            f"({self.cited_rate_answered * 100:.0f}%)",
        )


def _classify(
    result_outcome: str, source_urls: list[str], target_host: str | None
) -> tuple[str, str]:
    """Map a ProbeResult into a ``(bucket, detail)`` for one probe.

    SIMPLE host-match only (full credit gate = U5): a probe is ``cited`` when
    the target host equals the canonical host of any returned source URL.
    """
    if result_outcome == "refused":
        return "refused", "engine refused"
    # Treat parse_error as a non-answer for citation purposes — the engine gave
    # us nothing creditable. It is NOT a refusal (the engine did respond), so it
    # falls into "absent" (answered-with-no-creditable-cite) for rate purposes.
    if target_host is not None:
        for url in source_urls:
            if _canonical_host(url) == target_host:
                return "cited", f"cited via {url}"
    return "absent", "answered, target not cited"


def _run_one(
    target: str, query: str, target_host: str | None, cfg: GeoProbeConfig
) -> ProbeOutcome:
    """Issue one probe and classify it. NEVER raises — an adapter exception for
    one (target, query) is counted as ``refused`` so the spike completes all
    probes (plan never-raises requirement).
    """
    try:
        result = dispatch_probe(_ENGINE, query, cfg)
    except Exception as exc:  # noqa: BLE001 — intentional: count, never propagate.
        return ProbeOutcome(
            query=query,
            bucket="refused",
            detail=f"error:{type(exc).__name__}",
        )
    bucket, detail = _classify(result.outcome, result.source_urls, target_host)
    return ProbeOutcome(query=query, bucket=bucket, detail=detail)


def _print_results(
    reports: list[TargetReport],
    refused_threshold: float,
    cited_floor: float,
) -> int:
    """Format the spike as Markdown; return the process exit code (0-6 contract).

    exit 0 — a viable GEO-eligible target set exists.
    exit 1 — the eligible set is empty/near-empty → Phase-C-only verdict (D12).
    """
    eligible: list[str] = []
    excluded: list[tuple[str, str]] = []
    for r in reports:
        is_excluded, reason = r.verdict(refused_threshold, cited_floor)
        if is_excluded:
            excluded.append((r.target, reason))
        else:
            eligible.append(r.target)

    has_viable_set = len(eligible) > 0

    print("# GEO citation refusal-spike — feature-level value gate")
    print()
    print(f"- **Engine**: {_ENGINE}")
    print(f"- **Targets probed**: {len(reports)}")
    print(f"- **GEO-eligible (included)**: {len(eligible)}")
    print(f"- **Excluded**: {len(excluded)}")
    print(
        f"- **Per-target gates**: exclude if refused-rate >= "
        f"{refused_threshold * 100:.0f}% OR cited-rate-among-answered <= "
        f"{cited_floor * 100:.0f}%"
    )
    print()

    # ── Aggregate D12 verdict ────────────────────────────────────────────────
    if has_viable_set:
        print(
            "- **Aggregate verdict**: ✅ BUILD/ENABLE Phase B — a viable "
            "GEO-eligible target set exists: "
            + ", ".join(eligible)
        )
    else:
        print(
            "- **Aggregate verdict**: ❌ SHIP PHASE C ONLY (deterministic "
            "levers) + runbook; DEFER Phase B (measurement). No target is "
            "GEO-eligible (every target is refused or never-cited) — building "
            "the measurement half would measure nothing (D12 / review A1)."
        )
    print()

    # ── Per-target table ─────────────────────────────────────────────────────
    print("| target | refused | absent | cited | answered | cited-rate (answered) | verdict |")
    print("|---|---|---|---|---|---|---|")
    for r in reports:
        is_excluded, reason = r.verdict(refused_threshold, cited_floor)
        status = "❌ excluded" if is_excluded else "✅ included"
        cited_rate = (
            f"{r.cited_rate_answered * 100:.0f}%" if r.answered else "n/a"
        )
        print(
            f"| {r.target} | {r.refused} | {r.absent} | {r.cited} | "
            f"{r.answered} | {cited_rate} | {status}: {reason} |"
        )
    print()

    # ── Per-probe detail (one block per target) ──────────────────────────────
    for r in reports:
        print(f"## {r.target}")
        if r.total == 0:
            print()
            print("_No probe queries configured for this target._")
            print()
            continue
        print()
        print("| # | query | bucket | detail |")
        print("|---|---|---|---|")
        for i, o in enumerate(r.outcomes, start=1):
            query = o.query if len(o.query) <= 50 else o.query[:47] + "…"
            detail = o.detail if len(o.detail) <= 50 else o.detail[:47] + "…"
            print(f"| {i} | {query} | {o.bucket} | {detail} |")
        print()

    return 0 if has_viable_set else 1


def _build_reports(config: Config) -> list[TargetReport]:
    """Probe every configured target that has ``probe_queries`` and aggregate.

    Returns a per-target report. A target whose query list is empty still gets a
    report (with zero outcomes) so the table/verdict can flag "no probes".
    """
    # main() exits 2 before calling this when the provider is unset, so it is
    # non-None here; assert to document the invariant and narrow for mypy.
    cfg = config.geo_probe_provider
    assert cfg is not None
    reports: list[TargetReport] = []
    for target, queries in sorted(config.target_probe_queries.items()):
        report = TargetReport(target=target)
        target_host = _canonical_host(target)
        for query in queries:
            report.outcomes.append(
                _run_one(target, query, target_host, cfg)
            )
        reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="geo_refusal_spike",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.toml (default: ~/.config/backlink-publisher/config.toml)",
    )
    parser.add_argument(
        "--refused-threshold",
        type=float,
        default=_DEFAULT_REFUSED_THRESHOLD,
        metavar="FLOAT",
        help=(
            "Per-target: exclude when refused-rate >= this, 0-1 "
            f"(default: {_DEFAULT_REFUSED_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--cited-floor",
        type=float,
        default=_DEFAULT_CITED_FLOOR,
        metavar="FLOAT",
        help=(
            "Per-target: exclude when cited-rate-among-answered <= this, 0-1 "
            f"(default: {_DEFAULT_CITED_FLOOR})"
        ),
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except PipelineError as exc:
        # Honor the documented 0-6 exit-code contract instead of crashing with a
        # traceback on a malformed/invalid config (e.g. bad TOML -> exit 3,
        # invalid [geo.probe_provider] -> exit 2).
        print(f"ERROR: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    if config.geo_probe_provider is None:
        print(
            "ERROR: no GEO probe provider configured. Set [geo.probe_provider] "
            "in config.toml (base_url + model) and supply the GEO key via the "
            "BACKLINK_GEO_API_KEY env var or the toml value. GEO probing is "
            "operator-invoked — the rest of the tool runs fine without it.",
            file=sys.stderr,
        )
        return 2

    if not config.target_probe_queries:
        print(
            "ERROR: no target has [targets.\"<main_domain>\"].probe_queries "
            "configured. Add probe_queries to at least one target to run the "
            "citation spike.",
            file=sys.stderr,
        )
        return 2

    reports = _build_reports(config)
    return _print_results(reports, args.refused_threshold, args.cited_floor)


if __name__ == "__main__":
    sys.exit(main())
