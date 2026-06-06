"""Deterministic validation for AI-generated backlink drafts."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .types import GenerationRequest, ValidationIssue, ValidationResult

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_CONTROL_OR_BIDI_RE = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2066-\u2069]")
_UNSAFE_TEXT_RE = re.compile(
    r"(ignore previous instructions|system prompt|developer message|api[_ -]?key|"
    r"password|secret credentials|publish secret)",
    re.IGNORECASE,
)


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _norm_url(url: str) -> str:
    return url.rstrip("/")


def _issue(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, message=message)


def validate_draft(request: GenerationRequest, body_markdown: str) -> ValidationResult:
    """Validate generated Markdown before it can enter review/publish flow."""
    issues: list[ValidationIssue] = []
    body = body_markdown or ""
    target = _norm_url(request.target_url)
    target_host = _host(request.target_url)

    if not body.strip():
        issues.append(_issue("empty_body", "Generated body is empty."))

    if _CONTROL_OR_BIDI_RE.search(body):
        issues.append(_issue("unsafe_control_chars", "Generated body contains unsafe control characters."))

    if _UNSAFE_TEXT_RE.search(body):
        issues.append(_issue("unsafe_instructional_text", "Generated body contains prompt or secret related text."))

    links = [(label.strip(), _norm_url(url)) for label, url in _MARKDOWN_LINK_RE.findall(body)]
    target_links = [
        (label, url)
        for label, url in links
        if url == target or (target_host and _host(url) == target_host)
    ]
    if not target_links:
        issues.append(_issue("missing_target_link", "Generated body does not link to the target domain."))

    required_anchors = [anchor for anchor in request.anchors if anchor.strip()]
    if required_anchors:
        labels = {label.casefold() for label, _url in target_links}
        if not any(anchor.casefold() in labels for anchor in required_anchors):
            issues.append(_issue("missing_required_anchor", "Generated body does not use a required target anchor."))

    words = re.findall(r"\S+", body)
    if len(words) < 20:
        issues.append(_issue("too_short", "Generated body is too short for reviewable backlink content."))

    accepted = not any(issue.severity == "error" for issue in issues)
    return ValidationResult(accepted=accepted, issues=issues)
