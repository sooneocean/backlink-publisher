"""Operator dedup escape verbs for publish-backlinks (U5a, extended in U5b).

Early-exit verbs (``--forget``, ``--list-uncertain``, and later
``--adjudicate-uncertain``) that read/mutate the authoritative dedup store and
its append-only audit log. Mutual exclusion with the publish/checkpoint verbs is
enforced in :func:`_publish_helpers._handle_checkpoint_ops`; this module performs
the action and exits 0.

Extracted from ``_publish_helpers.py`` to keep that file under the monolith
budget and to give the dedup verbs (which grow with U5b) their own home.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U5a).
"""

from __future__ import annotations

import sys
from typing import Any


def add_dedup_arguments(parser: Any) -> None:
    """Register the dedup escape-verb flags on the publish-backlinks parser.
    Kept here (not in ``_build_parser``) so all dedup-verb concerns live together
    and ``_publish_helpers.py`` stays under the monolith budget."""
    parser.add_argument(
        "--forget",
        nargs=2,
        default=None,
        metavar=("PLATFORM", "TARGET_URL"),
        help=(
            "Clear one dedup key (single key only — no globs/bulk) so it becomes "
            "re-publishable, and exit 0. Requires --reason. Records an audit entry."
        ),
    )
    parser.add_argument(
        "--list-uncertain",
        action="store_true",
        default=False,
        help=(
            "List held (uncertain) dedup rows awaiting adjudication and exit 0. "
            "Optional --platform filter."
        ),
    )
    parser.add_argument(
        "--adjudicate-uncertain",
        nargs=2,
        default=None,
        metavar=("PLATFORM", "TARGET_URL"),
        help=(
            "Terminally resolve ONE held (uncertain) dedup key. Requires "
            "--to (succeeded|failed) and --reason. Records an audit entry. Exit 0."
        ),
    )
    parser.add_argument(
        "--adjudicate-bulk",
        action="store_true",
        default=False,
        help=(
            "Bulk-resolve held (uncertain) rows matched by --platform/--older-than. "
            "Requires --to, --reason, and either --list-affected (preview) or "
            "--confirm N matching the affected count (blast-radius guard)."
        ),
    )
    parser.add_argument(
        "--to",
        default=None,
        metavar="OUTCOME",
        help="Adjudication outcome: succeeded (-> done) or failed (-> re-publishable).",
    )
    parser.add_argument(
        "--older-than",
        default=None,
        metavar="DURATION",
        help="Bulk-adjudicate filter: only rows older than e.g. 7d / 24h / 3600s.",
    )
    parser.add_argument(
        "--confirm",
        type=int,
        default=None,
        metavar="N",
        help="Bulk-adjudicate guard: must equal the affected-row count to proceed.",
    )
    parser.add_argument(
        "--list-affected",
        action="store_true",
        default=False,
        help="Bulk-adjudicate preview: print the rows that would be resolved; do not mutate.",
    )
    parser.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="Operator reason recorded in the dedup audit log (--forget/--adjudicate).",
    )
    parser.add_argument(
        "--backfill-dedup",
        action="store_true",
        default=False,
        help=(
            "Seed the dedup store from publish-success events (best-effort, "
            "decision-preserving, idempotent) and exit 0. Run before flipping "
            "enforce so the back-catalogue is not re-published."
        ),
    )
    parser.add_argument(
        "--check-enforce-readiness",
        action="store_true",
        default=False,
        help=(
            "Read-only pre-flip check: is the dedup store ready for enforce "
            "(back-catalogue covered, quarantine zero/acknowledged)? Exit 0 if "
            "ready, 1 if not. Counts only on stderr — no campaign URLs."
        ),
    )
    parser.add_argument(
        "--force-manifest",
        default=None,
        metavar="FILE",
        help=(
            "Honor force-flagged rows from a preview manifest (JSONL from "
            "--preview-manifest with force:true) on this enforce run: re-publish "
            "held/uncertain keys. Requires --confirm N (count of force rows) and "
            "--reason; a force on a done key is rejected as a conflict (R11)."
        ),
    )


