"""recheck-backlinks — post-publish backlink survival re-verification (Plan 2026-05-29-004).

Re-probes previously-published backlinks for liveness, dofollow drift, and
link/anchor tampering, emitting a ``link.rechecked`` lifecycle time series to
events.db. Decoupled from the plan-007 migration: it writes its own event kind
and never touches history_store (R6 ledger writeback is a deferred follow-up).

Contract (mirrors equity-ledger / validate-backlinks conventions):

* Network is gated behind ``--probe``. Without it the run is a **zero-network
  dry preview** that lists which backlinks WOULD be probed (R12).
* stdout = JSONL data, stderr = config banner + reconciliation summary.
* Exit 0 by default (advisory diagnostic). ``--fail-on-dead`` exits 6 (the
  domain-alarm code, like the anchor-distribution alarm) when any *deterministic*
  dead backlink (host_gone / link_stripped) is found — dofollow_lost and
  probe_error never trip it (R13).
* Candidates come from events.db age selection, or from stdin JSONL when piped
  (R11). The probe is never-raises: one bad link never aborts the batch.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from .._util.errors import emit_envelope_and_exit, emit_error
from .._util.jsonl import write_jsonl
from .._util.logger import get_logger
from ..config import load_config

_log = get_logger("recheck")

#: --fail-on-dead exit code. 6 is the project's "advisory domain alarm fired"
#: code (the anchor-distribution alarm uses it too); it sits outside the 1–5
#: error taxonomy so it never collides with a real pipeline error.
FAIL_ON_DEAD_EXIT_CODE = 6

_PER_TARGET_TIMEOUT = 10.0  # seconds per probe (bounds redirect-chain accumulation)
_BATCH_BUDGET_S = 600.0     # total wall-clock ceiling for the probe batch (SEC1)

#: A readable channel whose indexability is `unknown` at/above this fraction is
#: flagged "unverifiable by simple fetch" (R6b) — so an anti-bot channel reads as
#: visibly inert, not falsely green. 0.5: a majority-unknown channel told us
#: nothing useful about indexability.
_INDEXABILITY_UNKNOWN_RATE_FLAG = 0.5

#: R3e caveat — page-level noindex from OUR UA is necessary-not-sufficient for
#: indexing and could be UA-cloaked; we never query Google/GSC. Mirrors
#: docs/runbooks/2026-05-27-canary-targets-operations.md §7.
_INDEXABILITY_CAVEAT = (
    "  note: page-level noindex/X-Robots-Tag seen by our UA only — "
    "necessary-not-sufficient for indexing, not a Googlebot guarantee "
    "(possible UA cloaking). No GSC/Google query."
)


def main(argv: list[str] | None = None) -> None:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        prog="recheck-backlinks",
        description=(
            "Re-verify previously-published backlinks for liveness, dofollow "
            "drift, and link/anchor tampering. Emits a link.rechecked time "
            "series to events.db; the /ce:health dashboard surfaces decay "
            "counts. Without --probe this is a zero-network dry preview."
        ),
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="enable network re-verification (default: zero-network dry preview)",
    )
    parser.add_argument(
        "--fail-on-dead", action="store_true",
        help="exit 6 if any deterministic dead backlink (host_gone/link_stripped) is found",
    )
    parser.add_argument(
        "--fail-on-unindexable", action="store_true",
        help=("exit 6 if any backlink sits on a confirmed-blocked (noindex) page; "
              "indeterminate (unknown) pages never trip it (opt-in; default off)"),
    )
    parser.add_argument("--host", metavar="HOST", help="only recheck backlinks on this host")
    parser.add_argument("--run-id", metavar="ID", help="only recheck backlinks from this run_id")
    parser.add_argument(
        "--since", metavar="ISO",
        help="only recheck backlinks published on/after this ISO timestamp",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="cap candidates this run (in addition to the built-in per-run cap)",
    )
    args = parser.parse_args(argv)

    # Post-parse validation — repo convention is UsageError (exit 1), not
    # argparse's exit 2; so no choices=/type-coerced enums. [[argparse-choices-vs-usage-error]]
    if args.limit is not None and args.limit <= 0:
        emit_error("recheck-backlinks: --limit must be a positive integer", exit_code=1)
    since_dt = None
    if args.since:
        since_dt = _parse_since(args.since)

    cfg = load_config()
    config_echo.emit_banner(cfg, "recheck-backlinks")

    from backlink_publisher.events import EventStore
    from backlink_publisher.recheck import selection
    from backlink_publisher.recheck.events_io import emit_recheck
    from backlink_publisher.recheck.probe import recheck_link

    store = EventStore()
    now = datetime.now(timezone.utc)

    candidates = selection.read_stdin_candidates(sys.stdin)
    if candidates is None:
        candidates = selection.select_candidates(
            store, now=now, since=since_dt, host=args.host,
            run_id=args.run_id, limit=args.limit,
        )

    # ── dry preview (no --probe): zero network ───────────────────────────────
    if not args.probe:
        rows = [recheck_link(c, probe=False) for c in candidates]
        write_jsonl(iter(rows), sys.stdout)
        _log.recon("recheck_dry_preview", candidates=len(rows))
        print(
            f"recheck-backlinks: dry preview — {len(rows)} candidate(s) would be "
            f"probed (add --probe to run)",
            file=sys.stderr,
        )
        return

    # ── probe (network): concurrency guard + batch budget ────────────────────
    with _single_run_lock(store.path.parent) as acquired:
        if not acquired:
            _log.recon("recheck_skipped_locked")
            print(
                "recheck-backlinks: another run holds the lock; skipping",
                file=sys.stderr,
            )
            return
        results = _probe_batch(candidates)
        written = emit_recheck(store, results)

    tally = _tally(results)
    _log.recon("recheck_reconciliation", checked=len(results), written=written, **tally)
    write_jsonl(iter(results), sys.stdout)
    print(_summary_line(len(results), written, tally), file=sys.stderr)
    for line in _indexability_summary(results):
        print(line, file=sys.stderr)

    # Fail gates (both reuse exit code 6). --fail-on-dead is checked first: a dead
    # link is strictly worse than a live-but-noindex one, and both map to the same
    # code, so the exit code stays deterministic when both flags trip.
    dead = tally["host_gone"] + tally["link_stripped"]
    if args.fail_on_dead and dead > 0:
        emit_envelope_and_exit(
            "DeadBacklinksDetected",
            FAIL_ON_DEAD_EXIT_CODE,
            f"recheck-backlinks: {dead} deterministic dead backlink(s) detected",
        )

    # Opt-in indexability gate: only a live, present dofollow link on a CONFIRMED
    # blocked (noindex) page trips it (the equity-waste case — same count as the
    # summary headline). unknown (fail-open) never trips; a stripped/nofollow link
    # is excluded. Reuses --fail-on-dead's exit code (R9).
    blocked = _alive_blocked_count(results)
    if args.fail_on_unindexable and blocked > 0:
        emit_envelope_and_exit(
            "UnindexableBacklinksDetected",
            FAIL_ON_DEAD_EXIT_CODE,
            f"recheck-backlinks: {blocked} live dofollow backlink(s) on "
            f"confirmed-unindexable (noindex) page(s)",
        )


def _parse_since(value: str):
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        emit_error(
            f"recheck-backlinks: --since must be an ISO timestamp, got {value!r}",
            exit_code=1,
        )
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _probe_batch(candidates: list[dict]) -> list[dict]:
    """Probe each candidate; never-raises. Stops at the batch wall-clock budget
    so a tarpitting host can't stall the cron run indefinitely (SEC1)."""
    import time

    from backlink_publisher.recheck.probe import recheck_link

    deadline = time.monotonic() + _BATCH_BUDGET_S
    results: list[dict] = []
    for index, candidate in enumerate(candidates):
        if time.monotonic() > deadline:
            deferred = len(candidates) - index
            _log.recon("recheck_budget_exhausted", probed=index, deferred=deferred)
            print(
                f"recheck-backlinks: batch budget ({_BATCH_BUDGET_S:.0f}s) "
                f"exhausted; {deferred} candidate(s) deferred to next run",
                file=sys.stderr,
            )
            break
        results.append(recheck_link(candidate, probe=True, timeout=_PER_TARGET_TIMEOUT))
    return results


