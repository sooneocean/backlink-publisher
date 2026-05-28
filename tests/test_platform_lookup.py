"""Tests for ``registry.platforms_by_auth_type`` — the shared registry
reverse-lookup that the credential-save drift guard (Plan 2026-05-27-008,
Unit 3) reads to verify each save-dispatch map stays a subset of its declared
auth_type bucket.

Discipline mirrors ``tests/test_auth_type_classification.py``: the registry is
populated by importing the adapters package at module load (registration is an
import side-effect), and every assertion runs at test time — never at import —
per ``invert-drift-check-when-invariant-becomes-dynamic``.
"""

import backlink_publisher.publishing.adapters  # noqa: F401 — trigger registration

import pytest

from backlink_publisher.publishing.registry import (
    active_platforms,
    auth_type,
    platforms_by_auth_type,
)

# Expected membership per bucket on the current registry. Pinned (not derived)
# so a silent auth_type flip — the #253 drift class — fails loudly here too.
_EXPECTED_BUCKETS = {
    "anon": {"rentry", "telegraph", "txtfyi"},
    "token": {"devto", "writeas"},
    "token_fields": {"ghpages", "hashnode", "notion", "tumblr", "wordpresscom"},
    "paste_blob": {"substack"},
    "userpass": {"livejournal"},
    "oauth": {"blogger"},
    "live_browser": {"mastodon", "medium", "velog"},
}


@pytest.mark.parametrize("bucket,expected", sorted(_EXPECTED_BUCKETS.items()))
def test_platforms_by_auth_type_returns_exact_bucket(bucket, expected):
    """Happy path: each known auth_type resolves to exactly its platforms."""
    assert platforms_by_auth_type(bucket) == frozenset(expected)


def test_returns_frozenset():
    """The lookup returns an immutable frozenset (safe to share / compare)."""
    assert isinstance(platforms_by_auth_type("paste_blob"), frozenset)


def test_unknown_auth_type_is_empty_not_error():
    """Edge case: an auth-type with no platforms yields an empty set, not a
    KeyError — so a caller iterating a never-used bucket is safe."""
    assert platforms_by_auth_type("does-not-exist") == frozenset()


def test_buckets_partition_all_active_platforms():
    """Every active platform lands in exactly one bucket — proves the lookup
    reads the live registry and that no active platform is unclassified
    (the coverage half of the #253 guard, restated through the reverse-lookup)."""
    union = set()
    for bucket in _EXPECTED_BUCKETS:
        members = platforms_by_auth_type(bucket)
        assert union.isdisjoint(members), f"{bucket} overlaps another bucket"
        union |= members
    assert union == set(active_platforms())


def test_reflects_live_registry_not_a_snapshot():
    """Integration: the lookup queries the live registry, so a platform's
    classification is read through ``auth_type`` at call time."""
    for p in platforms_by_auth_type("paste_blob"):
        assert auth_type(p) == "paste_blob"
