"""Table-driven publish dispatcher — extracted from registry.py.

The single ``dispatch()`` function walks a platform's registered adapter
chain, trying each entry in order (class or instance). DependencyError
entries fall through to the next; ExternalServiceError propagates
immediately; AuthExpiredError propagates so the WebUI can prompt re-bind.

See ``registry.py`` for the full module docstring (ABC, fallback semantics,
throttle metadata, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from backlink_publisher.config import Config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)

from ._verify_html import verify_rendered_link
from .registry import _REGISTRY

if TYPE_CHECKING:
    from .adapters.base import AdapterResult


def _attach_backlink_outcome(result: AdapterResult, payload: dict[str, Any]) -> None:
    """Post-publish rendered-link check — best-effort, never fails the publish.

    Fetches the published page and classifies the backlink outcome into the
    BacklinkOutcome taxonomy. Stores the result in ``_provider_meta`` so the
    publish output and WebUI can distinguish real backlinks from dead ends.
    """
    target_url = payload.get("target_url", "")
    published_url = result.published_url
    if result.status not in ("published", "drafted") or not published_url or not target_url:
        return
    try:
        vr = verify_rendered_link(published_url=published_url, target_url=target_url)
        if vr.effective:
            outcome = "effective_backlink"
            reason = None
        else:
            outcome = "published_but_ineffective"
            reason = vr.failure_reason
    except Exception as exc:
        outcome = "failed"
        reason = f"verify_failed:{type(exc).__name__}"
    meta = dict(result._provider_meta) if result._provider_meta else {}
    meta["backlink_outcome"] = outcome
    if reason is not None:
        meta["backlink_outcome_reason"] = reason
    object.__setattr__(result, "_provider_meta", meta)


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

    _entry = _REGISTRY.get(plat)
    if not _entry:
        raise ExternalServiceError(f"unsupported platform: {plat}")
    chain = _entry.publishers

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
            result = adapter.publish(payload, mode, config)

            # Post-publish rendered-link verification — Wave 2 (zero-auth MVP).
            # After a successful publish, fetch the live page and check whether
            # the target URL appears as a dofollow <a href>. The outcome is
            # stored in _provider_meta so downstream consumers (publish output,
            # WebUI, events) can distinguish real backlinks from dead-end pages.
            # Verification is best-effort: network errors are logged, never
            # turned into publish failures.
            _attach_backlink_outcome(result, payload)

            return result
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
