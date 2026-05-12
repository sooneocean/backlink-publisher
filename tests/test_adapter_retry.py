"""Tests for the shared exponential-backoff retry helper."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import patch, call

import pytest

from backlink_publisher.adapters.retry import (
    MAX_ATTEMPTS,
    BACKOFF_BASE,
    JITTER_FACTOR,
    RETRYABLE_HTTP_STATUSES,
    retry_transient_call,
)
from backlink_publisher.errors import DependencyError, ExternalServiceError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _TransientError(Exception):
    """Stands in for any retryable network exception."""


class _PermanentError(Exception):
    """Stands in for any non-retryable error."""


def _always_retry(exc: Exception) -> bool:
    return isinstance(exc, _TransientError)


def _never_retry(exc: Exception) -> bool:
    return False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_success_on_first_attempt_returns_value():
    result = retry_transient_call(lambda: 42, is_retryable=_always_retry)
    assert result == 42


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_success_on_first_attempt_never_sleeps(mock_sleep):
    retry_transient_call(lambda: "ok", is_retryable=_always_retry)
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Retry + recovery
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_retry_and_recovery_on_attempt_2(mock_sleep):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise _TransientError("flake")
        return "recovered"

    result = retry_transient_call(fn, is_retryable=_always_retry)
    assert result == "recovered"
    assert len(calls) == 2
    mock_sleep.assert_called_once()


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_sleep_duration_within_jitter_bounds(mock_sleep):
    """First retry wait is backoff_base^1 = 2s, ±15% → [1.7, 2.3]."""
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 2:
            raise _TransientError("flake")
        return "ok"

    retry_transient_call(fn, is_retryable=_always_retry)
    wait = mock_sleep.call_args[0][0]
    low = BACKOFF_BASE ** 1 * (1.0 - JITTER_FACTOR)
    high = BACKOFF_BASE ** 1 * (1.0 + JITTER_FACTOR)
    assert low <= wait <= high, f"sleep {wait} outside [{low}, {high}]"


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_retry_recovery_on_attempt_3(mock_sleep):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise _TransientError("flake")
        return "recovered"

    result = retry_transient_call(fn, is_retryable=_always_retry)
    assert result == "recovered"
    assert mock_sleep.call_count == 2  # before attempt 2 and before attempt 3


# ---------------------------------------------------------------------------
# Retry exhaustion
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_exhaustion_reraises_last_exception(mock_sleep):
    exc = _TransientError("boom")

    with pytest.raises(_TransientError, match="boom"):
        retry_transient_call(lambda: (_ for _ in ()).throw(exc), is_retryable=_always_retry)


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_exhaustion_sleeps_before_each_retry(mock_sleep):
    """3 attempts → 2 sleeps (before attempt 2 and before attempt 3)."""
    with pytest.raises(_TransientError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_TransientError()),
            is_retryable=_always_retry,
        )
    assert mock_sleep.call_count == 2


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_exhaustion_sleep_durations_increase(mock_sleep):
    """Backoff: wait before attempt 2 = 2s, before attempt 3 = 4s (±jitter)."""
    with pytest.raises(_TransientError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_TransientError()),
            is_retryable=_always_retry,
        )
    waits = [c[0][0] for c in mock_sleep.call_args_list]
    assert waits[0] < waits[1], f"Expected increasing waits: {waits}"


# ---------------------------------------------------------------------------
# Non-retryable exception
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_non_retryable_propagates_immediately(mock_sleep):
    with pytest.raises(_PermanentError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_PermanentError()),
            is_retryable=_never_retry,
        )
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_non_retryable_is_retryable_called_once(mock_sleep):
    calls = []

    def predicate(exc):
        calls.append(exc)
        return False

    with pytest.raises(_PermanentError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_PermanentError()),
            is_retryable=predicate,
        )
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# DependencyError / ExternalServiceError passthrough
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_dependency_error_passes_through_immediately(mock_sleep):
    with pytest.raises(DependencyError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(DependencyError("missing config")),
            is_retryable=lambda exc: True,  # would retry anything — but ESE/DE must not
        )
    mock_sleep.assert_not_called()


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_external_service_error_passes_through_immediately(mock_sleep):
    with pytest.raises(ExternalServiceError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(ExternalServiceError("service down")),
            is_retryable=lambda exc: True,
        )
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Bare raise preserves exception type (R6 contract)
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_exception_type_preserved_on_non_retryable(mock_sleep):
    """Bare raise must preserve exact type — no re-wrapping."""
    class MySpecificError(Exception):
        pass

    with pytest.raises(MySpecificError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(MySpecificError()),
            is_retryable=_never_retry,
        )


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_exception_type_preserved_on_exhaustion(mock_sleep):
    class MySpecificError(Exception):
        pass

    with pytest.raises(MySpecificError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(MySpecificError()),
            is_retryable=lambda exc: True,
        )


# ---------------------------------------------------------------------------
# max_attempts=1 edge case
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_max_attempts_1_no_retry(mock_sleep):
    with pytest.raises(_TransientError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_TransientError()),
            is_retryable=_always_retry,
            max_attempts=1,
        )
    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Stderr content (R3a) — no credentials/bodies
# ---------------------------------------------------------------------------

@patch("backlink_publisher.adapters.retry.time.sleep")
def test_stderr_emitted_on_retry(mock_sleep, capsys):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) < 2:
            raise _TransientError("flake")
        return "ok"

    retry_transient_call(fn, is_retryable=_always_retry, adapter="test-adapter")
    captured = capsys.readouterr()
    assert captured.err.strip(), "Expected retry message on stderr"
    msg = json.loads(captured.err.strip())
    assert msg["level"] == "WARN"
    assert "retrying" in msg["msg"]
    assert "attempt" in msg["msg"]
    assert "waiting" in msg["msg"]
    assert msg["adapter"] == "test-adapter"


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_stderr_not_emitted_on_success(mock_sleep, capsys):
    retry_transient_call(lambda: "ok", is_retryable=_always_retry, adapter="x")
    captured = capsys.readouterr()
    assert captured.err == ""


@patch("backlink_publisher.adapters.retry.time.sleep")
def test_stderr_not_emitted_on_non_retryable(mock_sleep, capsys):
    with pytest.raises(_PermanentError):
        retry_transient_call(
            lambda: (_ for _ in ()).throw(_PermanentError()),
            is_retryable=_never_retry,
        )
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_retryable_http_statuses_contains_expected_codes():
    assert 429 in RETRYABLE_HTTP_STATUSES
    assert 500 in RETRYABLE_HTTP_STATUSES
    assert 502 in RETRYABLE_HTTP_STATUSES
    assert 503 in RETRYABLE_HTTP_STATUSES
    assert 504 in RETRYABLE_HTTP_STATUSES
    assert 401 not in RETRYABLE_HTTP_STATUSES
    assert 403 not in RETRYABLE_HTTP_STATUSES
    assert 422 not in RETRYABLE_HTTP_STATUSES
