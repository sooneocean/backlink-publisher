"""probe-citations — GEO AI-citation probe CLI verb (Plan 2026-05-29-006 Unit 7).

Wires: selection (U6) → guard → probe → classify (U5) → emit → advance cursor.

Contract:
* ``--dry-run`` is the **default**: prints the selected query plan + estimated
  cost ceiling; zero network calls; exit 0.
* ``--probe`` hits the network (requires ``[geo.probe_provider]`` config;
  absent → ``DependencyError``/exit 3). Per-pair: guard → probe → classify
  → append ``citation.observed`` → advance cursor (D5). A transient failure
  **re-probes, not re-appends** (D11 read-time dedup). Mid-batch abort on
  cost cap / wall-clock budget exits cleanly with durably-appended pairs
  cursored, rest untouched; never-raises.
* ``flock`` guards overlapping runs.
* stdout = JSONL data; stderr = config banner + diagnostics + one
  ``logger.recon(...)`` per run. Exit 0 default; ``--fail-on-low-share``
  → advisory exit 6, **suppressed for never_probed/warming_up/below-floor**.
* Closed-set args (``--engine``, ``--format``) validated post-parse via
  ``UsageError`` (exit 1), not ``choices=`` (repo convention).
* No ``--api-key`` flag (S4: key lives in config / env only).
"""

from __future__ import annotations

import contextlib
import json
import sys
import uuid
from pathlib import Path

from backlink_publisher._util.errors import (
    DependencyError,
    UsageError,
    emit_envelope_and_exit,
    emit_error,
    handle_error,
)
from backlink_publisher._util.logger import get_logger
from backlink_publisher.config import load_config
from backlink_publisher.events import EventStore

from .. import config_echo

_log = get_logger("probe-citations")

#: Advisory exit code when --fail-on-low-share fires (mirrors anchor-alarm
#: and recheck-backlinks exit 6 convention).
FAIL_ON_LOW_SHARE_EXIT_CODE = 6

#: Default cost cap (maximum probes per run).
DEFAULT_COST_CAP = 20

#: Default wall-clock budget in seconds.
DEFAULT_WALL_CLOCK_S = 300.0

#: Low-share threshold (below this measured share → low-share advisory fires).
LOW_SHARE_THRESHOLD = 0.5

#: Valid engine names for post-parse validation (closed-set).
_VALID_FORMATS = frozenset({"jsonl", "text"})


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="probe-citations",
        description=(
            "GEO AI-citation probe: for each (target, query) pair from config, "
            "query an AI engine and record whether the target site was cited in "
            "the answer. Without --probe this is a zero-network dry preview. "
            "Emits citation.observed events to events.db. stdout = JSONL."
        ),
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="enable network probing (default: zero-network dry preview)",
    )
    parser.add_argument(
        "--engine",
        default="perplexity",
        metavar="ENGINE",
        help="AI probe engine (default: perplexity)",
    )
    parser.add_argument(
        "--format",
        default="jsonl",
        metavar="FORMAT",
        help="output format: jsonl or text (default: jsonl)",
    )
    parser.add_argument(
        "--cost-cap",
        type=int,
        default=DEFAULT_COST_CAP,
        metavar="N",
        help=f"maximum probes this run (default: {DEFAULT_COST_CAP})",
    )
    parser.add_argument(
        "--fail-on-low-share",
        action="store_true",
        help=(
            "exit 6 if any measured-above-floor target has share below "
            f"{LOW_SHARE_THRESHOLD:.0%}; suppressed for "
            "never_probed/warming_up/below-floor targets"
        ),
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=7,
        metavar="N",
        help="minimum age in days before a pair is re-eligible (default: 7)",
    )
    parser.add_argument(
        "--max-pairs",
        type=int,
        default=10,
        metavar="N",
        help="maximum pairs to select per run (default: 10)",
    )
    args = parser.parse_args(argv)

    # -- Post-parse validation (closed-set convention: UsageError/exit 1) -----
    from backlink_publisher.geo.engines import known_engines

    valid_engines = set(known_engines())
    if args.engine not in valid_engines:
        emit_error(
            f"probe-citations: unknown engine {args.engine!r}; "
            f"supported engines: {', '.join(sorted(valid_engines))}",
            exit_code=1,
        )

    if args.format not in _VALID_FORMATS:
        emit_error(
            f"probe-citations: unknown format {args.format!r}; "
            f"supported formats: {', '.join(sorted(_VALID_FORMATS))}",
            exit_code=1,
        )

    if args.cost_cap is not None and args.cost_cap <= 0:
        emit_error(
            "probe-citations: --cost-cap must be a positive integer",
            exit_code=1,
        )

    if args.stale_days <= 0:
        emit_error(
            "probe-citations: --stale-days must be a positive integer",
            exit_code=1,
        )

    if args.max_pairs <= 0:
        emit_error(
            "probe-citations: --max-pairs must be a positive integer",
            exit_code=1,
        )

    cfg = load_config()
    config_echo.emit_banner(cfg, "probe-citations")

    # -- Build the (target, query) corpus from config -------------------------
    all_pairs = _build_corpus(cfg)

    if not all_pairs:
        print(
            "probe-citations: no (target, query) pairs configured; "
            "add [targets.\"<domain>\"].probe_queries to config.toml",
            file=sys.stderr,
        )
        _log.recon("probe_citations_no_pairs")
        return

    # -- Selection (D5 cursor from events.db) ---------------------------------
    from backlink_publisher.geo.selection import select_pairs

    store = EventStore()
    selection = select_pairs(
        all_pairs,
        store=store,
        stale_days=args.stale_days,
        max_pairs=args.max_pairs,
    )

    # -- Dry-run (default): zero network ---------------------------------------
    if not args.probe:
        _run_dry(args, cfg, selection, store)
        return

    # -- Probe (network): requires GEO config ----------------------------------
    if cfg.geo_probe_provider is None:
        handle_error(
            DependencyError(
                "probe-citations: --probe requires [geo.probe_provider] in "
                "config.toml (or BACKLINK_GEO_API_KEY env var). "
                "See config.example.toml for the required fields."
            )
        )

    _run_probe(args, cfg, selection, store, all_pairs=all_pairs)


