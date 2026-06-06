"""Credential lifecycle — health checks, proactive refresh, expiry detection.

Wave 2 (Self-Healing Credential Renewal): pre-publish credential gates that
verify and refresh tokens BEFORE the publish loop starts, preventing mid-run
AuthExpiredError (exit 3) when a token expires during a batch.
"""
from .health import CredentialHealth, CredentialStatus, check_credentials, CredentialCheckResult

__all__ = [
    "CredentialHealth",
    "CredentialStatus",
    "CredentialCheckResult",
    "check_credentials",
]
