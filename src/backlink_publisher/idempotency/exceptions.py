from __future__ import annotations

class IdempotencyError(Exception):
    """Base class for idempotency store errors."""
    pass

class IdempotencyConflictError(IdempotencyError):
    """Raised when a single-flight operation fails due to concurrency."""
    pass

class IdempotencyStateError(IdempotencyError):
    """Raised when an invalid state transition is attempted."""
    pass
