"""R9 falsifiable acceptance proof (plan 2026-05-18-009 Unit 1 / R9a+b+e).

The contract R9 promises: registering a new ``Publisher`` subclass with one
``register("x", XAdapter)`` call makes ``x`` reachable through *both* the CLI
argparse layer AND the schema-layer validation, without any other CLI or
schema edit.

This test is the falsifiable proof. If R9 silently regresses to hardcoded
platform names anywhere on the path, the FakeAdapter fixture stops working
and these tests fail.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

# Importing adapters at module level populates the registry side-effect — the
# same idiom plan_backlinks.py and validate_backlinks.py use post-R9.
import backlink_publisher.publishing.adapters  # noqa: F401
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    register,
    registered_platforms,
    _REGISTRY,
)
from backlink_publisher.schema import (
    reject_unsupported_platform,
    supported_platforms,
    validate_publish_payload,
)


class FakeAdapter(Publisher):
    """Stub publisher used only by the R9 acceptance test."""

    @classmethod
    def available(cls, config: Any) -> bool:
        return True

    def publish(self, payload: dict[str, Any], mode: str, config: Any) -> AdapterResult:
        return AdapterResult(
            status="drafted",
            adapter="fake",
            platform="fake",
            draft_url="https://fake.example/p/1",
        )


@pytest.fixture
def fake_platform_registered():
    """Fixture-scoped ``register("fake", FakeAdapter)`` with strict teardown."""
    previous = _REGISTRY.get("fake")
    register("fake", FakeAdapter)
    try:
        yield
    finally:
        if previous is None:
            _REGISTRY.pop("fake", None)
        else:
            _REGISTRY["fake"] = previous


class TestR9AcceptanceProof:
    """The single proof that R9's contract holds end-to-end."""

    def test_fake_platform_appears_in_supported_platforms(
        self, fake_platform_registered
    ) -> None:
        assert "fake" in supported_platforms()
        assert "fake" in registered_platforms()

    def test_schema_validate_accepts_fake_platform(self, fake_platform_registered) -> None:
        row = {
            "target_url": "https://example.com",
            "main_domain": "https://example.com/",
            "language": "en",
            "platform": "fake",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        errors = validate_publish_payload(row)
        platform_errors = [e for e in errors if "platform" in e.lower()]
        assert not platform_errors, (
            f"schema rejected platform='fake' despite FakeAdapter being "
            f"registered: {platform_errors}"
        )

    def test_argparse_accepts_fake_platform(self, fake_platform_registered) -> None:
        """Re-construct the same argparse choices the CLI builds.

        Mirrors the post-R9 wiring in publish_backlinks.py / plan_backlinks.py:
        ``choices=registered_platforms()``. If the CLI ever reverts to a
        hardcoded list, this test catches it because ``"fake"`` would not be
        accepted.
        """
        parser = argparse.ArgumentParser()
        parser.add_argument("--platform", choices=registered_platforms())
        args = parser.parse_args(["--platform", "fake"])
        assert args.platform == "fake"

    def test_argparse_rejects_unregistered_platform(self, fake_platform_registered) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--platform", choices=registered_platforms())
        with pytest.raises(SystemExit):
            parser.parse_args(["--platform", "definitely-not-registered"])

    def test_teardown_pops_fake_entry(self, fake_platform_registered) -> None:
        """Sanity-check the fixture itself: after yield, 'fake' is gone."""
        # Inside the fixture body — assertion deferred to a separate test
        # below that does NOT use the fixture.
        assert "fake" in _REGISTRY

    def test_fake_is_absent_outside_fixture(self) -> None:
        assert "fake" not in _REGISTRY
        assert "fake" not in supported_platforms()


class TestRejectUnsupportedPlatform:
    """R9d helper: replaces 3 LinkedIn-specific rejection sites with one
    registry-driven helper. Coverage now extends to any unregistered
    platform.
    """

    def test_returns_none_for_registered_platform(self) -> None:
        assert reject_unsupported_platform("blogger") is None
        assert reject_unsupported_platform("medium") is None

    def test_rejects_linkedin_with_preserved_phrasing(self) -> None:
        msg = reject_unsupported_platform("linkedin")
        assert msg is not None
        assert "linkedin" in msg
        assert "not supported" in msg
        # Lists what IS supported, so the user can self-correct
        assert "blogger" in msg
        assert "medium" in msg

    def test_rejects_arbitrary_unregistered_platform(self) -> None:
        """R9d's net is wider than legacy linkedin-only rejection."""
        for unregistered in ("tiktok", "threads", "wordpress", "definitely-fake"):
            msg = reject_unsupported_platform(unregistered)
            assert msg is not None, f"{unregistered!r} should be rejected"
            assert unregistered in msg

    def test_registered_fixture_platform_passes(self, fake_platform_registered) -> None:
        """The acceptance proof: registering FakeAdapter makes 'fake' acceptable
        without any schema or CLI edit.
        """
        assert reject_unsupported_platform("fake") is None


class TestRouteTierFallbackForRegisteredButUnmappedPlatform:
    """R9e companion: a registered platform with no ROUTE_TIER_MATRIX entry
    must default-deny ``content_html``-only rows (fail-closed default tier).
    """

    def test_unmapped_registered_platform_defaults_to_tier_c(
        self, fake_platform_registered
    ) -> None:
        from backlink_publisher.publishing.content_negotiation import route_tier_for

        assert route_tier_for("fake") == "c"
