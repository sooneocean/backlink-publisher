"""Lock the pipeline exit-code contract encoded in ``_util.errors``.

The 0-6 exit-code table (AGENTS.md) is a *documented contract* but, per that
doc, "not enforced by ``sys.exit()`` in CLI code" -- the codes live on the
``PipelineError`` subclasses and are turned into process exits by
``handle_error`` / ``emit_error``. Coverage was scattered across ~10 per-CLI
test files; nothing locked the map as a whole, and nothing forced a *new*
exception class to declare its exit code. This file is the single canonical
guard so the contract cannot drift silently -- e.g. the classic ``UsageError``
(1) vs argparse (2) confusion, or a sibling error accidentally re-parented
under ``AuthExpiredError`` and triggering ``mark_expired``.
"""

from __future__ import annotations

import inspect

import pytest

from backlink_publisher._util import errors
from backlink_publisher._util.errors import (
    AntiBotChallengeError,
    AuthExpiredError,
    BannerUploadError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
    InputValidationError,
    InternalError,
    PipelineError,
    RegistryError,
    UsageError,
    emit_error,
    handle_error,
    handle_unexpected_error,
)

# (exception class, documented exit code) -- the authoritative 0-6 contract.
EXIT_CODE_CONTRACT = [
    (PipelineError, 5),
    (UsageError, 1),
    (InputValidationError, 2),
    (DependencyError, 3),
    (ExternalServiceError, 4),
    (AntiBotChallengeError, 4),
    (RegistryError, 5),
    (AuthExpiredError, 3),
    (BannerUploadError, 3),
    (ContentRejectedError, 3),
    (InternalError, 5),
]

# Errors whose __init__ takes only a message -- safe to instantiate directly
# in the handle_error wiring test (Auth/ContentRejected need kwargs).
_SIMPLE_CTOR_CODES = [
    (UsageError, 1),
    (InputValidationError, 2),
    (DependencyError, 3),
    (ExternalServiceError, 4),
    (InternalError, 5),
]


@pytest.mark.parametrize(
    "exc_class, expected_code",
    EXIT_CODE_CONTRACT,
    ids=[c.__name__ for c, _ in EXIT_CODE_CONTRACT],
)
def test_exit_code_class_attribute(exc_class, expected_code):
    """Each exception carries its documented exit code as a class attribute."""
    assert exc_class.exit_code == expected_code


def test_contract_covers_every_pipeline_error_subclass():
    """A new PipelineError subclass must be pinned here, or this test fails.

    This is the compounding guard: adding an exception without declaring its
    exit code in EXIT_CODE_CONTRACT breaks the build, forcing the author to
    make the contract decision explicit.
    """
    defined = {
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, PipelineError) and obj.__module__ == errors.__name__
    }
    covered = {cls for cls, _ in EXIT_CODE_CONTRACT}
    missing = defined - covered
    assert not missing, (
        "new PipelineError subclass(es) not pinned in EXIT_CODE_CONTRACT: "
        f"{sorted(c.__name__ for c in missing)}"
    )


def test_antibot_is_external_service_not_dependency():
    """exit 4, not 3: docstring pins this parentage as load-bearing."""
    assert issubclass(AntiBotChallengeError, ExternalServiceError)
    assert not issubclass(AntiBotChallengeError, DependencyError)


def test_auth_expired_is_dependency_not_external_service():
    """exit 3, not 4: AuthExpiredError must stay under DependencyError."""
    assert issubclass(AuthExpiredError, DependencyError)
    assert not issubclass(AuthExpiredError, ExternalServiceError)


@pytest.mark.parametrize("sibling", [BannerUploadError, ContentRejectedError])
def test_dependency_siblings_are_not_auth_subclasses(sibling):
    """'sibling (NOT subclass) of AuthExpiredError' -- mark_expired must not fire."""
    assert issubclass(sibling, DependencyError)
    assert not issubclass(sibling, AuthExpiredError)


@pytest.mark.parametrize(
    "exc_class, expected_code",
    _SIMPLE_CTOR_CODES,
    ids=[c.__name__ for c, _ in _SIMPLE_CTOR_CODES],
)
def test_handle_error_propagates_class_code(exc_class, expected_code):
    """handle_error turns the class attribute into the actual process exit."""
    with pytest.raises(SystemExit) as exc_info:
        handle_error(exc_class("boom"))
    assert exc_info.value.code == expected_code


def test_emit_error_default_code_is_five():
    with pytest.raises(SystemExit) as exc_info:
        emit_error("boom")
    assert exc_info.value.code == 5


def test_emit_error_honors_explicit_code():
    with pytest.raises(SystemExit) as exc_info:
        emit_error("boom", exit_code=2)
    assert exc_info.value.code == 2


def test_handle_unexpected_error_always_exits_five():
    with pytest.raises(SystemExit) as exc_info:
        handle_unexpected_error(ValueError("x"))
    assert exc_info.value.code == 5
