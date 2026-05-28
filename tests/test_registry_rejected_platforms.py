"""Unit 1: _REJECTED_PLATFORMS map + RegistryError class.

Plan 2026-05-20-009 §Unit 1.
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.errors import (
    PipelineError,
    RegistryError,
)
from backlink_publisher.publishing.registry import _REJECTED_PLATFORMS


class TestRejectedPlatformsMap:
    def test_rejected_entries(self) -> None:
        # PR #108 → #109 negative-knowledge corpus.
        # devto/mastodon/wordpresscom were all un-rejected and re-registered.
        # jianshu re-rejected 2026-05-27; csdn/juejin/note hard-removed
        # 2026-05-28. All four entries arm register()'s re-add tripwire.
        assert set(_REJECTED_PLATFORMS) == {"jianshu", "csdn", "juejin", "note"}

    def test_every_rationale_meets_length_floor(self) -> None:
        # Mirrors monolith_budget.toml rationale discipline. Loop assertion
        # so the invariant holds for any future entry, not just the seed.
        for name, rationale in _REJECTED_PLATFORMS.items():
            assert isinstance(rationale, str), f"{name}: rationale must be str"
            assert len(rationale.strip()) >= 80, (
                f"{name}: rationale is {len(rationale.strip())} chars, "
                f"need ≥80"
            )


class TestRegistryError:
    def test_subclasses_pipeline_error(self) -> None:
        assert issubclass(RegistryError, PipelineError)

    def test_exit_code_is_internal(self) -> None:
        # Exit code 5 = Internal (programmer bug at import time, not
        # runtime user error).
        assert RegistryError.exit_code == 5

    def test_constructs_with_message(self) -> None:
        err = RegistryError("test message")
        assert err.message == "test message"
        assert str(err) == "test message"

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(RegistryError, match="test"):
            raise RegistryError("test fire")
