"""Error definitions for the backlink pipeline."""

from __future__ import annotations


class PipelineError(Exception):
    """Base exception for pipeline errors."""

    exit_code: int = 5

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class UsageError(PipelineError):
    """CLI usage error."""

    exit_code = 1


class InputValidationError(PipelineError):
    """Input validation failure."""

    exit_code = 2


class DependencyError(PipelineError):
    """Missing dependency (e.g. OpenCLI not installed)."""

    exit_code = 3


class ExternalServiceError(PipelineError):
    """External service failure (unreachable URL, API error, etc.)."""

    exit_code = 4


class InternalError(PipelineError):
    """Unexpected internal error."""

    exit_code = 5


def emit_error(message: str, exit_code: int = 5) -> None:
    """Print diagnostic to stderr and exit."""
    import sys

    print(message, file=sys.stderr, flush=True)
    raise SystemExit(exit_code)


def handle_error(exc: PipelineError) -> None:
    """Handle a pipeline error by printing to stderr and exiting."""
    import sys

    print(str(exc.message), file=sys.stderr, flush=True)
    raise SystemExit(exc.exit_code)


def handle_unexpected_error(exc: Exception) -> None:
    """Handle an unexpected exception."""
    import sys

    print(f"unexpected error: {exc}", file=sys.stderr, flush=True)
    raise SystemExit(5)