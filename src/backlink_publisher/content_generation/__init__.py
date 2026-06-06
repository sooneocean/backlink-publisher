"""AI-assisted backlink content generation service boundary."""

from .service import generate_draft
from .types import (
    DraftGenerationResult,
    GenerationRequest,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "DraftGenerationResult",
    "GenerationRequest",
    "ValidationIssue",
    "ValidationResult",
    "generate_draft",
]