#: ``--to`` outcome → dedup terminal state. Closed set validated post-parse with
#: ``UsageError`` (exit 1), never argparse ``choices=`` (which exits 2) — see
#: [[argparse-choices-vs-usage-error]].
_TO_STATE = {"succeeded": "done", "failed": "failed"}


def _handle_dedup_ops(args: Any) -> None:
    """Dispatch the dedup escape verbs. Each raises ``SystemExit(0)`` on success
    (or exits 1 via ``emit_error`` on a usage error). No stdin needed."""
    if getattr(args, "forget", None):
        _do_forget(args)
        raise SystemExit(0)

    if getattr(args, "list_uncertain", False):
        _do_list_uncertain(args)
        raise SystemExit(0)

    if getattr(args, "adjudicate_uncertain", None):
        _do_adjudicate_single(args)
        raise SystemExit(0)

    if getattr(args, "adjudicate_bulk", False):
        _do_adjudicate_bulk(args)
        raise SystemExit(0)

    if getattr(args, "backfill_dedup", False):
        from backlink_publisher.idempotency.backfill import run_backfill_cli
        run_backfill_cli()
        raise SystemExit(0)

    if getattr(args, "check_enforce_readiness", False):
        _do_check_enforce_readiness()


def load_force_manifest(
    path: str, *, confirm: int | None, reason: str | None
) -> set[tuple[str, str, str]]:
    """Validate a preview manifest's force-flags and return the set of forced key
    tuples ``(platform, account, target_url)`` (U7c). Exits 1 on any guard
    failure: missing --reason, store-token mismatch (foreign/stale manifest), or
    a --confirm count that doesn't match the number of force:true rows."""
    import json

    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore

    if not reason:
        emit_error("error: --force-manifest requires --reason <text>", exit_code=1)

    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        emit_error(f"error: cannot read --force-manifest {path!r}: {exc}", exit_code=1)

    forced: set[tuple[str, str, str]] = set()
    current_token = DedupStore().store_token()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not entry.get("force"):
            continue
        if entry.get("store_token") != current_token:
            emit_error(
                "error: --force-manifest is bound to a different dedup store "
                "(store_token mismatch) — regenerate it with --preview-manifest "
                "against the current store",
                exit_code=1,
            )
        platform, target_url = entry.get("platform"), entry.get("target_url")
        if not platform or not target_url:
            continue
        key = DedupKey(platform=str(platform), target_url=str(target_url))
        forced.add(key.as_tuple())

    if confirm != len(forced):
        emit_error(
            f"error: --force-manifest has {len(forced)} force-flagged row(s); "
            f"re-run with --confirm {len(forced)} to proceed (blast-radius guard)",
            exit_code=1,
        )
    return forced


def _do_check_enforce_readiness() -> None:
    """Read-only R19b readiness report. Exit 0 if ready, 1 if not. Counts +
    HMAC key digests only on stderr — never campaign URLs."""
    from backlink_publisher.idempotency.reconcile import check_enforce_readiness

    r = check_enforce_readiness()
    print(
        "enforce-readiness: "
        f"covered={r.covered_count}/{r.event_key_count} published key(s), "
        f"missing={r.missing_count}, "
        f"quarantine={r.quarantine_count} "
        f"(acknowledged={r.quarantine_acknowledged}).",
        file=sys.stderr,
    )
    if r.missing_digests:
        print(
            "  missing key digests (run --backfill-dedup): "
            + ", ".join(r.missing_digests),
            file=sys.stderr,
        )
    if r.ok:
        print("enforce-readiness: READY.", file=sys.stderr)
        raise SystemExit(0)
    print(
        "enforce-readiness: NOT READY — see --backfill-dedup / "
        "BACKLINK_PUBLISHER_DEDUP_ENFORCE_ACK_QUARANTINE.",
        file=sys.stderr,
    )
    # Operator-action-required (DependencyError, exit 3) — same class as the
    # enforce precondition gate, NOT a CLI usage error (exit 1).
    raise SystemExit(3)


