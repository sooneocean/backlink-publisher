"""Tests for gated SSL context behavior (gated by real_ssrf_check marker).

The get_ssl_context() helper must verify by default and allow opt-in insecure
verification via BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL=1 for self-signed targets.
"""

from __future__ import annotations

import os
import ssl

import pytest


@pytest.mark.real_ssrf_check
def test_ssl_context_verifies_by_default():
    """Default SSL context must verify certificates."""
    from backlink_publisher._util.ssl_ctx import get_ssl_context

    # Ensure the env var is not set
    os.environ.pop("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL", None)

    ctx = get_ssl_context()
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.real_ssrf_check
def test_ssl_context_allows_insecure_when_env_set():
    """Env var enables loose certificate verification."""
    from backlink_publisher._util.ssl_ctx import get_ssl_context

    # Set env var to allow insecure
    original = os.environ.get("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL")
    os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = "1"

    try:
        ctx = get_ssl_context()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE
    finally:
        if original is None:
            os.environ.pop("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL", None)
        else:
            os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = original


@pytest.mark.real_ssrf_check
def test_ssl_context_env_value_must_be_exactly_one():
    """Only '1' enables insecure; other values verify."""
    from backlink_publisher._util.ssl_ctx import get_ssl_context

    original = os.environ.get("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL")

    try:
        os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = "0"
        ctx = get_ssl_context()
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

        os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = "true"
        ctx = get_ssl_context()
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED

        os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = ""
        ctx = get_ssl_context()
        assert ctx.check_hostname is True
        assert ctx.verify_mode == ssl.CERT_REQUIRED
    finally:
        if original is None:
            os.environ.pop("BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL", None)
        else:
            os.environ["BACKLINK_PUBLISHER_ALLOW_INSECURE_SSL"] = original