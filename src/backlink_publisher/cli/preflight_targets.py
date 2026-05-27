"""preflight-targets — read-only destination-page health check.

Reads a ``plan-backlinks`` JSONL, dedupes each row's ``target_url`` (canonical
form), fetches each distinct target once via
``content._preflight_fetch.fetch_target``, and emits a per-target JSONL
"receipt" describing whether the destination is reachable and not-obviously-dead
(reachability / redirect / noindex / soft-404 / title+h1). stdout = JSONL
receipts, stderr = a RECON summary, **always exit 0** — it is a diagnostic, not
a gate. Plan 2026-05-26-008.

Deliberately NOT an indexability oracle: the checks are necessary-not-sufficient
(canonical tags, robots.txt, JS-rendered noindex, crawl budget are NOT checked).
A ``healthy`` verdict asserts only "reachable + not obviously dead." The receipt
``final_url`` is recorded as-fetched and is NOT a safety attestation.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from backlink_publisher._util.errors import PipelineError, UsageError, handle_error
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import PipelineLogger, set_log_level
from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.content._preflight_fetch import PreflightFacts, fetch_target

preflight_logger = PipelineLogger("preflight-targets")

_LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}

#: All verdicts the ladder can emit (kept explicit for the summary + tests).
VERDICTS = ("healthy", "not-healthy", "redirected-offsite", "unreachable", "ssrf_blocked", "unknown")

# Reason strings that mean "the network couldn't be completed" → unreachable.
_UNREACHABLE_REASONS = {"unreachable", "timeout", "network_error"}


def _failed_checks(f: PreflightFacts) -> list[str]:
    """The FULL set of failing checks (not first-match), for operator triage."""
    fc: list[str] = []
    if f.reason == "ssrf_blocked":
        fc.append("ssrf_blocked")
    if f.reason in _UNREACHABLE_REASONS:
        fc.append(f.reason)
    if f.reason == "invalid_url":
        fc.append("invalid_url")
    if f.tls_unverified:
        fc.append("tls_unverified")
    if f.redirect_capped:
        fc.append("redirect_capped")
    if f.status is not None and f.status != 200:
        fc.append(f"http_{f.status}")
    if f.host_diff:
        fc.append("redirected_offsite")
    if f.noindex:
        fc.append("noindex")
    if f.soft404:
        fc.append("soft_404")
    if f.status == 200 and not f.has_title:
        fc.append("no_title")
    if f.status == 200 and not f.has_h1:
        fc.append("no_h1")
    return fc


def _classify(f: PreflightFacts) -> str:
    """Map facts to a verdict via the R5b precedence ladder (first match wins).

    ssrf_blocked > unreachable > non-200 not-healthy > redirected-offsite (200,
    host differs) > unknown (unparseable) > not-healthy (200 same-host but
    soft-404 / noindex / missing title|h1 / tls / redirect-capped) > healthy.
    """
    r = f.reason
    if r == "ssrf_blocked":
        return "ssrf_blocked"
    if r in _UNREACHABLE_REASONS:
        return "unreachable"
    # Structural / transport failures on a present-but-bad target.
    if r in ("invalid_url", "tls_unverified", "redirect_capped"):
        return "not-healthy"
    if f.status is not None and f.status != 200:
        return "not-healthy"
    if f.status == 200:
        if f.host_diff:
            return "redirected-offsite"
        if f.soft404 or f.noindex or not f.has_title or not f.has_h1:
            return "not-healthy"
        return "healthy"
    # Fail-closed: any fact combination the ladder didn't recognize is surfaced
    # as a visible ``unknown`` (never silently dropped or defaulted to healthy).
    return "unknown"


def _build_receipt(target_url: str, facts: PreflightFacts, source_rows: list[int]) -> dict[str, Any]:
    """The single canonical serializer — every fact maps to one receipt key."""
    return {
        "target_url": target_url,
        "verdict": _classify(facts),
        "failed_checks": _failed_checks(facts),
        "final_url": facts.final_url,  # as-fetched, unverified — not a safety attestation
        "redirected": facts.redirected,
        "host_diff": facts.host_diff,
        "redirect_capped": facts.redirect_capped,
        "noindex": facts.noindex,
        "soft404": facts.soft404,
        "has_title": facts.has_title,
        "has_h1": facts.has_h1,
        "tls_unverified": facts.tls_unverified,
        "status": facts.status,
        "x_robots_tag": facts.x_robots_tag,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source_rows": source_rows,  # fan-out: 1-based plan row indices pointing here
    }


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="preflight-targets",
        description=(
            "Read-only destination-page preflight: fetch each plan row's "
            "target_url once and report reachability/redirect/noindex/soft-404/"
            "title+h1. JSONL receipts on stdout; RECON summary on stderr; always "
            "exit 0. NOT an indexability oracle."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input plan JSONL (default: stdin)",
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        metavar="LEVEL",
        help="Log verbosity: DEBUG|INFO|WARN|ERROR (default: WARN)",
    )
    args = parser.parse_args(argv)

    try:
        # Closed-set validation post-parse (repo convention: UsageError exit 1,
        # not argparse's exit 2). See [[argparse-choices-vs-usage-error]].
        if args.log_level not in _LOG_LEVELS:
            raise UsageError(
                f"preflight-targets: --log-level must be one of {sorted(_LOG_LEVELS)}; "
                f"got {args.log_level!r}"
            )
        set_log_level(args.log_level)

        rows = list(read_jsonl(args.input))

        # Dedupe by canonical target_url; preserve fan-out (which rows point here).
        targets: dict[str, dict[str, Any]] = {}
        skipped = 0
        for idx, row in enumerate(rows, start=1):
            raw = row.get("target_url", "")
            if not isinstance(raw, str) or not raw.strip():
                skipped += 1
                continue
            target = raw.strip()
            canon = canonicalize_url(target)
            entry = targets.setdefault(canon, {"url": target, "rows": []})
            entry["rows"].append(idx)

        receipts: list[dict[str, Any]] = []
        counts = {v: 0 for v in VERDICTS}
        for entry in targets.values():
            facts = fetch_target(entry["url"])
            receipt = _build_receipt(entry["url"], facts, entry["rows"])
            receipts.append(receipt)
            counts[receipt["verdict"]] += 1
            if receipt["verdict"] == "unknown":
                # Fail-closed tripwire: an unparseable response must be loud.
                preflight_logger.recon(
                    "preflight_unknown_verdict",
                    target=entry["url"],
                    status=facts.status,
                    reason=facts.reason,
                )

        write_jsonl(receipts)

        # Always-on RECON summary (bypasses --log-level; stripped by tests'
        # _stderr_without_warnings filter, so it doesn't break existing assertions).
        preflight_logger.recon(
            "preflight_summary",
            checked=len(receipts),
            skipped_no_target=skipped,
            verdicts=counts,
        )
        # No SystemExit: verdicts are data, not process failures (always exit 0).
    except PipelineError as exc:
        handle_error(exc)


if __name__ == "__main__":
    main()
