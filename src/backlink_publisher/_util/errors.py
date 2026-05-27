"""Error definitions for the backlink pipeline."""

from __future__ import annotations

from typing import NoReturn


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


class AntiBotChallengeError(ExternalServiceError):
    """Anti-bot interstitial (Cloudflare JS challenge, CAPTCHA wall) blocked
    a well-formed publish request.

    Subclass of ``ExternalServiceError`` (exit code 4), NOT ``DependencyError``.
    Rationale (Plan 2026-05-25-001 Unit 4): credential-less form-POST adapters
    are a *single-entry* chain — there is no CDP fallback after them. If a
    challenge surfaced as ``DependencyError`` it would be re-raised verbatim by
    the registry dispatch loop and become indistinguishable from "platform not
    configured". Propagating it as a service-rejection (semantics mirror
    ``instant_web``'s ``content_is_blocked``) keeps "the site blocked us"
    cleanly separate from "the operator never set this up".

    Never carries the POST body or response HTML in its message — see the log
    scrubber contract in ``_util.logger``.
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
    """Server accepted the request but rejected the content — cookie valid.

    Sibling (NOT subclass) of ``AuthExpiredError``.  ``mark_expired`` must
    NOT fire on this exception: the credentials are fine, but the publish
    was silently dropped for a non-auth reason (content validation,
    server-side rate-limit, slug collision, or an undocumented velog
    restriction).

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


# exit_code → canonical class name, for emit_error() which has no exception object.
# Mirrors the PipelineError hierarchy above.
_EXIT_CODE_CLASS_NAME = {
    1: "UsageError",
    2: "InputValidationError",
    3: "DependencyError",
    4: "ExternalServiceError",
    5: "InternalError",
}


def _emit_error_envelope(error_class: str, exit_code: int, message: str) -> None:
    """Emit the machine-readable typed-error line to stderr (Phase 1 contract).

    Additive: callers print the human-readable text first, then this line. The
    WebUI bridge parses it into a typed ``PipeResult.error`` instead of slicing
    ``stderr[:200]``. Best-effort — a failure here must never mask the original
    error (the human text + ``SystemExit`` already happened / will happen).
    """
    import sys

    try:
        from backlink_publisher._util.error_envelope import ErrorEnvelope

        print(
            ErrorEnvelope(
                error_class=error_class, exit_code=exit_code, message=message
            ).serialize(),
            file=sys.stderr,
            flush=True,
        )
    except Exception:  # pragma: no cover - envelope emission is best-effort
        pass


def emit_error(
    message: str, exit_code: int = 5, *, error_class: str | None = None
) -> NoReturn:
    """Print diagnostic to stderr and exit. Always raises ``SystemExit`` —
    ``NoReturn`` lets type checkers narrow past ``emit_error(...)`` guards.

    ``error_class`` overrides the envelope's class name. Pass it when the caller
    holds a specific exception whose type the operator must see (e.g.
    ``AuthExpiredError``) — otherwise the class is derived from ``exit_code`` via
    ``_EXIT_CODE_CLASS_NAME``, which would collapse it to the coarse family name
    (``DependencyError`` for exit 3) and defeat the typed-error contract.
    """
    import sys

    print(message, file=sys.stderr, flush=True)
    _emit_error_envelope(
        error_class or _EXIT_CODE_CLASS_NAME.get(exit_code, "PipelineError"),
        exit_code,
        message,
    )
    raise SystemExit(exit_code)


def emit_envelope_and_exit(error_class: str, exit_code: int, message: str) -> NoReturn:
    """Attach the typed-error envelope, then ``raise SystemExit(exit_code)``.

    For fatal-exit sites that have ALREADY printed their own domain-specific
    human-readable diagnostics (a per-row validation-error loop, a plan-check
    schema-violation line, a publish-epilogue failure list) and now need only the
    machine-readable envelope before exiting. Unlike :func:`emit_error` it prints
    no human text of its own, so existing stderr stays byte-identical and the
    additive sentinel line is the only new output. ``error_class`` is the
    operator-facing type string — an exception class name when one is in hand, or
    the canonical name for the exit code (see ``_EXIT_CODE_CLASS_NAME``) / a
    descriptive name for domain exit codes outside the 1–5 taxonomy (e.g. the
    anchor-distribution alarm's exit 6, plan-check drift's exit 7).
    """
    _emit_error_envelope(error_class, exit_code, message)
    raise SystemExit(exit_code)


def handle_error(exc: PipelineError) -> None:
    """Handle a pipeline error by printing to stderr and exiting."""
    import sys

    print(str(exc.message), file=sys.stderr, flush=True)
    # error_class = the specific exception type (e.g. "AuthExpiredError",
    # "ContentRejectedError") so the operator sees the real error, not a coarse
    # bucket. (classify_exception's 5-value ErrorClass would collapse
    # ContentRejectedError → "unexpected", defeating the Phase 1 success criterion.)
    _emit_error_envelope(type(exc).__name__, exc.exit_code, str(exc.message))
    raise SystemExit(exc.exit_code)


def handle_unexpected_error(exc: Exception) -> None:
    """Handle an unexpected exception."""
    import sys

    print(f"unexpected error: {exc}", file=sys.stderr, flush=True)
    _emit_error_envelope(type(exc).__name__, 5, f"unexpected error: {exc}")
    raise SystemExit(5)