def _resolve_to_state(args: Any) -> str:
    """Validate ``--to`` post-parse and map to a dedup terminal state."""
    from backlink_publisher._util.errors import emit_error

    raw = getattr(args, "to", None)
    if raw not in _TO_STATE:
        emit_error(
            f"error: --to must be one of {sorted(_TO_STATE)}; got {raw!r}",
            exit_code=1,
        )
    if not args.reason:
        emit_error("error: --adjudicate requires --reason <text>", exit_code=1)
    return _TO_STATE[raw]


def _parse_older_than(spec: str | None) -> float | None:
    """Parse ``7d`` / ``24h`` / ``90m`` / ``3600s`` to seconds. ``None`` → no
    age filter. Raises ``UsageError`` (exit 1) on a malformed spec."""
    from backlink_publisher._util.errors import emit_error

    if not spec:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    unit = spec[-1]
    if unit not in units or not spec[:-1].isdigit():
        emit_error(
            f"error: --older-than must be <int><s|m|h|d> (e.g. 7d); got {spec!r}",
            exit_code=1,
        )
    return int(spec[:-1]) * units[unit]


def _adjudicate_one(store: Any, key: Any, to_state: str, reason: str, *, run_id: str | None) -> None:
    """Transition uncertain → terminal, then append the audit entry. The
    ``expect_from=("uncertain",)`` guard makes the state check atomic with the
    write, so a concurrent enforce run that reclaimed the row (uncertain ->
    attempting) raises ValueError instead of letting us clobber an in-flight
    dispatch. The audit entry is written only after the transition succeeds."""
    from backlink_publisher.idempotency import audit_log

    store.transition(key, to_state, run_id=run_id, expect_from=("uncertain",))
    audit_log.append_entry(
        action="adjudicate",
        platform=key.platform,
        target_url=key.target_url,
        account=key.account,
        from_state="uncertain",
        to_state=to_state,
        reason=reason,
        run_id=run_id,
    )


def _do_adjudicate_single(args: Any) -> None:
    """Resolve ONE held key (``uncertain`` → ``done``/``failed``). Refuses a key
    that is absent or not currently held."""
    import sys as _sys

    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore

    to_state = _resolve_to_state(args)
    platform, target_url = args.adjudicate_uncertain
    key = DedupKey(platform=platform, target_url=target_url)
    store = DedupStore()
    record = store.get(key)
    if record is None:
        emit_error(f"error: no dedup row for {platform} key (nothing to adjudicate)", exit_code=1)
    if record.state != "uncertain":
        emit_error(
            f"error: key is {record.state}, not uncertain; "
            "only held rows can be adjudicated (use --forget for a terminal row)",
            exit_code=1,
        )
    try:
        _adjudicate_one(store, key, to_state, args.reason, run_id=record.run_id)
    except ValueError as exc:
        # The row changed (e.g. a concurrent enforce run reclaimed it) between the
        # read above and the guarded transition — surface cleanly, do not clobber.
        emit_error(f"error: adjudication skipped — {exc}", exit_code=1)
    print(
        f"adjudicate: {platform} key uncertain -> {to_state}.",
        file=_sys.stderr,
    )


