"""Shared CLI argument helpers — reduces argparse boilerplate across 27 entrypoints.

Usage::

    from backlink_publisher.cli._helpers import common_args, add_verbose, add_dry_run

    parser = argparse.ArgumentParser(prog="my-cli")
    common_args(parser)
    parser.add_argument("--my-flag", ...)
    args = parser.parse_args()

Provides a consistent ``--help``, ``--verbose``, ``--dry-run``, ``--json`` set
so every CLI entrypoint doesn't re-declare the same five flags.
"""

from __future__ import annotations

import argparse


def add_verbose(parser: argparse.ArgumentParser) -> None:
    """Add a ``--verbose`` / ``-v`` flag."""
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Increase output verbosity",
    )


def add_dry_run(parser: argparse.ArgumentParser) -> None:
    """Add a ``--dry-run`` / ``-n`` flag."""
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Perform a dry run without making changes",
    )


def add_json_output(parser: argparse.ArgumentParser) -> None:
    """Add a ``--json`` flag for JSON output."""
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (instead of human-readable)",
    )


def add_stdin_jsonl(parser: argparse.ArgumentParser) -> None:
    """Add a ``--stdin`` / ``-`` positional convention docstring (no flag needed)."""
    # stdin-based CLIs don't need an extra flag — just a help note.
    # This is a no-op placeholder for documentation consistency.
    pass


def common_args(
    parser: argparse.ArgumentParser,
    *,
    verbose: bool = True,
    dry_run: bool = False,
    json_output: bool = False,
) -> None:
    """Add commonly-used flags to an argument parser.

    Parameters
    ----------
    parser:
        The argument parser to decorate.
    verbose:
        If True, add ``--verbose`` / ``-v``.
    dry_run:
        If True, add ``--dry-run`` / ``-n``.
    json_output:
        If True, add ``--json``.
    """
    if verbose:
        add_verbose(parser)
    if dry_run:
        add_dry_run(parser)
    if json_output:
        add_json_output(parser)