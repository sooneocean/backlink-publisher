"""AI-assisted backlink content generation service boundary."""

from .service import generate_draft
from .openai_sdk import OpenAISDKArticleProvider
from .types import (
    DraftGenerationResult,
    GenerationRequest,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "DraftGenerationResult",
    "GenerationRequest",
    "OpenAISDKArticleProvider",
    "ValidationIssue",
    "ValidationResult",
    "generate_draft",
]
