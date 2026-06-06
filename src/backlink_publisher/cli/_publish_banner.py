"""Banner event emission helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides banner event emission and publish-path drift recording utilities.
"""

from __future__ import annotations

from typing import Any


def _make_banner_emit() -> Any:
    store_holder: dict[str, Any] = {}

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        from backlink_publisher._util.logger import publish_logger

        publish_logger.info(
            f"banner-embed: {kind} {payload}",
            extra={"banner_event": kind, **payload},
        )
        if "store" not in store_holder:
            from backlink_publisher.events.store import EventStore
            store_holder["store"] = EventStore()
        try:
            store_holder["store"].append(kind, payload)
        except Exception as exc:
            publish_logger.warning(
                f"banner-event EventStore.append({kind!r}) failed: {exc}"
            )

    return _emit


def _error_class(exc: Exception) -> str:
    from backlink_publisher.publishing.adapters.retry import classify_exception
    return classify_exception(exc).value


def _record_publish_failure(
    outputs: list[dict[str, Any]],
    row: dict[str, Any],
    platform: str,
    ts: str,
    run_id: str | None,
    exc: Exception,
    err_class: str,
    err_msg: str,
) -> str | None:
    from backlink_publisher._util.logger import publish_logger
    from backlink_publisher.cli._publish_checkpoint import (
        _build_failure_row,
        _try_update_ckpt_failed,
    )

    outputs.append(_build_failure_row("failed", row, platform, err_msg, ts, adapter=platform))
    new_run_id = _try_update_ckpt_failed(run_id, row.get("id", ""), err_msg, err_class)
    # Observe-only dedup record (U2): map this failure to failed/uncertain. Never
    # gates publish; a store error is swallowed inside the gate helper.
    from backlink_publisher.cli._dedup_gate import record_failure
    record_failure(row, platform, error_class=err_class, run_id=run_id)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": row.get("id"), "platform": platform},
    )
    return new_run_id


def _record_publish_path(platform: str, result: Any, row: dict[str, Any]) -> int:
    """Record per-platform forward-path drift advisory verdict after publish.

    Reads the target-specific fields from the adapter's ``link_attr_verification``
    result (computed in Unit 1 with no extra fetch) and writes a ``link-alive``
    or ``drift`` verdict to the per-platform ``_publish_path`` stream in
    ``canary-health.json``. Issues a WARN on drift naming the offending link(s).

    Returns 1 if drift was recorded, 0 otherwise (for the epilogue count).
    Skips silently (returns 0) when:
    - verification was skipped/absent (R5: skipped → nothing recorded)
    - no required links in the payload (``target_*`` fields absent)

    Advisory only: never raises, never changes exit code.
    Plan 2026-05-27-006 Unit 3.
    """
    meta = (result._provider_meta or {}) if result._provider_meta is not None else {}
    link_attr = meta.get("link_attr_verification") or {}
    if link_attr.get("verification") != "ok":
        return 0  # skipped or missing — R5: record nothing
    if "target_found" not in link_attr:
        return 0  # no required links in payload — nothing checkable

    is_drift = (
        bool(link_attr.get("target_nofollow"))
        or bool(link_attr.get("target_rewritten"))
        or not bool(link_attr.get("target_found", True))
    )

    try:
        from backlink_publisher.canary.store import (
            STATUS_DRIFT_CONFIRMED,
            STATUS_LINK_ALIVE,
            record_publish_path_verdict,
        )
        from backlink_publisher._util.logger import publish_logger

        verdict = STATUS_DRIFT_CONFIRMED if is_drift else STATUS_LINK_ALIVE
        record_publish_path_verdict(platform, verdict)
    except Exception as _exc:  # noqa: BLE001
        from backlink_publisher._util.logger import publish_logger
        publish_logger.debug(
            f"[publish-path-canary] store write failed for {platform!r}: {_exc}"
        )  # advisory — never fail publish

    if is_drift:
        nofollow_urls = link_attr.get("target_nofollow_urls", [])
        rewritten_urls = link_attr.get("target_rewritten_urls", [])
        missing_urls = link_attr.get("target_missing_urls", [])
        row_id = row.get("id", "")

        publish_logger.warn(
            f"[publish-path-canary] id={row_id} platform={platform} verdict=drift "
            f"nofollow={nofollow_urls} rewritten={rewritten_urls} missing={missing_urls}",
            extra={"id": row_id, "platform": platform},
        )
        return 1
    return 0