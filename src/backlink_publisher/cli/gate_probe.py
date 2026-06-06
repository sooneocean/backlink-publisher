"""gate-probe — Phase-0 falsification-gate dispatcher (plan 2026-06-01-005).

One read-only verb that runs a cheap premise probe and emits a single
``GO``/``KILL``/``INCONCLUSIVE``/``BLOCKED`` verdict (one JSONL line) on stdout,
for hand-curation into ``docs/ideation/gate-verdicts.md``. Each ``--gate`` routes
to a pure engine under :mod:`backlink_publisher.gates`.

* ``--gate g2`` — money-page silent-decay baseline (Tier-1 offline). **Live.**
* ``--gate g3`` — referer render-path audit + GA4 referral intake (Tier-2). *(Unit 3)*
* ``--gate g5`` — footprint survival re-fetch (Tier-1 offline). *(Unit 4)*

stdout = the verdict JSONL; stderr = the config-echo banner + a RECON line.
Exit 0 on a completed probe (even when every page is dead); exit 1 on usage.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from backlink_publisher._util.errors import emit_error
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher.config import load_config
from backlink_publisher.gates import g2_decay, g3_referer, g5_footprint_survival
from backlink_publisher.gates import verdict as gv

_GATES = ("g2", "g3", "g5")


def _money_page_urls(cfg) -> list[str]:
    """The operator's own money pages from ``[sites.*.url_categories]`` config.

    Flattens ``site_url_categories`` (``{main_domain: {category: url}}``) to a
    deduped, sorted URL list. Empty when no sites are configured.
    """
    seen: set[str] = set()
    for categories in cfg.site_url_categories.values():
        seen.update(u for u in categories.values() if u)
    return sorted(seen)


def _run_g2(cfg, decay_threshold: float | None) -> gv.GateVerdict:
    urls = _money_page_urls(cfg)
    if not urls:
        print(
            "gate-probe g2: no money pages configured ([sites.*.url_categories]); "
            "verdict is INCONCLUSIVE (nothing to measure).",
            file=sys.stderr,
        )
    verdict = g2_decay.assess_decay(urls, decay_threshold=decay_threshold)
    _recon(verdict)
    return verdict


def _run_g3(args) -> gv.GateVerdict:
    referral = None
    if args.referral_sessions is not None:
        referral = g3_referer.ReferralEvidence(
            sessions=args.referral_sessions, window=args.referral_window
        )
    verdict = g3_referer.assess_g3(
        referral=referral,
        credentials_available=not args.credentials_unavailable,
        strip_threshold=args.strip_threshold,
    )
    _recon(verdict)
    return verdict


def _run_g5(args) -> gv.GateVerdict:
    import json

    from backlink_publisher.events import EventStore

    store = EventStore()
    rows = store.query(
        "SELECT live_url, target_urls_json FROM articles WHERE live_url IS NOT NULL"
    )
    links: list[g5_footprint_survival.PublishedLink] = []
    for row in rows:
        targets = json.loads(row["target_urls_json"] or "[]")
        if targets:
            links.append(
                g5_footprint_survival.PublishedLink(
                    live_url=row["live_url"], target_url=targets[0]
                )
            )
    if args.sample_size is not None:
        links = links[: args.sample_size]
    if not links:
        print(
            "gate-probe g5: no published links in events.db articles; "
            "verdict is INCONCLUSIVE (nothing to re-fetch).",
            file=sys.stderr,
        )
    verdict = g5_footprint_survival.assess_survival(
        links,
        expected_rel=args.expected_rel,
        survival_threshold=args.survival_threshold,
        saturation_floor=args.saturation_floor,
    )
    _recon(verdict)
    return verdict


def _recon(verdict: gv.GateVerdict) -> None:
    print(
        f"gate-probe {verdict.gate}: verdict={verdict.state} "
        f"rate={'—' if verdict.rate is None else f'{verdict.rate:.2%}'} "
        f"sample_n={verdict.sample_n}",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gate-probe",
        description=(
            "Run a Phase-0 falsification gate (a cheap, read-only premise probe) "
            "and emit one GO/KILL/INCONCLUSIVE/BLOCKED verdict on stdout. The "
            "first run per gate is a calibration pass (INCONCLUSIVE) that sets "
            "the threshold; rerun with the recorded threshold to reach GO/KILL."
        ),
    )
    parser.add_argument(
        "--gate",
        metavar="ID",
        help="which gate to run: g2 (money-page decay), g3, g5",
    )
    parser.add_argument(
        "--decay-threshold",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "[g2] calibrated decay-rate boundary in [0,1]. Omit on the first "
            "(calibration) run → INCONCLUSIVE; provide it to reach GO (rate >= "
            "threshold) / KILL (below)."
        ),
    )
    parser.add_argument(
        "--strip-threshold",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "[g3] calibrated majority-strip boundary in [0,1]. At/above it the "
            "static audit KILLs (attribution structurally blind). Omit → calibration."
        ),
    )
    parser.add_argument(
        "--referral-sessions",
        type=int,
        default=None,
        metavar="N",
        help="[g3] operator GA4 referral-session count (from gsearch-radar). Typed evidence.",
    )
    parser.add_argument(
        "--referral-window",
        default=None,
        metavar="ISO",
        help="[g3] the ISO window the referral count covers (required with --referral-sessions).",
    )
    parser.add_argument(
        "--credentials-unavailable",
        action="store_true",
        help="[g3] Tier-2 GA4/GSC credentials are not configured → BLOCKED (parked).",
    )
    parser.add_argument(
        "--survival-threshold",
        type=float,
        default=None,
        metavar="FRAC",
        help=(
            "[g5] calibrated fingerprint-survival boundary in [0,1]. At/above → GO "
            "(footprint measures a crawler-visible signal); below → KILL. Omit → calibration."
        ),
    )
    parser.add_argument(
        "--saturation-floor",
        type=float,
        default=g5_footprint_survival.DEFAULT_SATURATION_FLOOR,
        metavar="FRAC",
        help=(
            "[g5] minimum re-fetchable fraction; below it the gate returns terminal "
            "INCONCLUSIVE-unmeasurable (default: 0.5)."
        ),
    )
    parser.add_argument(
        "--expected-rel",
        default=g5_footprint_survival.DEFAULT_EXPECTED_REL,
        metavar="REL",
        help="[g5] the operator's emitted rel fingerprint to test survival of "
        "(default: 'noopener noreferrer').",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        metavar="N",
        help="[g5] cap the number of published links re-fetched (default: all).",
    )
    args = parser.parse_args(argv)

    gate = (args.gate or "").lower()
    if gate not in _GATES:
        emit_error(
            f"gate-probe: --gate must be one of {', '.join(_GATES)}", exit_code=1
        )
    for name, value in (
        ("--decay-threshold", args.decay_threshold),
        ("--strip-threshold", args.strip_threshold),
        ("--survival-threshold", args.survival_threshold),
        ("--saturation-floor", args.saturation_floor),
    ):
        if value is not None and not (0.0 <= value <= 1.0):
            emit_error(f"gate-probe: {name} must be within [0, 1]", exit_code=1)
    if args.referral_sessions is not None:
        if args.referral_sessions < 0:
            emit_error("gate-probe: --referral-sessions must be >= 0", exit_code=1)
        if not args.referral_window:
            emit_error(
                "gate-probe: --referral-window is required with --referral-sessions",
                exit_code=1,
            )
    if args.sample_size is not None and args.sample_size <= 0:
        emit_error("gate-probe: --sample-size must be a positive integer", exit_code=1)

    cfg = load_config()
    config_echo.emit_banner(cfg, "gate-probe")

    if gate == "g2":
        verdict = _run_g2(cfg, args.decay_threshold)
    elif gate == "g3":
        verdict = _run_g3(args)
    else:
        verdict = _run_g5(args)
    write_jsonl([verdict.to_jsonl_dict()], sys.stdout)


if __name__ == "__main__":
    main()
