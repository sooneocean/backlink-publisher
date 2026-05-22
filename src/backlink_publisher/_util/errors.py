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
    """Missing dependency or external precondition that requires user action.

    Family rule (Plan 2026-05-19-001):
        DependencyError = "user must take action" (install a tool, re-bind
        credentials, rebuild a config file).
        ExternalServiceError = "service reachable but rejected our well-formed
        call; retrying later may succeed".

    AuthExpiredError lives under this family for coordination with
    plan-012 §Unit 4 (velog cookie expired → DependencyError); see plan
    §Open Questions Resolved for the coordination-not-semantics rationale.
    """

    exit_code = 3


class ExternalServiceError(PipelineError):
    """External service failure (unreachable URL, API error, etc.).

    See ``DependencyError`` docstring for the family-vs-family rule.
    """

    exit_code = 4


class RegistryError(PipelineError):
    """Adapter registry violation — programmer bug, not user input.

    Raised by ``publishing.registry.register()`` when a registration
    violates the dofollow-gate contract: missing required ``dofollow=``
    after the gate flip, rationale too short for non-True dofollow, or
    re-attempting a name listed in ``_REJECTED_PLATFORMS`` without first
    deleting that entry. Exit code 5 (Internal) because these are
    code-author errors visible at import time, not runtime user errors.

    Plan 2026-05-20-009 Unit 1.
    """

    exit_code = 5


class AuthExpiredError(DependencyError):
    """Channel credentials expired — user must re-bind via webui or CLI.

    Plan 2026-05-19-001 Unit 1. Inherits from ``DependencyError`` (exit
    code 3, not 4) for coordination with plan-012 which raises
    ``DependencyError("velog cookie expired")`` for the same logical
    event. See plan §Key Technical Decisions.

    Construction validates ``channel`` against the
    ``cli._bind.channels.CHANNELS`` frozenset; ``UsageError`` is raised
    for unknown / traversal payloads (defense-in-depth against supply-
    chain adapters injecting ``channel="../evil"``).
    """

    exit_code = 3

    def __init__(self, *, channel: str, reason: str | None = None) -> None:
        # Local import avoids a top-level cycle (errors.py is imported
        # very early in package init; cli._bind.channels is leaf-level
        # but importing the whole cli package here would be premature).
        from backlink_publisher.cli._bind.channels import CHANNELS

        if not channel or channel not in CHANNELS:
            raise UsageError(
                f"AuthExpiredError: unknown channel {channel!r} "
                f"(allowed: {sorted(CHANNELS)})"
            )
        self.channel = channel
        self.reason = reason
        msg = f"channel {channel!r} credentials expired"
        if reason:
            msg += f" ({reason})"
        super().__init__(msg)


class BannerUploadError(DependencyError):
    """Media upload to a publisher platform failed.

    Plan 2026-05-20-004 Unit 1.  Sibling (NOT subclass) of
    ``AuthExpiredError`` — a banner upload failure is a media-API
    problem, not a credential failure.  Channel-status
    ``mark_expired`` must NOT fire on this exception (publish-time
    auth-flip is reserved for ``AuthExpiredError`` from
    ``adapter.publish()``).

    Honors ``config.image_gen.strict``: ``False`` (default) logs
    warn + publishes without banner; ``True`` propagates and fails
    the row.  Strict gating is implemented by
    ``publishing.banner_dispatcher.apply``, not by this class.
    """

    exit_code = 3


class ContentRejectedError(DependencyError):
    """Server accepted the request but rejected the content — cookie is still valid.

    Sibling (NOT subclass) of ``AuthExpiredError``.  ``mark_expired`` must
    NOT fire on this exception: the credentials are fine, but the publish
    was silently dropped for a non-auth reason (content validation, server-side
    rate-limit, slug collision, or an undocumented velog restriction).

    The operator should inspect the debug artifact cited in the log line and
    fix the underlying content issue rather than re-binding the channel.
    """

    exit_code = 3

    def __init__(self, *, channel: str, reason: str) -> None:
        self.channel = channel
        self.reason = reason
        super().__init__(f"channel {channel!r} content rejected ({reason})")


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