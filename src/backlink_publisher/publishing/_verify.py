"""Three-tier verify dispatch for channel binding dashboard (Plan 2026-05-19-006 Unit 2).

Public entry point is ``verify_adapter_setup(platform, config, *, mode=..., payload=...)``
in ``adapters/__init__.py``. This module provides the dataclasses, exceptions, and
dispatch helpers that the public function delegates to for non-offline modes.

Backward-compat invariant: ``mode='offline'`` preserves the pre-Unit-2 contract
(raise ``DependencyError`` on failure, return ``None`` on success). Modes ``'live'``
and ``'dry-run'`` return a ``VerifyResult`` and never raise for auth failures.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

VerifyResultLiteral = Literal[
    "ok",
    "token_expired",
    "timeout",
    "never",
    "payload_invalid",
    "unverifiable_live",
]


@dataclass
class VerifyResult:
    """Returned by ``verify_adapter_setup(mode='live'|'dry-run')``.

    Shape mirrors ``AdapterResult`` (dataclass, no methods on hot path) so it
    serializes cleanly into ``/api/<channel>/{status,verify,dry-run}`` JSON.
    """

    ok: bool
    identity: Optional[str] = None
    last_verified_at: Optional[str] = None  # iso8601 UTC, None if never
    last_verify_result: VerifyResultLiteral = "never"
    blockers: list[str] = field(default_factory=list)
    dofollow: Optional[bool] = None  # None = unknown, True/False = confirmed


class DryRunInterceptError(Exception):
    """Raised inside ``_dry_run_intercept()`` when an adapter attempts a real
    HTTP send. The intercept is module-level on ``requests.Session.send`` so
    even adapters that forget to honor a dry-run flag cannot leak a real publish.

    Defense-in-depth per SEC-5 review finding: fail-safe rather than fail-open.
    Adapters using non-``requests`` HTTP libraries (e.g. urllib3 direct, SDKs)
    are NOT caught by this intercept — those must return
    ``last_verify_result='unverifiable_live'`` for dry-run mode.
    """


@contextlib.contextmanager
def dry_run_intercept() -> Iterator[None]:
    """Context manager that monkey-patches ``requests.Session.send`` to raise
    ``DryRunInterceptError`` instead of executing a real HTTP call.

    Usage::

        with dry_run_intercept():
            adapter.publish(payload, mode, config)  # any real POST → raises

    Restores the original ``Session.send`` on exit even if exceptions fire.
    """
    import requests

    original_send = requests.Session.send

    def _intercepted(self, request, **kwargs):  # noqa: ARG001
        raise DryRunInterceptError(
            f"dry-run intercept: refusing to {request.method} {request.url}"
        )

    requests.Session.send = _intercepted  # type: ignore[method-assign]  # reason: monkey-patching requests.Session.send for dry-run intercept
    try:
        yield
    finally:
        requests.Session.send = original_send  # type: ignore[method-assign]  # reason: restoring original after dry-run