# ---------------------------------------------------------------------------
# Dry-run path
# ---------------------------------------------------------------------------


def _run_dry(args, cfg, selection, store) -> None:
    """Zero-network dry preview: print plan + cost ceiling, exit 0."""
    from backlink_publisher.geo.share import compute_share

    candidates = selection.candidates
    cost_ceiling = len(candidates)  # upper bound (one API call per pair)

    print(
        f"probe-citations: dry-run — {len(candidates)} pair(s) selected "
        f"(cost ceiling: {cost_ceiling} probe(s)); add --probe to run",
        file=sys.stderr,
    )

    if selection.starvation_risk:
        print(
            f"probe-citations: WARNING starvation risk — corpus has "
            f"{selection.total_pairs} pairs but capacity is "
            f"{selection.coverage_capacity:.0f} (--max-pairs × --stale-days). "
            f"Some pairs may not be re-probed within {args.stale_days} days.",
            file=sys.stderr,
        )

    rows = []
    for cand in candidates:
        aliases = cfg.target_brand_aliases.get(
            _strip_scheme_host(cand.target_url), []
        )
        warnings = []
        if not aliases:
            warnings.append("missing-brand-alias")

        # Advisory share status (read-only, no network).
        share = compute_share(cand.target_url, store=store)
        if share.state == "never_probed":
            warnings.append("never_probed")
        elif share.state == "warming_up":
            warnings.append("warming_up")

        row = {
            "type": "dry_run",
            "target_url": cand.target_url,
            "query": cand.query,
            "last_probed_at": cand.last_probed_at,
            "staleness_days": (
                None
                if cand.staleness_days == float("inf")
                else round(cand.staleness_days, 2)
            ),
            "warnings": warnings,
        }
        rows.append(row)
        if args.format == "jsonl":
            print(json.dumps(row, ensure_ascii=False), flush=True)

    if args.format == "text":
        for row in rows:
            warn_str = (
                f" [{', '.join(row['warnings'])}]" if row["warnings"] else ""
            )
            print(
                f"  {row['target_url']}  q={row['query']!r}"
                f"  stale={row['staleness_days']}d{warn_str}"
            )

    _log.recon(
        "probe_citations_dry_run",
        pairs=len(rows),
        cost_ceiling=cost_ceiling,
        starvation_risk=selection.starvation_risk,
    )


# ---------------------------------------------------------------------------
# Probe path
# ---------------------------------------------------------------------------


def _run_probe(args, cfg, selection, store, *, all_pairs) -> None:
    """Network probe path — requires GEO config, flock-protected."""
    geo_cfg = cfg.geo_probe_provider  # confirmed non-None by caller

    with _single_run_lock(store.path.parent) as acquired:
        if not acquired:
            _log.recon("probe_citations_skipped_locked")
            print(
                "probe-citations: another run holds the lock; skipping",
                file=sys.stderr,
            )
            return

        _probe_and_emit(args, cfg, geo_cfg, selection, store, all_pairs=all_pairs)


