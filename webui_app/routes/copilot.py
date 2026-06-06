"""Pro Mode Copilot — deterministic advisor route (Plan U3) + Q&A (U5).

Read-only and keyless: ``GET /copilot/advice`` aggregates the in-process
advisory tools (via the ``copilot_advisor`` service), ranks them
deterministically (``copilot_ranking``), and returns JSON for the side panel.
Per-tool failures surface honestly (no false-green).

``POST /copilot/ask`` (U5) answers natural-language questions via the
configured LLM.  Requires a bound LLM key in ``llm-settings.json``; returns
``400`` when not configured.  The live-tool runs remain a separate, gated seam
(v3).
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from ..helpers.contexts import _load_llm_settings
from ..helpers.security import (
    _check_bind_origin_or_abort,
    _refuse_when_allow_network,
)
from ..services.copilot_advisor import cached_aggregate
from ..services.copilot_ranking import rank
from ..services.copilot_recon import log_invocation
from backlink_publisher.llm.http_guard import safe_post_json

# Control / bidi characters — same regex used by llm.client._sanitize_input
# but without the XML-attribute escaping (user questions are not spliced into
# XML attributes, so escaping would mangle the prompt).
_QNA_UNSAFE_CHARS = re.compile(
    "[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2066-\u2069]"
)

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


# ── Q&A (U5): LLM-backed natural-language questions ──────────────────────────


def _sanitize_question(text: str, max_len: int = 500) -> str:
    """Strip control/bidi chars and clamp length.

    Deliberately does NOT XML-escape — user questions are spliced into the
    LLM ``user`` message, not into XML attribute values, so escaping would
    mangle whitespace and punctuation for downstream models.
    """
    if not isinstance(text, str):
        return ""
    cleaned = _QNA_UNSAFE_CHARS.sub("", text)
    return cleaned[:max_len]


@bp.route("/copilot/ask", methods=["POST"])
def copilot_ask():
    """Answer a natural-language question via the configured LLM.

    Requires a bound LLM key (``endpoint`` + ``api_key`` in ``llm-settings.json``).
    Returns ``400`` when unconfigured, ``502`` on LLM failure, ``200`` with
    ``{"answer": "..."}`` on success.  No identifying data is logged.
    """
    settings = _load_llm_settings()
    endpoint = settings.get("endpoint", "").rstrip("/")
    api_key = settings.get("api_key", "")
    model = settings.get("model", "gpt-3.5-turbo")

    if not endpoint or not api_key:
        return jsonify({
            "error": "llm_not_configured",
            "detail": "请在设置中绑定 LLM API Key",
        }), 400

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "bad_request", "detail": "请求体必须是 JSON"}), 400
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "bad_request", "detail": "question 字段不能为空"}), 400

    safe_question = _sanitize_question(question)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 SEO 和外链策略助手。请用中文简洁回答。"
                    "不要重复敏感数据。"
                ),
            },
            {"role": "user", "content": safe_question},
        ],
        "temperature": settings.get("temperature", 0.7),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    chat_url = f"{endpoint}/chat/completions"

    try:
        status, body = safe_post_json(chat_url, headers, payload, timeout=60)
    except ValueError as exc:
        # safe_post_json raises ValueError on guard violations
        # (redirect, bad content-type, too-large body)
        return jsonify({
            "error": "llm_call_failed",
            "detail": f"LLM 请求被拒绝: {exc}",
        }), 502
    except Exception as exc:
        return jsonify({
            "error": "llm_call_failed",
            "detail": f"LLM 请求异常: {exc}",
        }), 502

    if status != 200:
        return jsonify({
            "error": "llm_call_failed",
            "detail": f"LLM 返回 HTTP {status}",
        }), 502

    try:
        answer = body["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return jsonify({
            "error": "llm_response_invalid",
            "detail": "LLM 响应格式异常",
        }), 502

    if not isinstance(answer, str) or not answer.strip():
        return jsonify({
            "error": "llm_response_empty",
            "detail": "LLM 返回了空内容",
        }), 502

    log_invocation("qa", "/copilot/ask", {})
    return jsonify({"answer": answer})
