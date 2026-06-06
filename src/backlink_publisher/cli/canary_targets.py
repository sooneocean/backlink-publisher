"""canary-targets — read-only adapter-contract canary (Plan 2026-05-27-001 Unit 3).

For each *dofollow* cohort platform (``registry.dofollow_status(p) is True``),
re-fetch its configured ``[canary.<platform>]`` post, inspect whether the
target backlink's *own* anchor is still present + dofollow on a still-readable
page, classify via a STRICT three-state ladder, debounce-update the canary
health store, and emit one JSONL receipt per platform. stdout = JSONL receipts,
stderr = a RECON summary, **always exit 0** — it is an advisory diagnostic, not
a gate.

Input is **config-driven** (registry cohort + ``[canary.<platform>]`` config),
NOT a stdin JSONL spine: the preflight-targets template lends only its
receipt/verdict helper shape + exit-0/recon contract.

Honest boundary (``mode=evergreen``): this re-fetches a STATIC post published
under the *old* contract, so it catches a platform *retroactively* rewriting an
existing page's rel/noindex, but is structurally blind to *forward* publish-path
drift (auth/schema/selector breaking a NEW post). The verdict label is
``link-alive`` — never ``healthy`` — so green is never misread as "the publish
pipeline is fine." Not an indexability oracle (page-level noindex only,
necessary-not-sufficient).
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from typing import Any

from backlink_publisher._util.errors import PipelineError, UsageError, handle_error
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.logger import PipelineLogger, set_log_level
from backlink_publisher.canary.store import (
    STATUS_ADVISORY,
    STATUS_DRIFT_CONFIRMED,
    STATUS_LINK_ALIVE,
    STATUS_NOT_CONFIGURED,
    get_health,
    read_canary_config,
    record_verdict,
)
# Patched at this reference in tests (mock.patch path follows the function).
from backlink_publisher.content._preflight_fetch import fetch_target
from backlink_publisher.publishing.adapters.link_attr_verifier import inspect_target_anchor

canary_logger = PipelineLogger("canary-targets")

_LOG_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}

#: All verdicts the ladder can emit (kept explicit for the summary + tests).
VERDICTS = (STATUS_LINK_ALIVE, STATUS_DRIFT_CONFIRMED, STATUS_ADVISORY, STATUS_NOT_CONFIGURED)

#: Evergreen mode label — never "healthy"; see module docstring.
MODE = "evergreen"

#: Consecutive-advisory runs after which a canary post is presumed rotted and a
#: ``canary-stale/needs-reseed`` note is surfaced. v1 only has the minimal Unit 1
#: fields, so this thresholds on ``consecutive_failures`` is NOT available for
#: advisory (advisory preserves counters by design). TODO(Unit 4): track
#: consecutive-advisory in the health store; until then stale-detection is a
#: best-effort note keyed off whatever the store exposes.
_STALE_ADVISORY_RUNS = 3

#: Inter-platform jitter bounds (seconds). Kept tiny; zeroed in tests via the
#: monkeypatchable ``_sleep`` seam so the suite never actually sleeps.
_DELAY_MIN = 0.0
_DELAY_MAX = 2.0


def _sleep(seconds: float) -> None:
    """Monkeypatchable sleep seam (tests patch this to a no-op)."""
    import time

    if seconds > 0:
        time.sleep(seconds)


def _jittered_inter_platform_delay() -> None:
    """Light jittered delay between platforms (mirrors ``_sleep_with_throttle``
    spirit). Skippable: tests patch ``_sleep`` to a no-op so nothing blocks."""
    _sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))


def _failed_checks(facts: Any, anchor: dict[str, Any], configured: bool) -> list[str]:
    """The FULL set of failing checks (not first-match), for operator triage.

    Non-sensitive field names only (R15) — never credentials/HTML/query strings.
    """
    fc: list[str] = []
    if not configured:
        fc.append("not_configured")
        return fc

    # Transport / readability facts from _preflight_fetch.
    reason = getattr(facts, "reason", None)
    status = getattr(facts, "status", None)
    if reason == "ssrf_blocked":
        fc.append("ssrf_blocked")
    if reason in ("unreachable", "timeout", "network_error"):
        fc.append(reason)
    if reason == "invalid_url":
        fc.append("invalid_url")
    if getattr(facts, "soft404", False):
        fc.append("soft_404")
    if status is not None and status != 200:
        fc.append(f"http_{status}")
    if getattr(facts, "noindex", False):
        fc.append("noindex")

    # Anchor-inspection facts (target's OWN rel — never a page-wide aggregate).
    if not anchor.get("page_readable"):
        fc.append("page_not_readable")
    if anchor.get("marker_present") is False:
        fc.append("marker_absent")
    if anchor.get("target_anchor_found") is False:
        fc.append("target_anchor_missing")
    if anchor.get("target_is_nofollow"):
        fc.append("target_nofollow")
    return fc


def _classify(facts: Any, anchor: dict[str, Any], configured: bool) -> str:
    """Map (facts, anchor) to a verdict via a STRICT three-state ladder.

    First match wins:

    1. ``not-configured`` — no ``[canary.<platform>]`` entry (first-class
       verdict; loudly listed as a coverage gap, NOT advisory, NOT drift).
    2. ``drift-confirmed`` — HTTP 200 + page readable + marker present (proves
       this IS the canary page) + (target anchor gone OR target is nofollow).
       **marker-present + anchor gone/nofollow MUST land here, never advisory.**
    3. ``link-alive`` — 200 + readable + marker present + target anchor found +
       not nofollow + no noindex.
    4. ``advisory`` — everything else we could not cleanly read+confirm
       (soft404 / null / ssrf-blocked / non-200 / marker absent / page not
       readable / interstitial-unverifiable). NEVER quarantine.
    """
    if not configured:
        return STATUS_NOT_CONFIGURED

    status = getattr(facts, "status", None)
    page_readable = bool(anchor.get("page_readable"))
    marker_present = anchor.get("marker_present") is True
    anchor_found = bool(anchor.get("target_anchor_found"))
    is_nofollow = bool(anchor.get("target_is_nofollow"))
    noindex = bool(getattr(facts, "noindex", False))

    confirmed_canary_page = status == 200 and page_readable and marker_present

    # 2. STRONGEST drift signal: we PROVED this is the canary page (marker on a
    # readable 200) and the target anchor is gone or has flipped to nofollow.
    if confirmed_canary_page and (not anchor_found or is_nofollow):
        return STATUS_DRIFT_CONFIRMED

    # 3. Clean link-alive: page proven, anchor present, dofollow, indexable.
    if confirmed_canary_page and anchor_found and not is_nofollow and not noindex:
        return STATUS_LINK_ALIVE

    # 4. Everything else: couldn't cleanly read+confirm → advisory, never drift.
    return STATUS_ADVISORY


def _build_receipt(
    platform: str,
    verdict: str,
    failed_checks: list[str],
    *,
    stale: bool = False,
) -> dict[str, Any]:
    """The single canonical serializer — non-sensitive allowlisted fields ONLY.

    MUST NOT include credentials/tokens/cookies/raw HTML/credential-bearing
    query strings (R15). ``checked_at`` is point-in-time, not a durability
    claim.
    """
    receipt: dict[str, Any] = {
        "platform": platform,
        "verdict": verdict,
        "mode": MODE,  # evergreen — existing-link survival, NOT publish-path health
        "failed_checks": failed_checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),  # point-in-time only
    }
    if stale:
        receipt["note"] = "canary-stale/needs-reseed"
    return receipt


def _build_cohort(include_uncertain: bool = False) -> list[str]:
    """Cohort = registered platforms declared ``dofollow_status is True``.

    When *include_uncertain* is ``True``, platforms declared as ``"uncertain"``
    are also included — used by the Canary Blitz (``--include-uncertain``)
    to verify unresolved dofollow candidates via the same canary protocol.

    Imports the adapters package for its register() side effects so the
    registry is populated before we read it (mirrors validate-backlinks idiom).
    Deliberately uses ``dofollow_status is True`` by default — NOT
    ``referral_value`` (an orthogonal axis that is None for the dofollow tier
    → empty set, R5).
    """
    import backlink_publisher.publishing.adapters  # noqa: F401  populate registry
    from backlink_publisher.publishing import registry

    return [
        p
        for p in registry.registered_platforms()
        if registry.dofollow_status(p) is True
        or (include_uncertain and registry.dofollow_status(p) == "uncertain")
    ]


def _is_stale(platform: str, verdict: str) -> bool:
    """Best-effort canary-stale detection.

    v1 health store carries only minimal fields (Unit 1); it does NOT track
    consecutive-advisory runs (advisory preserves counters by design). So we can
    only flag stale when the store ALREADY reflects a long advisory streak via
    a field it exposes. Until Unit 4 adds ``consecutive_advisory`` tracking,
    this returns False for the advisory path and is a documented gap.
    TODO(Unit 4): wire consecutive-advisory tracking into the store and
    threshold on ``_STALE_ADVISORY_RUNS`` here.
    """
    return False


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="canary-targets",
        description=(
            "Read-only adapter-contract canary: re-fetch each dofollow cohort "
            "platform's configured canary post and assert the target backlink's "
            "own anchor is still present + dofollow on a readable page. JSONL "
            "receipts on stdout; RECON summary on stderr; always exit 0. "
            "mode=evergreen (existing-link survival, NOT publish-path health); "
            "NOT an indexability oracle. Use --include-uncertain for the Canary "
            "Blitz (probe uncertain-dofollow platforms)."
        ),
    )
    parser.add_argument(
        "--platform",
        default=None,
        metavar="NAME",
        help="Limit the run to a single cohort platform (default: all).",
    )
    parser.add_argument(
        "--include-uncertain",
        action="store_true",
        default=False,
        help="Also check platforms with dofollow_status='uncertain' (Canary Blitz).",
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
                f"canary-targets: --log-level must be one of {sorted(_LOG_LEVELS)}; "
                f"got {args.log_level!r}"
            )
        set_log_level(args.log_level)

        cohort = _build_cohort(include_uncertain=args.include_uncertain)

        # R6 fail-loud: an empty cohort means the dofollow predicate regressed
        # and silently disabled the canary — surface it, never run empty.
        if not cohort:
            hint = ""
            if not args.include_uncertain:
                hint = (
                    " Try --include-uncertain if you want to probe uncertain "
                    "platforms (Canary Blitz)."
                )
            raise UsageError(
                "canary-targets: dofollow cohort is empty — no platform has "
                "dofollow_status is True." + hint + (
                " The canary would silently cover nothing. Check the adapter "
                "registry / dofollow declarations."
            ))

        if args.platform is not None:
            if args.platform not in cohort:
                hint = (
                    " (use --include-uncertain if it is registered as uncertain)"
                    if not args.include_uncertain
                    else ""
                )
                raise UsageError(
                    f"canary-targets: --platform {args.platform!r} is not in the "
                    f"cohort {sorted(cohort)}.{hint}"
                )
            cohort = [args.platform]

        receipts: list[dict[str, Any]] = []
        counts = {v: 0 for v in VERDICTS}
        not_configured: list[str] = []
        stale: list[str] = []

        for i, platform in enumerate(cohort):
            if i > 0:
                _jittered_inter_platform_delay()

            cfg = read_canary_config(platform)
            if cfg is None:
                # First-class not-configured verdict — loudly listed as a
                # coverage gap, NOT advisory and NOT drift.
                verdict = STATUS_NOT_CONFIGURED
                failed = _failed_checks(None, {}, configured=False)
                not_configured.append(platform)
                record_verdict(platform, verdict)
                receipts.append(_build_receipt(platform, verdict, failed))
                counts[verdict] += 1
                continue

            # The v1 config schema has no dedicated private-marker field; the
            # expected_target href is what we assert. If a future schema adds a
            # ``marker``, it flows through as expected_marker; absent it,
            # marker_present stays None and drift cannot be confirmed (the page
            # is never PROVEN to be the canary), so it correctly falls to
            # advisory rather than a false drift.
            facts = fetch_target(cfg["post_url"])
            anchor = inspect_target_anchor(
                cfg["post_url"],
                cfg["expected_target"],
                expected_marker=cfg.get("marker") or None,
            )

            verdict = _classify(facts, anchor, configured=True)
            failed = _failed_checks(facts, anchor, configured=True)
            record_verdict(platform, verdict)

            # Wave 4 zero-auth MVP: best-effort rendered-link verification.
            # Adds backlink_outcome to the receipt when the page is readable
            # and the post_url is set (no-op for not-configured paths).
            backlink_outcome: str | None = None
            try:
                from backlink_publisher.publishing._verify_html import verify_rendered_link
                vr = verify_rendered_link(
                    published_url=cfg["post_url"],
                    target_url=cfg["expected_target"],
                )
                if vr.effective:
                    backlink_outcome = "effective_backlink"
                else:
                    backlink_outcome = "published_but_ineffective"
            except Exception:  # noqa: BLE001 — best-effort; never fails the canary
                backlink_outcome = "failed"

            is_stale = verdict == STATUS_ADVISORY and _is_stale(platform, verdict)
            if is_stale:
                stale.append(platform)
            receipt = _build_receipt(platform, verdict, failed, stale=is_stale)
            if backlink_outcome:
                receipt["backlink_outcome"] = backlink_outcome
            receipts.append(receipt)
            counts[verdict] += 1

        write_jsonl(receipts)

        # Always-on RECON summary (bypasses --log-level). Coverage gaps and
        # stale canaries are surfaced loudly, never silently absent.
        canary_logger.recon(
            "canary_summary",
            mode=MODE,
            checked=len(receipts),
            verdicts=counts,
            not_configured=not_configured,
            canary_stale=stale,
        )
        if not_configured:
            canary_logger.recon(
                "canary_coverage_gap",
                platforms=not_configured,
                hint="seed a [canary.<platform>] post or accept the gap",
            )
        if stale:
            canary_logger.recon(
                "canary_stale_needs_reseed",
                platforms=stale,
            )
        # No SystemExit: verdicts are advisory data, not process failures.
    except PipelineError as exc:
        handle_error(exc)


if __name__ == "__main__":
    main()
