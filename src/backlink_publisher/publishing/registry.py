"""Publisher ABC + table-driven dispatcher — Plan 2026-05-18-001 Unit 7,
extended by Plan 2026-05-18-009 R9 (CLI/schema decoupling).

Replaces the ``if plat == "blogger" / elif "medium"`` chain in
``adapters/__init__.py:publish()`` with a registry the dispatch logic
walks once per call. Adding a new platform means:

  1. Implement ``Publisher.publish(payload, mode, config) -> AdapterResult``
  2. Call ``register("<platform>", NewAdapterCls)``

No changes to the dispatcher, the CLI argparse layer, or
``schema.supported_platforms`` — all of those read
``registered_platforms()`` dynamically post-R9. See
``AGENTS.md → Adding a new publisher adapter`` for the contributor
walkthrough that cites ``BloggerAPIAdapter`` at each step.

Fallback semantics (preserved from the legacy chain):

- The registry stores an ordered list of adapter classes per platform.
- ``dispatch`` walks the chain in order, instantiating each adapter and
  calling ``.publish(...)``.
- ``AuthExpiredError`` (subclass of ``DependencyError``) → propagate
  immediately; operator must re-bind (Plan 2026-05-20-016 Unit 0b).
- ``DependencyError`` (base class) from one adapter → fall through to
  the next
  (the legacy "no Medium token → try browser" path).
- ``ExternalServiceError`` from any adapter → propagate up immediately
  (preserves the legacy "401 / 429 / network failure does NOT fall
  through" semantics).
- An adapter can declare itself unavailable for a given environment by
  overriding ``Publisher.available(cls, config)`` — used by
  ``MediumBraveAdapter`` to gate itself to macOS.

Adapter-declared throttle metadata (post-R9c): adapters set
``AdapterResult.post_publish_delay_seconds`` to declare a required
post-publish wait (Medium adapters set ``30``). The CLI's verify-poll
window and inter-row throttle bookkeeping key off this field rather than
matching adapter strings against a hardcoded ``_MEDIUM_ADAPTERS`` set.

This is the minimum dispatcher generalisation; per Plan D5 we do not
rewrite adapter internals, and per Plan D8 the only method on the ABC
is ``publish`` (``verify_adapter_setup`` stays a module function).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable, Literal, TYPE_CHECKING

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
    RegistryError,
)

if TYPE_CHECKING:
    # Importing AdapterResult at module top triggers loading of
    # .adapters/__init__.py, which imports back into this module
    # (dispatch/register/registered_platforms). When THIS module is the
    # first one loaded in the package, the cycle hits a partially
    # initialized state and ImportError fires. Type annotations are
    # PEP 563 lazy via __future__ import above; the only runtime use is
    # the AdapterResult(...) constructor inside dispatch() — that import
    # is local to the function body, which by call time is safe.
    from .adapters.base import AdapterResult


class Publisher(ABC):
    """Abstract base for a single-platform publisher.

    Subclasses must implement ``publish``. They may optionally override
    ``available`` to declare environment prerequisites (e.g. macOS-only).
    """

    @abstractmethod
    def publish(
        self,
        payload: dict[str, Any],
        mode: str,
        config: Config,
    ) -> AdapterResult:
        """Publish the payload. Return an ``AdapterResult`` on success.

        Raise:
        - ``DependencyError`` if a prerequisite is missing (no token, no
          browser, no AppleScript host) — dispatcher will try the next
          adapter in the chain.
        - ``ExternalServiceError`` if the remote service returned an
          error (401, 429, 5xx, network failure) — dispatcher will NOT
          fall through; the error propagates immediately.
        """

    @classmethod
    def available(cls, config: Config) -> bool:
        """Return False to skip this adapter in the dispatch chain.

        Default ``True`` — most adapters do not need environment gating.
        Use cases: macOS-only adapters (``MediumBraveAdapter``), feature
        flags, license checks.
        """
        return True


# platform → ordered list of adapter entries to try. Each entry is either
# a ``Publisher`` subclass (instantiated lazily at dispatch time, the
# legacy pattern used by all API-based adapters) or a ``Publisher``
# instance (the new pattern used by ``BrowserPublishDispatcher.for_channel(...)``
# — see Plan 2026-05-21-001 Unit 2 §D6). Populated by ``adapters/__init__.py``
# at import time (see ``_install``).
_REGISTRY: dict[str, list[type[Publisher] | Publisher]] = {}


# Negative-knowledge registry: platforms empirically verified as nofollow
# (or otherwise unsuitable as dofollow backlink sources) by prior PR
# attempts. ``register("devto", ...)`` raises ``RegistryError`` at import
# time — un-rejection path is to delete the entry from this map in the
# same PR as the re-``register()`` call (see Plan 2026-05-20-009 R12).
#
# Each value is a free-form rationale string ≥80 chars stripped, mirroring
# the ``monolith_budget.toml`` rationale convention. The value-shape stays
# a plain ``dict[str, str]`` rather than a dataclass because only the
# rationale string has a programmatic consumer (the failure message);
# ``rejected_at`` is recoverable from ``git log`` and ``dofollow=False``
# is implicit (this is a rejection map).
_REJECTED_PLATFORMS: dict[str, str] = {
    # devto: re-registered as nofollow chrome-publish channel in
    #   Plan 2026-05-21-001 Unit 4b (PR #157).
    # mastodon: re-registered as nofollow chrome-publish channel in
    #   Plan 2026-05-21-001 Unit 4c — Fediverse referral traffic +
    #   topical signal value despite the hardcoded nofollow attribute.
    "wordpresscom": (
        "WordPress.com free tier applies rel=\"nofollow\" to outbound links; "
        "paid Business/Commerce tiers enable dofollow but require a paid "
        "subscription not justified at solo-operator scale. Free-tier ship "
        "would emit nofollow-only backlinks. Reverted in PR #109."
    ),
}


_DofollowStatus = Literal[True, False, "uncertain"]


# Parallel-dict storage for the dofollow capability (Plan 2026-05-20-009
# U2). Kept alongside ``_REGISTRY`` rather than folded into its value
# shape so the existing single-key conftest snapshot pattern survives
# (``tests/conftest.py:206-221`` only saves/restores the ``"fake"`` key).
# Future capability fields (banner_upload, oauth_dialect, daily_cap)
# would justify migrating to a ``RegistryEntry`` dataclass — deferred
# to capability field #2 per Plan 2026-05-20-009 §Scope Boundaries.
_DOFOLLOW_BY_PLATFORM: dict[str, _DofollowStatus] = {}
_RATIONALE_BY_PLATFORM: dict[str, str] = {}


def register(
    platform: str,
    *publishers: type[Publisher] | Publisher,
    dofollow: _DofollowStatus,
    rationale: str | None = None,
) -> None:
    """Register the fallback chain for one platform. Last call wins.

    Order matters: the first registered entry is tried first. Each
    ``publishers`` entry may be:

    - A ``Publisher`` subclass — the legacy pattern. ``dispatch()`` will
      instantiate it lazily per call (e.g. ``BloggerAPIAdapter``,
      ``MediumAPIAdapter``).
    - A ``Publisher`` instance — the Plan 2026-05-21-001 Unit 2 pattern
      for ``BrowserPublishDispatcher.for_channel("hashnode")`` and
      siblings, where ctor-time recipe binding is needed (D6).

    The mixing is supported per-platform too (a class entry can be followed
    by an instance entry in the same chain).

    ``dofollow`` is a required keyword argument (Literal ``True`` /
    ``False`` / ``"uncertain"``) declaring whether the platform produces
    a dofollow backlink. Missing ``dofollow=`` raises ``TypeError`` at
    import time — the structural gate that replaces the institutional
    "grep _DOFOLLOW_BY_CHANNEL before shipping" rule (memory feedback
    ``feedback_grep_dofollow_map_before_shipping_adapter``). ``rationale``
    is required when ``dofollow`` is anything other than ``True`` (R3 /
    Plan 2026-05-20-009).

    Raises:
        RegistryError: when ``platform`` is listed in
            ``_REJECTED_PLATFORMS`` (un-rejection path: delete the
            entry in the same PR as the new ``register()`` call), OR
            when ``dofollow ∈ {False, "uncertain"}`` and the rationale
            is missing / shorter than 80 chars stripped.
    """
    if platform in _REJECTED_PLATFORMS:
        prior = _REJECTED_PLATFORMS[platform]
        raise RegistryError(
            f"previously rejected: {platform!r}; prior rationale: {prior!r}. "
            f"To retry, delete this entry from `_REJECTED_PLATFORMS` in the "
            f"same PR as the new `register()` call."
        )
    if dofollow in (False, "uncertain"):
        if rationale is None or len(rationale.strip()) < 80:
            actual = 0 if rationale is None else len(rationale.strip())
            raise RegistryError(
                f"`register({platform!r}, ..., dofollow={dofollow!r})` "
                f"requires `rationale=` with len(rationale.strip()) >= 80 "
                f"(got {actual}). Length-only gate — content is reviewer "
                f"concern; see `monolith_budget.toml` for the precedent."
            )
    _REGISTRY[platform] = list(publishers)
    _DOFOLLOW_BY_PLATFORM[platform] = dofollow
    if rationale is not None:
        _RATIONALE_BY_PLATFORM[platform] = rationale
    else:
        _RATIONALE_BY_PLATFORM.pop(platform, None)


def registered_platforms() -> list[str]:
    """Return the list of platforms with at least one adapter registered."""
    return sorted(_REGISTRY)


def dofollow_status(name: str) -> _DofollowStatus | None:
    """Return the declared dofollow status for ``name``, or ``None`` if
    the platform is not registered with explicit dofollow declaration.

    Plan 2026-05-20-009 R5.
    """
    return _DOFOLLOW_BY_PLATFORM.get(name)


def dofollow_rationale(name: str) -> str | None:
    """Return the registration rationale string for ``name``, or ``None``
    if no rationale was supplied (the common case for ``dofollow=True``
    registrations; mandatory for ``False`` / ``"uncertain"`` per R3).

    Plan 2026-05-20-009 R5.
    """
    return _RATIONALE_BY_PLATFORM.get(name)


def dispatch(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
    *,
    banner_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> AdapterResult:
    """Walk the registered fallback chain for ``payload["platform"]``.

    Error semantics: dry-run returns a sentinel result;
    ``AuthExpiredError`` (subclass of ``DependencyError``) propagates
    immediately so operator UX can prompt re-bind (Plan
    2026-05-20-016 Unit 0b); plain ``DependencyError`` from one
    adapter falls through to the next; ``ExternalServiceError``
    propagates; unknown platform raises ``ExternalServiceError``.

    Banner embed (Plan 2026-05-20-004 Unit 1): when ``banner_emit`` is
    supplied AND the payload carries a non-degraded ``banner`` field
    (``banner["path"]`` not None), each available adapter in the chain
    gets a chance to embed via ``adapter.embed_banner`` before its
    ``publish()`` runs.  See ``banner_dispatcher.apply`` for the
    branch semantics.  ``banner_emit`` is the event sink (kind,
    payload) and defaults to ``None`` which suppresses banner work
    entirely (back-compat for callers that don't set up banners).
    """
    from .adapters.base import AdapterResult  # local: breaks module-level circular

    plat = payload.get("platform", "")

    if dry_run:
        return AdapterResult(
            status="draft",
            adapter=f"{plat}-api",
            platform=plat,
            _dry_run=True,
            _command=f"publish to {plat} --mode {mode} (dry-run)",
        )

    chain = _REGISTRY.get(plat)
    if not chain:
        raise ExternalServiceError(f"unsupported platform: {plat}")

    banner_dict = payload.get("banner") if banner_emit is not None else None
    do_banner = banner_dict is not None and banner_dict.get("path") is not None
    strict = bool(do_banner and config.image_gen and config.image_gen.strict)

    last_dep_error: DependencyError | None = None
    for entry in chain:
        # Entry may be a Publisher subclass (legacy) or instance
        # (BrowserPublishDispatcher.for_channel — Plan 2026-05-21-001 U2).
        is_class = isinstance(entry, type)
        publisher_cls = entry if is_class else type(entry)
        if not publisher_cls.available(config):
            continue
        try:
            adapter = entry() if is_class else entry
            if do_banner:
                # Lazy import avoids a top-level cycle (banner_dispatcher
                # lives in the same publishing package and is leaf-level,
                # but importing it during registry init is unnecessary
                # for the >99% of dispatch calls that have no banner).
                from . import banner_dispatcher

                new_body = banner_dispatcher.apply(
                    adapter,
                    banner=banner_dict,
                    body=payload.get("content_markdown", ""),
                    platform=plat,
                    strict=strict,
                    emit=banner_emit,  # type: ignore[arg-type]  # do_banner gates non-None
                )
                if new_body != payload.get("content_markdown"):
                    payload = {**payload, "content_markdown": new_body}
            return adapter.publish(payload, mode, config)
        except AuthExpiredError:
            # Plan 2026-05-20-016 Unit 0b: credentials were valid enough to
            # reach the adapter but have expired — operator must re-bind.
            # Falling through would silently try the next chain entry and
            # hide the expiry; the correct semantics is to propagate so
            # the webui can surface "请重新绑定 <channel>" UX.
            # Order matters: AuthExpiredError IS-A DependencyError (per
            # _util/errors.py), so this except MUST precede the
            # DependencyError catch below — Python catches the first
            # matching except clause.
            raise
        except DependencyError as e:
            # Adapter declared itself missing a prerequisite → try next.
            last_dep_error = e
            continue
        # ExternalServiceError propagates without catch (legacy semantics).

    if last_dep_error is not None:
        raise last_dep_error
    raise DependencyError(
        f"No available adapter for platform {plat!r} — every entry in the "
        f"chain returned available()=False."
    )
