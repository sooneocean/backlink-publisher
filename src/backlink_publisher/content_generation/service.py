"""Shared AI draft generation orchestration."""

from __future__ import annotations

from typing import Any

from backlink_publisher.llm.client import _redact_for_log

from .types import (
    ArticleProvider,
    DraftGenerationResult,
    GenerationRequest,
    ValidationIssue,
    ValidationResult,
)
from .validator import validate_draft


def _provider_name(provider: Any) -> str:
    return str(getattr(provider, "provider_name", provider.__class__.__name__))


def generate_draft(
    request: GenerationRequest,
    *,
    provider: ArticleProvider | None,
    fallback_body: str | None = None,
) -> DraftGenerationResult:
    """Generate one reviewable backlink draft or return a deterministic fallback."""
    if provider is None:
        body = fallback_body or ""
        validation = validate_draft(request, body)
        issue = ValidationIssue(
            code="provider_unavailable",
            message="AI provider is unavailable; template fallback was used.",
        )
        validation = ValidationResult(
            accepted=False,
            issues=[issue, *validation.issues],
        )
        return DraftGenerationResult(
            status="fallback_used",
            provider="template",
            body_markdown=body,
            validation=validation,
        )

    try:
        body = provider.generate_article_body(
            domain_label=request.context.get("domain_label") or request.main_domain,
            main_domain=request.main_domain,
            anchors=list(request.anchors),
            topic=request.topic,
            language=request.language,
        )
    except Exception as exc:
        body = fallback_body or ""
        validation = validate_draft(request, body)
        issue = ValidationIssue(
            code="provider_failed",
            message="AI provider failed; template fallback was used.",
        )
        return DraftGenerationResult(
            status="fallback_used" if fallback_body is not None else "failed",
            provider=_provider_name(provider),
            body_markdown=body,
            validation=ValidationResult(False, [issue, *validation.issues]),
            error=_redact_for_log(str(exc)),
        )

    validation = validate_draft(request, body)
    cover_prompt: str | None = None
    if validation.accepted and hasattr(provider, "generate_image_prompt"):
        try:
            cover_prompt = provider.generate_image_prompt(request.title, body)
        except Exception:
            cover_prompt = None

    return DraftGenerationResult(
        status="reviewable" if validation.accepted else "rejected",
        provider=_provider_name(provider),
        body_markdown=body,
        validation=validation,
        cover_prompt=cover_prompt,
    )
