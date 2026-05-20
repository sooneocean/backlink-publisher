import pytest
from backlink_publisher.publishing.adapters.retry import (
    ErrorClass,
    classify_exception,
    is_transient_reason,
)
from backlink_publisher._util.errors import (
    AuthExpiredError,
    ExternalServiceError,
)

def test_error_class_enum_values():
    assert ErrorClass.TRANSIENT == "transient"
    assert ErrorClass.AUTH_EXPIRED == "auth_expired"
    assert ErrorClass.HTTP_5XX == "http_5xx"
    assert ErrorClass.SSRF_BLOCKED == "ssrf_blocked"
    assert ErrorClass.UNEXPECTED == "unexpected"

def test_classify_auth_expired():
    exc = AuthExpiredError(channel="velog", reason="cookie expired")
    assert classify_exception(exc) == ErrorClass.AUTH_EXPIRED

def test_classify_external_service_error():
    exc = ExternalServiceError("rate limited")
    assert classify_exception(exc) == ErrorClass.TRANSIENT

def test_classify_http_5xx():
    exc = Exception("HTTP Error 502 Bad Gateway")
    assert classify_exception(exc) == ErrorClass.HTTP_5XX

def test_classify_ssrf_blocked():
    exc = Exception("ssrf_blocked: loopback address blocked")
    assert classify_exception(exc) == ErrorClass.SSRF_BLOCKED
    
    exc2 = Exception("ssrf_redirect: IP blocked")
    assert classify_exception(exc2) == ErrorClass.SSRF_BLOCKED

def test_classify_unexpected():
    exc = Exception("Some arbitrary error message")
    assert classify_exception(exc) == ErrorClass.UNEXPECTED

def test_is_transient_reason():
    assert is_transient_reason("timeout") is True
    assert is_transient_reason("network_error") is True
    assert is_transient_reason("http_5xx") is True
    
    assert is_transient_reason("invalid_url") is False
    assert is_transient_reason("http_200_no_title") is False
    assert is_transient_reason("soft_404_title") is False
