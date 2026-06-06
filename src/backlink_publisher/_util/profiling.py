"""CLI profiling utility — opt-in cProfile wrapper for performance analysis.

Usage:
    with profile_if_enabled(args):
        # your code here
        pass

Profiles are saved under the project cache directory with timestamped filenames.
"""

from __future__ import annotations

import cProfile
import os
import pstats
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from backlink_publisher.config.loader import _cache_dir


def _get_profile_dir() -> Path:
    """Get or create the profile output directory."""
    profile_dir = _cache_dir() / "profiles"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


@contextmanager
def profile_if_enabled(args: object | None = None) -> Iterator[None]:
    """Context manager that profiles the block if --profile is set on args.

    If args has a ``profile`` attribute that is truthy, cProfile runs and
    saves stats to ``<cache-dir>/profiles/<module>-<timestamp>.prof``.

    Args:
        args: Parsed argparse namespace with optional ``profile`` boolean attribute.

    Yields:
        None
    """
    if args is None or not getattr(args, "profile", False):
        yield
        return

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        yield
    finally:
        profiler.disable()
        profile_dir = _get_profile_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prof_path = profile_dir / f"profile-{timestamp}.prof"
        profiler.dump_stats(str(prof_path))

        # Also print a quick summary to stderr
        import io
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative")
        stats.print_stats(20)
        print(f"\nProfile saved to {prof_path}", flush=True)
        print(stream.getvalue(), flush=True)


def add_profile_arg(parser: Any) -> None:
    """Add --profile flag to an argparse parser.

    Args:
        parser: argparse.ArgumentParser instance.
    """
    parser.add_argument(
        "--profile",
        action="store_true",
        default=False,
        help="Enable cProfile profiling (saved to ~/.cache/backlink-publisher/profiles/)",
    )
