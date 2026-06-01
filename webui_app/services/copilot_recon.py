"""Copilot invocation logging via the RECON channel (Plan U7).

v1 records advisor/Q&A invocations as a structured, **non-identifying** RECON
line — no persistent store, no `webui_store` registry entry. The fields are an
explicit allowlist ({kind, tool_or_route, counts}); domains, question text,
answer text, and secrets are never logged. The full persistent action-audit
store (with action/params/authorization_tier + tamper-evidence) is deferred to
v3 and designed against real two-layer-auth requirements.

RECON bypasses the --log-level gate so the operator always sees the
reconciliation signal; the rest of the advisor path keeps `stderr` empty.
"""

from __future__ import annotations

from typing import Literal

from backlink_publisher._util.logger import get_logger

_logger = get_logger("copilot")

InvocationKind = Literal["advisor", "qa"]


def log_invocation(
    kind: InvocationKind,
    tool_or_route: str,
    counts: dict[str, int],
) -> None:
    """Emit one non-identifying RECON line for a Copilot invocation.

    Only the allowlisted, non-identifying fields are emitted. ``counts`` values
    are coerced to ints and any non-int is dropped, so a caller cannot smuggle a
    domain/URL/answer string through the counts map.
    """
    safe_counts = {
        str(key): int(value)
        for key, value in counts.items()
        if isinstance(value, bool) is False and isinstance(value, int)
    }
    _logger.recon(
        "copilot invocation",
        kind=kind,
        tool_or_route=tool_or_route,
        counts=safe_counts,
    )