def _do_adjudicate_bulk(args: Any) -> None:
    """Resolve every held row matched by ``--platform``/``--older-than``. Guarded:
    without ``--list-affected`` it refuses unless ``--confirm N`` equals the
    affected count, so a wrong selector cannot silently mass-retire backlinks."""
    import sys as _sys
    import time as _time

    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore

    to_state = _resolve_to_state(args)
    older_than_s = _parse_older_than(getattr(args, "older_than", None))
    store = DedupStore()
    rows = store.list_by_state("uncertain", platform=getattr(args, "platform", None))
    if older_than_s is not None:
        cutoff = _time.time() - older_than_s
        rows = [r for r in rows if r.updated_at < cutoff]

    if getattr(args, "list_affected", False):
        print(f"adjudicate-bulk: {len(rows)} row(s) would be set to {to_state}:")
        for r in rows:
            print(f"  {r.platform:<14}  {r.target_url}")
        return

    confirm = getattr(args, "confirm", None)
    if confirm != len(rows):
        emit_error(
            f"error: --adjudicate-bulk would affect {len(rows)} row(s); re-run with "
            f"--confirm {len(rows)} to proceed (or --list-affected to preview)",
            exit_code=1,
        )

    resolved = 0
    skipped = 0
    for r in rows:
        key = DedupKey(platform=r.platform, target_url=r.target_url, account=r.account)
        try:
            _adjudicate_one(store, key, to_state, args.reason, run_id=r.run_id)
            resolved += 1
        except (ValueError, _sqlite_error()) as exc:
            # A concurrent change (reclaim, --forget) raced this row — skip it and
            # keep going so one mid-loop race doesn't abort the whole batch.
            skipped += 1
            print(f"adjudicate-bulk: skipped one row — {exc}", file=_sys.stderr)
    print(
        f"adjudicate-bulk: resolved {resolved} row(s) uncertain -> {to_state}"
        + (f"; {skipped} skipped (concurrent change)." if skipped else "."),
        file=_sys.stderr,
    )


def _sqlite_error() -> type[BaseException]:
    import sqlite3

    return sqlite3.Error


def _do_forget(args: Any) -> None:
    """Clear one dedup key → ``absent`` and append an audit entry. Single key
    only: a glob/wildcard in either field is rejected (exit 1) so a wrong pattern
    cannot silently mass-retire backlinks."""
    from backlink_publisher._util.errors import emit_error
    from backlink_publisher.idempotency import DedupKey, DedupStore
    from backlink_publisher.idempotency import audit_log

    platform, target_url = args.forget
    if not args.reason:
        emit_error("error: --forget requires --reason <text>", exit_code=1)
    if any("*" in v or "?" in v for v in (platform, target_url)):
        emit_error(
            "error: --forget takes a single concrete key; globs/wildcards are "
            "rejected (forget one key at a time)",
            exit_code=1,
        )

    key = DedupKey(platform=platform, target_url=target_url)
    store = DedupStore()
    record = store.get(key)
    from_state = record.state if record is not None else None

    # Append the audit entry BEFORE deleting so a crash mid-forget still leaves a
    # trail (the row simply remains; the operator re-runs). Canonical target_url
    # (key.target_url) is logged so U6's touched-key check matches the store key.
    audit_log.append_entry(
        action="forget",
        platform=key.platform,
        target_url=key.target_url,
        account=key.account,
        from_state=from_state,
        to_state="absent",
        reason=args.reason,
        run_id=getattr(args, "resume", None),
    )
    store.forget(key)
    if from_state is None:
        print(
            f"forget: key was already absent (platform={key.platform}); "
            "audit entry recorded.",
            file=sys.stderr,
        )
    else:
        print(
            f"forget: cleared {key.platform} key (was {from_state}); now "
            "re-publishable.",
            file=sys.stderr,
        )


def _do_list_uncertain(args: Any) -> None:
    """Print held (``uncertain``) dedup rows on stdout (the operator needs the
    target_url to adjudicate). Optional ``--platform`` filter."""
    from backlink_publisher.idempotency import DedupStore

    platform_filter = getattr(args, "platform", None)
    rows = DedupStore().list_by_state("uncertain", platform=platform_filter)
    if not rows:
        print("No uncertain (held) dedup rows.")
        return
    print(f"{'PLATFORM':<14}  {'STATE':<10}  {'RUN_ID':<28}  TARGET_URL")
    print("-" * 90)
    for r in rows:
        print(
            f"{r.platform:<14}  {r.state:<10}  {(r.run_id or ''):<28}  {r.target_url}"
        )
