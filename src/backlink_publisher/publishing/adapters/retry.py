"""Shared exponential-backoff retry helper for adapter publish calls."""

from __future__ import annotations

import json
import random
import sys
import time
from typing import Any, Callable, TypeVar

from backlink_publisher._util.errors import DependencyError, ExternalServiceError

T = TypeVar("T")

MAX_ATTEMPTS: int = 3
BACKOFF_BASE: int = 2
JITTER_FACTOR: float = 0.15

from enum import Enum
import re

# HTTP status codes that indicate a transient server-side failure worth retrying.
# Only used by call-site is_retryable predicates — not enforced here.
# NOTE: 5xx errors are NOT retried because neither Blogger API v3 nor Medium API
# document idempotency guarantees. A 5xx response could mean the resource was
# already created server-side (e.g., server timeout after POST succeeded but before
# sending response). Retrying without deduplication risks duplicate posts.
# See: https://sophiabits.com/blog/you-cant-always-retry-a-5xx
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429})


class ErrorClass(str, Enum):
    TRANSIENT = "transient"
    AUTH_EXPIRED = "auth_expired"
    HTTP_5XX = "http_5xx"
    SSRF_BLOCKED = "ssrf_blocked"
    UNEXPECTED = "unexpected"


_HTTP_5XX_RE = re.compile(r"\b5[0-9]{2}\b")


def classify_exception(exc: Exception) -> ErrorClass:
    """Classify an exception to the ErrorClass taxonomy.

    Used by the publisher and event projectors to route and store failure reasons.
    """
    from backlink_publisher._util.errors import AuthExpiredError, ExternalServiceError

    if isinstance(exc, AuthExpiredError):
        return ErrorClass.AUTH_EXPIRED

    msg = str(exc)
    if "ssrf_blocked" in msg or "ssrf_redirect" in msg or "ssrf_https_downgrade" in msg:
        return ErrorClass.SSRF_BLOCKED

    if _HTTP_5XX_RE.search(msg):
        return ErrorClass.HTTP_5XX

    if isinstance(exc, ExternalServiceError):
        return ErrorClass.TRANSIENT

    return ErrorClass.UNEXPECTED


def is_transient_reason(reason: str) -> bool:
    """Return True if the content_fetch failure reason is transient and safe to retry.

    Used by content_fetch to decide whether to retry a GET request.
    """
    return reason in {"timeout", "network_error", "http_5xx"}


def retry_transient_call(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[Exception], bool],
    max_attempts: int = MAX_ATTEMPTS,
    backoff_base: int = BACKOFF_BASE,
    jitter: float = JITTER_FACTOR,
    adapter: str = "",
) -> T:
    """Call fn() with exponential-backoff retry on transient failures.

    fn() should be a raw API call that has NOT yet converted exceptions to
    ExternalServiceError.  ExternalServiceError and DependencyError will never
    be raised from a well-behaved fn(), but is_retryable must return False for
    them as a belt-and-suspenders guard.

    On a non-retryable exception OR after exhausting max_attempts, the last
    caught exception is re-raised with bare ``raise`` to preserve its type,
    message, and traceback exactly — required so publish_backlinks.py can route
    DependencyError (exit 3) vs ExternalServiceError (exit 4) correctly.

    Stderr format (R3a): {"level":"WARN","msg":"retrying (attempt N/M): …
    — waiting Xs","adapter":"…"}
    No response bodies, headers, or credentials are emitted.
    """
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except (ExternalServiceError, DependencyError):
            # These must never be retried — re-raise immediately via bare raise
            # so the caller's except block sees the original type.
            raise
        except Exception as exc:
            last_exc = exc
            if not is_retryable(exc):
                raise
            if attempt == max_attempts:
                raise

            wait = float(backoff_base ** attempt) * random.uniform(
                1.0 - jitter, 1.0 + jitter
            )
            exc_name = type(exc).__name__
            _emit_retry(attempt, max_attempts, exc_name, wait, adapter)
            time.sleep(wait)

    # Unreachable, but satisfies the type checker.
    assert last_exc is not None
    raise last_exc  # pragma: no cover


def _emit_retry(
    attempt: int,
    max_attempts: int,
    exc_name: str,
    wait: float,
    adapter: str,
) -> None:
    """Write a structured retry warning to stderr (R3a — no credentials/bodies)."""
    msg: dict[str, Any] = {
        "level": "WARN",
        "msg": f"retrying (attempt {attempt}/{max_attempts}): {exc_name} — waiting {wait:.1f}s",
        "adapter": adapter,
    }
    print(json.dumps(msg), file=sys.stderr, flush=True)
