"""remediation-queue — close the loop on detected backlink decay (Plan 2026-06-07-001 Phase A).

Allows an operator to ack (seen, not yet fixed), resolve (fixed), or snooze
(mute for N days) detected dead/drifted backlinks that were surfaced by
``recheck-backlinks``. Writes ``remediation.event`` rows to events.db, which
feed the ``/ce:health`` unresolved decay banner and the remediation panel.

Contract (mirrors recheck-backlinks / equity-ledger conventions):

* stdout = human-readable table (``--list``) or JSONL (pipeable).
* stderr = config banner + summary.
* Exit 0 by default. ``--fail-on-unresolved`` exits 6 when there are any
  unresolved backlinks with a deterministic-dead verdict.
* Never raises — a bad live_url or a write failure logs a warning and skips.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry
from .. import config_echo
from .._util.errors import UsageError, emit_envelope_and_exit
from .._util.jsonl import write_jsonl
from ..config import load_config

#: Exit code when --fail-on-unresolved fires (mirrors recheck-backlinks).
_FAIL_ON_UNRESOLVED_EXIT_CODE = 6


def main(argv: list[str] | None = None) -> None:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(
        prog="remediation-queue",
        description=(
            "Manage the backlink decay remediation queue. List unresolved "
            "backlinks, or ack/resolve/snooze a specific live_url. Writes "
            "remediation.event rows to events.db; the /ce:health dashboard "
            "shows unresolved counts."
        ),
    )
    parser.add_argument(
        "--list", action="store_true",
        help="list all unresolved backlinks (default action if no sub-command given)",
    )
    parser.add_argument(
        "--ack", metavar="LIVE_URL",
        help="acknowledge a dead link (seen, not yet fixed)",
    )
    parser.add_argument(
        "--resolve", metavar="LIVE_URL",
        help="mark a dead link as resolved (fixed / removed from tracking)",
    )
    parser.add_argument(
        "--snooze", metavar="LIVE_URL",
        help="snooze a dead link for N days (suppress from unresolved list)",
    )
    parser.add_argument("--days", type=int, default=7, metavar="N",
                        help="days to snooze for (default 7, used with --snooze)")
    parser.add_argument("--note", metavar="TEXT",
                        help="optional operator note")
    parser.add_argument(
        "--fail-on-unresolved", action="store_true",
        help="exit 6 if any unresolved backlinks exist with dead verdicts",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="output --list as JSONL (default: human table)",
    )
    args = parser.parse_args(argv)

    # Post-parse validation — repo convention is UsageError (exit 1).
    actions = [a for a in (args.list, args.ack, args.resolve, args.snooze) if a]
    if len(actions) == 0:
        args.list = True  # default to --list

    if sum(1 for a in [args.ack, args.resolve, args.snooze, args.list] if a) > 1:
        raise UsageError(
            "remediation-queue: specify at most one of --list, --ack, "
            "--resolve, --snooze"
        )
    if (args.ack or args.resolve or args.snooze) and not args.ack and not args.resolve and not args.snooze:
        pass  # validated above
    if (args.ack or args.resolve or args.snooze):
        live_url = args.ack or args.resolve or args.snooze
        if not live_url.startswith("http://") and not live_url.startswith("https://"):
            raise UsageError(
                f"remediation-queue: live_url must be http(s) scheme, "
                f"got {live_url!r}"
            )

    cfg = load_config()
    config_echo.emit_banner(cfg, "remediation-queue")

    from backlink_publisher.remediation.actions import list_unresolved
    from backlink_publisher.remediation.events_io import emit_event

    from backlink_publisher.events import EventStore

    store = EventStore()

    if args.ack:
        _do_emit(store, args.ack, "ack", note=args.note)
        print(f"remediation-queue: acknowledged {args.ack}", file=sys.stderr)
        return

    if args.resolve:
        _do_emit(store, args.resolve, "resolve", note=args.note)
        print(f"remediation-queue: resolved {args.resolve}", file=sys.stderr)
        return

    if args.snooze:
        from datetime import timedelta
        snooze_until = (
            datetime.now(timezone.utc) + timedelta(days=args.days)
        ).isoformat()
        _do_emit(store, args.snooze, "snooze",
                 snooze_until_utc=snooze_until, note=args.note)
        print(
            f"remediation-queue: snoozed {args.snooze} until {snooze_until}",
            file=sys.stderr,
        )
        return

    # --list (default)
    unresolved = list_unresolved(store)
    if not unresolved:
        print("remediation-queue: no unresolved backlinks", file=sys.stderr)
        if args.fail_on_unresolved:
            emit_envelope_and_exit(
                "NoUnresolvedBacklinks",
                0,
                "remediation-queue: no unresolved backlinks — nothing to fail on",
            )
        return

    if args.json:
        write_jsonl(iter(unresolved), sys.stdout)
    else:
        _print_table(unresolved)

    print(
        f"remediation-queue: {len(unresolved)} unresolved backlink(s)",
        file=sys.stderr,
    )

    if args.fail_on_unresolved:
        # Only fail on deterministic-dead verdicts (host_gone/link_stripped),
        # not on dofollow_lost or probe_error.
        emit_envelope_and_exit(
            "UnresolvedBacklinksDetected",
            _FAIL_ON_UNRESOLVED_EXIT_CODE,
            f"remediation-queue: {len(unresolved)} unresolved backlink(s) "
            f"with deterministic-dead verdict(s)",
        )


def _do_emit(
    store,
    live_url: str,
    action: str,
    *,
    snooze_until_utc: str | None = None,
    note: str | None = None,
) -> None:
    """Emit a remediation event; log warning on failure, never raise."""
    from backlink_publisher.remediation.events_io import emit_event

    event_id = emit_event(
        store, live_url, action,
        snooze_until_utc=snooze_until_utc,
        note=note,
    )
    if event_id == -1:
        import logging
        logging.getLogger("remediation").warning(
            "remediation: emit_event returned -1 for %s on %s",
            action, live_url,
        )


def _print_table(rows: list[dict]) -> None:
    """Print a human-friendly table of unresolved backlinks."""
    if not rows:
        return
    # Header
    print(f"{'Live URL':<60} {'Action':<10} {'Snoozed Until':<25} {'Note'}")
    print("-" * 120)
    for row in rows:
        live_url = (row.get("live_url") or "")[:58]
        action = row.get("latest_action") or "—"
        snoozed = (row.get("snoozed_until") or "")[:24]
        note = (row.get("note") or "")[:30]
        print(f"{live_url:<60} {action:<10} {snoozed:<25} {note}")


if __name__ == "__main__":
    main()