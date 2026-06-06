"""Shared types for AI-assisted backlink content generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


GenerationStatus = Literal["reviewable", "rejected", "failed", "fallback_used"]


@dataclass(frozen=True)
class GenerationRequest:
    target_url: str
    main_domain: str
    platform: str
    language: str
    anchors: tuple[str, ...] = ()
    topic: str = ""
    title: str = ""
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def issue_codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


@dataclass(frozen=True)
class DraftGenerationResult:
    status: GenerationStatus
    provider: str
    body_markdown: str
    validation: ValidationResult
    cover_prompt: str | None = None
    error: str | None = None

    @property
    def issue_codes(self) -> list[str]:
        return self.validation.issue_codes


class ArticleProvider(Protocol):
    provider_name: str

    def generate_article_body(self, **kwargs: Any) -> str:
        ...

    def generate_image_prompt(self, title: str, content: str) -> str:
        ...
