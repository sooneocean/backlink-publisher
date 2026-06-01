#!/usr/bin/env python3
"""Anti-redrift guard — flag plan-docs that shipped but were never marked done.

The complement of ``plan-check``. ``plan-check`` *passes* when a plan-doc's
declared ``claims:`` (paths/shas) all resolve on ``origin/main``. This guard
*flags* the same resolution **when the status still says the work is in
progress** (``active`` / ``ready``): if every claimed artifact is already on
``main`` yet the status was never advanced to a terminal value, the plan has
drifted — the work shipped silently and the tracking lies.

This is the exact failure mode behind the 2026-06-01 convergence audit, where
14+ ``active`` plans turned out already merged.

Detection (low false-positive by construction):
  status ∈ {active, ready}  AND  claims has paths/shas  AND  every path/sha
  resolves on origin/main   →   REDRIFT.

Blind spot (documented, accepted): ``claims: {}`` is the explicit opt-out and
declares no artifacts, so drift in those plans cannot be detected — they
self-exempt. The forward ``plan-check`` / radar handles the missing-claims and
false-claims cases; this guard only adds the inverse signal for plans that
*did* declare their artifacts. Pre-cutoff (grandfathered) plans are skipped.

Usage:
  check_plan_redrift.py PATH [PATH ...]   # check specific plan-docs (PR gate)
  check_plan_redrift.py --all             # scan docs/plans/**/*.md (radar)
  --advisory                              # always exit 0 (report-only)

Exit 0 = no redrift (or advisory); 1 = redrift found; 2 = usage error.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from backlink_publisher.cli._plan_check_git import (
    _maybe_fetch_origin_main,
    _path_exists_on_main,
    _sha_reachable_from_main,
)
from backlink_publisher.cli._plan_check_schema import (
    PlanClaimsFrontmatterSchemaError,
    PlanClaimsGlobUnsupported,
    PlanClaimsMissingOnPostCutoff,
    _grandfathered,
    _parse_frontmatter,
    _read_plan_text,
    _validate_claims_schema,
)

# Statuses that assert the work is NOT yet done. A plan in one of these whose
# claimed artifacts all already exist on main is drifted. Honest partial
# statuses (``partial``, ``phase1-complete``) are deliberately NOT here — they
# already tell the truth about being incomplete, so they must never be flagged.
IN_PROGRESS_STATUSES = {"active", "ready"}

PathResolver = Callable[[str], tuple]
ShaResolver = Callable[[str], tuple]


def detect_redrift(
    plan_path: Path,
    *,
    path_resolver: PathResolver = _path_exists_on_main,
    sha_resolver: ShaResolver = _sha_reachable_from_main,
) -> Optional[str]:
    """Return a one-line drift summary if *plan_path* is redrifted, else ``None``.

    Resolvers are injectable so unit tests can drive the decision matrix without
    a real git repo (the resolvers themselves are covered by plan-check's tests).

    Malformed frontmatter is NOT this guard's concern (the forward plan-check /
    radar flags it) -- such docs are skipped rather than crashing a full scan.
    """
    try:
        fm = _parse_frontmatter(_read_plan_text(plan_path))
    except PlanClaimsFrontmatterSchemaError:
        return None  # invalid YAML/frontmatter -- forward gate's job, skip here

    raw_status = fm.get("status")
    status = str(raw_status).strip().split()[0].lower() if raw_status else ""
    if status not in IN_PROGRESS_STATUSES:
        return None  # terminal or honest-partial status — nothing to flag

    if _grandfathered(fm):
        return None  # pre-cutoff plan — not in scope

    try:
        claims = _validate_claims_schema(fm)
    except (
        PlanClaimsMissingOnPostCutoff,
        PlanClaimsFrontmatterSchemaError,
        PlanClaimsGlobUnsupported,
    ):
        # Missing/invalid claims are the forward gate's job, not ours.
        return None

    if not claims.paths and not claims.shas:
        return None  # claims:{} opt-out — blind spot, self-exempt

    # Redrift iff EVERY declared artifact already resolves on main.
    for p in claims.paths:
        ok, _ = path_resolver(p)
        if not ok:
            return None  # at least one artifact missing → genuinely in progress
    for s in claims.shas:
        ok, _ = sha_resolver(s)
        if not ok:
            return None

    n = len(claims.paths) + len(claims.shas)
    return (
        f"status={status!r} but all {n} declared claim(s) already resolve on "
        f"origin/main — work shipped, status never advanced"
    )


def _iter_all_plan_docs() -> list[Path]:
    base = Path("docs/plans")
    return sorted(p for p in base.rglob("*.md") if p.is_file())


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="check_plan_redrift",
        description="Flag plan-docs that shipped (claims resolve) but stayed active/ready.",
    )
    parser.add_argument("paths", nargs="*", help="plan-doc paths to check")
    parser.add_argument("--all", action="store_true", help="scan docs/plans/**/*.md")
    parser.add_argument(
        "--advisory", action="store_true", help="always exit 0 (report-only)"
    )
    args = parser.parse_args(argv)

    if args.all and args.paths:
        print("error: pass either PATHs or --all, not both", file=sys.stderr)
        return 2
    targets = _iter_all_plan_docs() if args.all else [Path(p) for p in args.paths]
    if not targets:
        print("no plan-docs to check (no-op).")
        return 0

    # Refresh origin/main once; resolvers query it. No-op if recently fetched.
    _maybe_fetch_origin_main()

    drifted: list[tuple[Path, str]] = []
    for t in targets:
        if not t.is_file():
            print(f"warning: {t} is not a file — skipped", file=sys.stderr)
            continue
        summary = detect_redrift(t)
        if summary:
            drifted.append((t, summary))

    if drifted:
        print(f"::warning::{len(drifted)} plan-doc(s) drifted (shipped but not marked done):")
        for path, summary in drifted:
            print(f"  - {path}: {summary}")
        print(
            "\nFix: flip each plan's `status:` to completed/shipped (annotate the "
            "merge SHA/PR), or correct its claims if the work is not actually done."
        )
        return 0 if args.advisory else 1

    print(f"no redrift: {len(targets)} plan-doc(s) checked, all status-consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
