"""Pro Mode Copilot — deterministic advisor route (Plan U3).

Read-only and keyless: ``GET /copilot/advice`` aggregates the in-process
advisory tools (via the ``copilot_advisor`` service), ranks them
deterministically (``copilot_ranking``), and returns JSON for the side panel.
Per-tool failures surface honestly (no false-green). The LLM Q&A layer and any
live-tool runs are separate, gated seams (v3 / shipped dark).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..helpers.security import (
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
)
from ..services.copilot_advisor import cached_aggregate
from ..services.copilot_ranking import rank
from ..services.copilot_recon import log_invocation

bp = Blueprint("copilot", __name__)


def _resolve_stale_days() -> int:
    try:
        days = int(request.args.get("stale_days", 30))
    except (TypeError, ValueError):
        return 30
    return days if days > 0 else 30


@bp.route("/copilot/advice", methods=["GET"])
def copilot_advice():
    """Ranked, source-traceable advice across the on-render advisory tools."""
    stale_days = _resolve_stale_days()
    page = request.args.get("page") or None
    agg = cached_aggregate(stale_days=stale_days)
    ranked = [r.to_dict() for r in rank(agg.findings)]
    log_invocation("advisor", "/copilot/advice", agg.counts())
    return jsonify(
        {
            "findings": ranked,
            "degraded": agg.degraded,
            "per_tool_status": [
                {
                    "tool": r.tool,
                    "ok": r.ok,
                    "outcome": r.outcome,
                    "error_code": r.error_code,
                }
                for r in agg.tool_results
            ],
            "page_context": page,
        }
    )


@bp.route("/copilot/run-live", methods=["POST"])
def copilot_run_live():
    """Reserved v3 seam: an explicit, operator-initiated live preflight/canary
    run. Not implemented in v1, but ships WITH the orthogonal origin guards
    wired (in addition to the app-level CSRF guard) so v3 inherits a correctly
    guarded endpoint rather than a CSRF-shaped hole."""
    _refuse_when_allow_network()
    _check_bind_origin_or_abort()
    return jsonify({"error": "not_implemented", "detail": "live runs land in v3"}), 501