def _tally(results: list[dict]) -> dict[str, int]:
    from backlink_publisher.recheck import verdicts

    counts = {v: 0 for v in verdicts.VERDICTS}
    for r in results:
        verdict = r.get("verdict")
        if verdict in counts:
            counts[verdict] += 1
    return counts


def _summary_line(checked: int, written: int, tally: dict[str, int]) -> str:
    from backlink_publisher.recheck import verdicts

    return (
        f"recheck-backlinks: checked {checked}, "
        f"alive {tally[verdicts.ALIVE]}, "
        f"unknown {tally[verdicts.PROBE_ERROR]}, "
        f"dead {tally[verdicts.HOST_GONE] + tally[verdicts.LINK_STRIPPED]}, "
        f"dofollow-lost {tally[verdicts.DOFOLLOW_LOST]} "
        f"({written} event(s) written)"
    )


def _alive_blocked_count(results: list[dict]) -> int:
    """Live, present, dofollow links sitting on a confirmed noindex page — the
    pure equity-waste case the summary headline and --fail-on-unindexable both
    target. A link_stripped page has no surviving backlink and a dofollow_lost
    one already passes zero equity, so only ``alive`` counts (the gate and the
    headline must agree on this; counting all blocked verdicts over-reports)."""
    from backlink_publisher.recheck import indexability, verdicts

    return sum(
        1 for r in results
        if r.get("verdict") == verdicts.ALIVE
        and r.get("indexability") == indexability.BLOCKED
    )


