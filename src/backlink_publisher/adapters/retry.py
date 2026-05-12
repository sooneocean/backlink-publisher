"""Shared exponential-backoff retry helper for adapter publish calls."""

from __future__ import annotations

import json
import random
import sys
import time
from typing import Any, Callable, TypeVar

from ..errors import DependencyError, ExternalServiceError

T = TypeVar("T")

MAX_ATTEMPTS: int = 3
BACKOFF_BASE: int = 2
JITTER_FACTOR: float = 0.15

# HTTP status codes that indicate a transient server-side failure worth retrying.
# Only used by call-site is_retryable predicates — not enforced here.
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


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
        "msg": f"retrying (attempt {attempt + 1}/{max_attempts}): {exc_name} — waiting {wait:.1f}s",
        "adapter": adapter,
    }
    print(json.dumps(msg), file=sys.stderr, flush=True)