def _probe_and_emit(args, cfg, geo_cfg, selection, store, *, all_pairs) -> None:
    """Inner probe loop — called inside the flock context."""
    from backlink_publisher.geo.engines import dispatch_probe
    from backlink_publisher.geo.joins import build_published_article_set
    from backlink_publisher.geo.run import probe_many
    from backlink_publisher.geo.share import compute_share

    engine_name = args.engine
    run_id = str(uuid.uuid4())

    # Build article URL set for credit matching (no network).
    article_urls = build_published_article_set(store=store)

    # Build per-target brand-alias map.
    brand_aliases_map = _build_brand_aliases_map(cfg, selection.candidates)

    # Injectable probe function bound to the chosen engine.
    def _probe_fn(query: str, probe_cfg):
        return dispatch_probe(engine_name, query, probe_cfg)

    candidates = selection.candidates
    rows, summary = probe_many(
        candidates,
        probe_fn=_probe_fn,
        cfg=geo_cfg,
        store=store,
        article_urls=article_urls,
        brand_aliases_map=brand_aliases_map,
        cost_cap=args.cost_cap,
        wall_clock_budget_s=DEFAULT_WALL_CLOCK_S,
        engine=engine_name,
        run_id=run_id,
    )

    # Emit per-probe rows to stdout.
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)

    # Emit summary row.
    summary_row = summary.to_jsonl_dict()
    summary_row["run_id"] = run_id
    print(json.dumps(summary_row, ensure_ascii=False), flush=True)

    _log.recon(
        "probe_citations_run",
        run_id=run_id,
        engine=engine_name,
        probed=summary.probed,
        site_cited=summary.site_cited,
        article_cited=summary.article_cited,
        absent=summary.absent,
        refused=summary.refused,
        probe_error=summary.probe_error,
        deferred=summary.deferred,
        cost_cap_hit=summary.cost_cap_hit,
        budget_exhausted=summary.budget_exhausted,
    )

    print(
        f"probe-citations: probed {summary.probed}, "
        f"site_cited {summary.site_cited}, "
        f"article_cited {summary.article_cited}, "
        f"absent {summary.absent}, "
        f"refused {summary.refused}, "
        f"errors {summary.probe_error}, "
        f"deferred {summary.deferred}",
        file=sys.stderr,
    )

    # -- --fail-on-low-share advisory gate (D7/D10) ---------------------------
    # Check ALL corpus targets (not just this run's candidates) so the gate
    # fires even when no pairs were selected (everything recently probed).
    if args.fail_on_low_share:
        all_targets = list({url for url, _ in all_pairs})
        _check_fail_on_low_share(all_targets, store)


def _check_fail_on_low_share(all_target_urls: list[str], store) -> None:
    """Advisory exit 6 if any measured above-floor target has low share.

    ``all_target_urls`` should contain every distinct target URL in the corpus
    (not just the candidates selected for this run — the gate is corpus-wide).
    Suppressed for never_probed, warming_up, and excluded targets (D10).
    """
    from backlink_publisher.geo.share import compute_share

    seen_targets: set[str] = set()
    low_share_targets: list[str] = []

    for tgt in all_target_urls:
        if tgt in seen_targets:
            continue
        seen_targets.add(tgt)

        share = compute_share(tgt, store=store)
        # Only fire for fully measured targets (never_probed / warming_up /
        # excluded are suppressed — D7/D10 contract).
        if share.state != "measured":
            continue
        # share is non-None when state == "measured"
        if share.share is not None and share.share < LOW_SHARE_THRESHOLD:
            low_share_targets.append(tgt)

    if low_share_targets:
        for tgt in low_share_targets:
            print(
                f"probe-citations: low citation share for {tgt}",
                file=sys.stderr,
            )
        emit_envelope_and_exit(
            "LowCitationShare",
            FAIL_ON_LOW_SHARE_EXIT_CODE,
            f"probe-citations: {len(low_share_targets)} target(s) have "
            f"measured citation share below {LOW_SHARE_THRESHOLD:.0%}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_corpus(cfg) -> list[tuple[str, str]]:
    """Build the (target_url, query) corpus from operator config.

    Uses ``cfg.target_probe_queries`` (keyed by domain, values = query list).
    Falls back to an empty list when no probe queries are configured.
    """
    pairs: list[tuple[str, str]] = []
    for domain, queries in cfg.target_probe_queries.items():
        # Reconstruct a canonical-enough URL from the domain key.
        # Config keys typically store the domain without scheme.
        target_url = domain if domain.startswith("http") else f"https://{domain}"
        for query in queries:
            if query:
                pairs.append((target_url, query))
    return pairs


def _build_brand_aliases_map(cfg, candidates) -> dict[str, list[str]]:
    """Return per-target brand alias list keyed by target_url."""
    result: dict[str, list[str]] = {}
    for cand in candidates:
        tgt = cand.target_url
        if tgt not in result:
            domain_key = _strip_scheme_host(tgt)
            result[tgt] = cfg.target_brand_aliases.get(domain_key, [])
    return result


def _strip_scheme_host(url: str) -> str:
    """Extract 'host' (no scheme, no path) for config-key lookup."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        return parts.netloc or url
    except Exception:
        return url


@contextlib.contextmanager
def _single_run_lock(config_dir: Path):
    """Non-blocking exclusive flock so overlapping cron runs don't compound.

    Yields True if acquired, False if another run already holds the lock.
    """
    import fcntl

    config_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config_dir / ".probe-citations.lock"
    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


if __name__ == "__main__":
    main()