def _indexability_summary(results: list[dict]) -> list[str]:
    """stderr indexability block (R6/R6b/R3e). The headline `alive·blocked` count
    is always shown (so 0 is visible); per-channel "unverifiable" flags and the
    UA-cloaking caveat appear only when there is an indexability signal — no
    false alarm on a clean run. Never on stdout (that stays JSONL data)."""
    from backlink_publisher.recheck import indexability, verdicts

    readable = {verdicts.ALIVE, verdicts.LINK_STRIPPED, verdicts.DOFOLLOW_LOST}
    alive_blocked = _alive_blocked_count(results)
    blocked_total = unknown_total = 0
    chan_readable: dict[str, int] = {}
    chan_unknown: dict[str, int] = {}
    for r in results:
        idx = r.get("indexability", indexability.UNKNOWN)
        verdict = r.get("verdict")
        if idx == indexability.BLOCKED:
            blocked_total += 1
        elif idx == indexability.UNKNOWN:
            unknown_total += 1
        # Per-channel unknown-rate over pages we actually READ — a dead page's
        # unknown indexability is expected and must not inflate the rate.
        if verdict in readable:
            chan = r.get("platform") or "unknown"
            chan_readable[chan] = chan_readable.get(chan, 0) + 1
            if idx == indexability.UNKNOWN:
                chan_unknown[chan] = chan_unknown.get(chan, 0) + 1

    lines = [
        f"recheck-backlinks: indexability — alive·blocked {alive_blocked} "
        f"(zero-equity dofollow), unknown {unknown_total}"
    ]
    flagged = [
        (chan, chan_unknown.get(chan, 0), total)
        for chan, total in sorted(chan_readable.items())
        if total and chan_unknown.get(chan, 0) / total >= _INDEXABILITY_UNKNOWN_RATE_FLAG
    ]
    for chan, unk, total in flagged:
        lines.append(
            f"  channel {chan}: indexability unverifiable by simple fetch "
            f"({unk}/{total} unknown, {unk / total:.0%})"
        )
    if alive_blocked or blocked_total or flagged:
        lines.append(_INDEXABILITY_CAVEAT)
    return lines


@contextlib.contextmanager
def _single_run_lock(config_dir: Path):
    """Non-blocking exclusive file lock so overlapping cron runs don't compound
    (SEC1). Yields True if acquired, False if another run already holds it."""
    import fcntl

    config_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config_dir / ".recheck-backlinks.lock"
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
