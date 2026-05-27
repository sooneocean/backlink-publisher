"""``comment`` — Comment Outreach Queue CLI (single console_script, subparsers).

Subcommands chain via stdin/stdout JSONL like the rest of the pipeline
(stdout = data, stderr = diagnostics, exit 0 on success):

    comment discover   seed URLs   -> CommentTarget JSONL   (detect comment region)
    comment import     CommentTarget JSONL in -> validated CommentTarget JSONL out
    comment qualify    CommentTarget JSONL -> QualificationResult JSONL
    comment brief      accept-decisions -> CommentBrief JSONL  (LLM-optional)
    comment status     manual ReviewStatus transitions (persistent store)

Design invariants (enforced by ``tests/test_comment_outreach_isolation.py``):
- This module and ``comment_outreach.*`` import nothing from the publishing
  adapter registry. The sole exception is a *lazy, function-local* import of
  the LLM provider inside the ``brief`` handler (added in plan Unit 7).
- No posting / browser-automation primitives anywhere.

Each handler is a thin shim: parse args, call one ``comment_outreach.<verb>``
entry function (imported lazily inside the handler), write JSONL. Closed-set
arguments are validated post-parse (never via argparse ``choices=``, which
exits 2 and clashes with ``UsageError``'s exit 1).
"""

from __future__ import annotations

import argparse
import sys

from backlink_publisher._util.errors import PipelineError, handle_error

EXIT_OK = 0
EXIT_NOT_IMPLEMENTED = 5  # scaffold marker; each unit replaces its stub


# ---------------------------------------------------------------------------
# Handlers — thin shims. Bodies are filled in by later plan units; until then
# they raise NotImplementedError (caught in main, mirroring cli/phase0_seal.py).
# ---------------------------------------------------------------------------
def _handle_discover(args: argparse.Namespace) -> int:
    from backlink_publisher.comment_outreach.discover import discover_targets

    discover_targets(args.input, args.output)
    return EXIT_OK


def _handle_import(args: argparse.Namespace) -> int:
    # Lazy, function-local import keeps `comment --help` and sibling verbs from
    # paying io_import's cost (and keeps the CLI module's import graph minimal).
    from backlink_publisher.comment_outreach.io_import import import_targets

    import_targets(args.input, args.output)
    return EXIT_OK


def _handle_qualify(args: argparse.Namespace) -> int:
    from backlink_publisher.comment_outreach.score import qualify_targets

    qualify_targets(args.input, args.output)
    return EXIT_OK


def _handle_brief(args: argparse.Namespace) -> int:
    # The provider is imported lazily inside brief._load_provider (function-local), so
    # importing this CLI module and running the other verbs never loads the registry.
    from backlink_publisher.comment_outreach.brief import brief_targets

    brief_targets(args.input, args.output)
    return EXIT_OK


def _handle_status(args: argparse.Namespace) -> int:
    from backlink_publisher._util.errors import UsageError
    from backlink_publisher.comment_outreach import schema
    from backlink_publisher.comment_outreach.store import set_status

    # Closed-set validation post-parse (never argparse choices=, which exits 2 and
    # clashes with UsageError's exit 1).
    if not args.target_id:
        raise UsageError("comment status requires a target_id")
    if not args.status:
        raise UsageError("comment status requires --set <status>")
    if args.status not in schema.STATUS_ENUM:
        raise UsageError(
            f"invalid status '{args.status}'; must be one of {sorted(schema.STATUS_ENUM)}"
        )

    record = set_status(
        args.target_id,
        args.status,
        reviewer=args.reviewer,
        comment_url=args.comment_url,
        final_comment_text=args.final_comment_text,
        result_notes=args.result_notes,
    )
    # Echo the resulting record to stdout (data) for confirmation / chaining.
    import json

    print(json.dumps(record, ensure_ascii=False))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comment",
        description=(
            "Comment Outreach Queue — find, qualify, draft, and track manual "
            "comment opportunities. Never posts comments or automates login."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    discover_p = sub.add_parser(
        "discover",
        help="Fetch operator-supplied exact public URLs and detect comment regions",
    )
    discover_p.add_argument(
        "--input", "-i", type=argparse.FileType("r"), default=None,
        help="Seed JSONL (default: stdin)",
    )
    discover_p.add_argument(
        "--output", "-o", type=argparse.FileType("w"), default=None,
        help="CommentTarget JSONL (default: stdout)",
    )
    discover_p.set_defaults(handler=_handle_discover)

    import_p = sub.add_parser(
        "import",
        help="Validate and ingest externally-provided CommentTarget records",
    )
    import_p.add_argument(
        "--input", "-i", type=argparse.FileType("r"), default=None,
        help="CommentTarget JSONL (default: stdin)",
    )
    import_p.add_argument(
        "--output", "-o", type=argparse.FileType("w"), default=None,
        help="Validated JSONL (default: stdout)",
    )
    import_p.set_defaults(handler=_handle_import)

    qualify_p = sub.add_parser(
        "qualify",
        help="Score CommentTargets and emit QualificationResult records",
    )
    qualify_p.add_argument(
        "--input", "-i", type=argparse.FileType("r"), default=None,
        help="CommentTarget JSONL (default: stdin)",
    )
    qualify_p.add_argument(
        "--output", "-o", type=argparse.FileType("w"), default=None,
        help="QualificationResult JSONL (default: stdout)",
    )
    qualify_p.set_defaults(handler=_handle_qualify)

    brief_p = sub.add_parser(
        "brief",
        help="Generate conservative CommentBrief drafts for accepted targets",
    )
    brief_p.add_argument(
        "--input", "-i", type=argparse.FileType("r"), default=None,
        help="QualificationResult JSONL (default: stdin)",
    )
    brief_p.add_argument(
        "--output", "-o", type=argparse.FileType("w"), default=None,
        help="CommentBrief JSONL (default: stdout)",
    )
    brief_p.set_defaults(handler=_handle_brief)

    status_p = sub.add_parser(
        "status",
        help="Record a manual ReviewStatus transition for a target",
    )
    status_p.add_argument("target_id", nargs="?", help="Target id to update")
    status_p.add_argument("--set", dest="status", metavar="STATUS", help="New review status")
    status_p.add_argument("--reviewer", help="Operator who reviewed/acted")
    status_p.add_argument("--comment-url", dest="comment_url", help="URL of the posted comment")
    status_p.add_argument("--final-comment-text", dest="final_comment_text",
                          help="The exact text actually posted (sensitive)")
    status_p.add_argument("--result-notes", dest="result_notes", help="Free-text outcome notes")
    status_p.set_defaults(handler=_handle_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Argparse dispatcher.

    Returns an exit code rather than calling ``sys.exit()`` so tests can call
    ``main()`` in-process and inspect the return value.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args) or EXIT_OK
    except PipelineError as exc:
        handle_error(exc)
    except NotImplementedError as exc:
        print(f"comment: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
