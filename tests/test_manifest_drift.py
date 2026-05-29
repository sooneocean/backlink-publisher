"""Property tests: runtime constants must not drift from manifest ``Policy``.

``test_manifest_contract.py`` checks visibility / bind / template / subclass
structure, but has **no** assertion that a runtime-used constant equals its
manifest-declared ``Policy`` value. So an adapter can silently drift from its
own manifest today: ``velog`` duplicates its jitter band as module constants
(``_VELOG_JITTER_MIN_S`` / ``_VELOG_JITTER_MAX_S``) *and* declares it as
``policy.throttle_band=(60, 180)``. Change one and the other goes stale with
nothing to catch it — the manifest is treated as documentation, not contract.

This module turns "manifest is documentation" into "manifest is checked
contract" for the cases where a runtime constant duplicates a manifest field.

``DRIFT_BINDINGS`` is the explicit registry of such duplications. As of
2026-05-29 ``velog`` / ``throttle_band`` is the only genuine runtime<->manifest
duplication in the tree:

- Other adapter module constants (``_HTTP_TIMEOUT_S``, ``_POST_PUBLISH_DELAY_S``,
  ``_VERIFY_TIMEOUT_S``, ``velog``'s ``_PROBE_TIMEOUT``) are **not** ``Policy``
  fields — they have no manifest counterpart to drift from. Note ``velog``'s
  ``_PROBE_TIMEOUT=10`` (single-probe HTTP timeout) is *not* the same as
  ``policy.liveness_probe_sec=900`` (recheck cadence); different semantics, no
  binding.
- ``liveness_probe_sec`` and ``language_whitelist`` are read straight from the
  policy at the call site, with no duplicated runtime constant — nothing to
  drift.

New duplications get one ``pytest.param`` line in ``DRIFT_BINDINGS``.
"""

from __future__ import annotations

import pytest

# Importing the production adapters package fires every ``register()`` call,
# populating ``_REGISTRY`` (same bootstrap as ``test_manifest_contract.py``).
import backlink_publisher.publishing.adapters as _production  # noqa: F401
from backlink_publisher.publishing._manifest_types import Policy
from backlink_publisher.publishing.adapters import velog_graphql
from backlink_publisher.publishing.registry import (
    _REGISTRY,
    registered_platforms,
)


def _policy(platform: str) -> Policy | None:
    """Return the declared ``Policy`` for ``platform`` (``None`` if omitted)."""
    return _REGISTRY[platform].policy


# Each binding asserts ``getattr(policy(platform), field) == runtime_value``.
# The runtime value is read from the *live* module constant, so a one-sided
# edit (touching the constant but not the manifest, or vice versa) trips this.
DRIFT_BINDINGS = [
    pytest.param(
        "velog",
        "throttle_band",
        (velog_graphql._VELOG_JITTER_MIN_S, velog_graphql._VELOG_JITTER_MAX_S),
        id="velog:throttle_band==(_VELOG_JITTER_MIN_S,_VELOG_JITTER_MAX_S)",
    ),
]


@pytest.mark.parametrize("platform, field, runtime_value", DRIFT_BINDINGS)
def test_runtime_constant_matches_manifest_policy(
    platform: str, field: str, runtime_value: object
) -> None:
    """A runtime constant must equal the manifest ``Policy`` field it duplicates."""
    policy = _policy(platform)
    assert policy is not None, (
        f"{platform!r} has a runtime constant bound to policy.{field} but "
        f"declares no Policy in publishing/_manifests.py — declare one so the "
        f"manifest stays the single source of truth."
    )
    declared = getattr(policy, field)
    assert declared == runtime_value, (
        f"{platform!r}: runtime constant {runtime_value!r} drifted from manifest "
        f"policy.{field}={declared!r}. Update both together, or collapse the "
        f"duplication by reading the manifest policy at the call site."
    )


@pytest.mark.parametrize("platform, field, runtime_value", DRIFT_BINDINGS)
def test_drift_binding_targets_exist(
    platform: str, field: str, runtime_value: object
) -> None:
    """``DRIFT_BINDINGS`` must reference live platforms and real ``Policy``
    fields, so a deleted platform or renamed field surfaces as a failure rather
    than silently dropping coverage."""
    assert platform in registered_platforms(), (
        f"DRIFT_BINDINGS references {platform!r} which is no longer registered — "
        f"remove the stale binding."
    )
    assert field in Policy.__dataclass_fields__, (
        f"DRIFT_BINDINGS references Policy field {field!r} which no longer "
        f"exists — update the binding to the renamed field."
    )


@pytest.mark.parametrize("platform", registered_platforms())
def test_declared_policy_is_well_formed(platform: str) -> None:
    """Every declared ``Policy`` must be internally valid, so a malformed
    throttle band or probe interval is caught at the manifest, not at runtime.

    ``Policy`` is optional — a platform with no special policy may omit it
    (``None``); only a *declared* policy is constrained here."""
    policy = _policy(platform)
    if policy is None:
        return

    band = policy.throttle_band
    if band is not None:
        assert isinstance(band, tuple) and len(band) == 2, (
            f"{platform!r}: throttle_band must be a (min, max) 2-tuple, got {band!r}."
        )
        lo, hi = band
        assert isinstance(lo, int) and isinstance(hi, int) and 0 <= lo <= hi, (
            f"{platform!r}: throttle_band must be 0 <= min <= max ints, got {band!r}."
        )

    assert isinstance(policy.retry_id, str) and policy.retry_id, (
        f"{platform!r}: retry_id must be a non-empty str, got {policy.retry_id!r}."
    )

    if policy.liveness_probe_sec is not None:
        assert (
            isinstance(policy.liveness_probe_sec, int) and policy.liveness_probe_sec > 0
        ), (
            f"{platform!r}: liveness_probe_sec must be a positive int, "
            f"got {policy.liveness_probe_sec!r}."
        )

    assert isinstance(policy.language_whitelist, tuple), (
        f"{platform!r}: language_whitelist must be a tuple, "
        f"got {type(policy.language_whitelist).__name__}."
    )
    assert all(isinstance(c, str) and c for c in policy.language_whitelist), (
        f"{platform!r}: language_whitelist entries must be non-empty str codes, "
        f"got {policy.language_whitelist!r}."
    )
