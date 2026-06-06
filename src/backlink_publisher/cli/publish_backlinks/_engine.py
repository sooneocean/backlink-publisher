"""Publish-loop state and per-row execution kernel.

Unit 3b: the per-row loop body extracted from __init__.py::main() so that
the publish-backlinks dispatcher stays focused on setup and orchestration.

Seam binding strategy (D1): loop-called seams are resolved by a late
(in-function) re-import from the publish_backlinks package namespace at
call time, so every ``@patch("...publish_backlinks.X")`` test still fires:

    from backlink_publisher.cli.publish_backlinks import adapter_publish

This mirrors the proven cli/plan_backlinks/_engine.py pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_AUTH_ABORT = "auth_abort"  # sentinel returned to signal epilogue must be skipped
_ROW_CONTINUE = "continue"  # sentinel: the row was handled, advance to next


@dataclass
class PublishRunState:
    """Loop-carried state for the fresh publish loop.

    Threaded into the per-row body rather than captured by a closure so
    the extracted helper stays module-level (the CC gate only sees
    top-level blocks) and the counters survive across iterations exactly
    as before.  Mirrors cli/_resume.py::_ResumeLoopState.

    Field semantics:
    - Counter fields are mutated in-place.
    - run_id is value-rebound (state.run_id = helper(...)); assign back at
      each rebinding site.
    - auth_aborted is set True on AuthExpiredError so main() can skip
      _publish_epilogue (R3a invariant).
    """

    # Accumulation outputs
    outputs: list[dict[str, Any]] = field(default_factory=list)
    # Counters
    success_count: int = 0
    fail_count: int = 0
    skipped_unreachable_count: int = 0
    skipped_quarantined_count: int = 0
    publish_path_drift_count: int = 0
    dedup_skip_count: int = 0
    dedup_hold_count: int = 0
    # Throttle-adjacency tracking
    last_medium_success_idx: int = -1
    # Checkpoint run identity (value-rebound, not mutated-in-place)
    run_id: str | None = None
    # Dedup warning deduplication per platform
    canary_warned: set[str] = field(default_factory=set)
    # R3a: signals main() to skip _publish_epilogue after AuthExpiredError
    auth_aborted: bool = False


def run_publish_loop(
    rows: list[dict[str, Any]],
    args: Any,
    config: Any,
    state: PublishRunState,
    ts: str,
    banner_emit: Any,
    forced_keys: set,
    throttle_min: int,
    throttle_max: int,
    initial_token_revs: dict[str, int],
) -> None:
    """Drive the per-row publish loop, mutating state in place.

    Returns normally. Sets state.auth_aborted=True when an AuthExpiredError
    fires mid-loop; main() must skip _publish_epilogue in that case (R3a).
    """
    for row_idx, row in enumerate(rows):
        result = _publish_one_row(
            row_idx, row, state, args, config, ts, banner_emit,
            forced_keys, throttle_min, throttle_max, initial_token_revs,
        )
        if result == _AUTH_ABORT:
            state.auth_aborted = True
            return


def _publish_one_row(  # noqa: C901 -- per-row publish gate; real logic in sub-helpers below
    row_idx: int,
    row: dict[str, Any],
    state: PublishRunState,
    args: Any,
    config: Any,
    ts: str,
    banner_emit: Any,
    forced_keys: set,
    throttle_min: int,
    throttle_max: int,
    initial_token_revs: dict[str, int],
) -> str | None:
    """Handle one row in the fresh publish loop.

    Returns _AUTH_ABORT when an AuthExpiredError requires aborting the run,
    _ROW_CONTINUE (or None implicitly) otherwise.
    """
    # ── Late re-import of loop-called seams from the publish_backlinks namespace.
    # Tests patch these at backlink_publisher.cli.publish_backlinks.X; re-reading
    # the name here at call time means every @patch(...publish_backlinks.X) fires.
    from backlink_publisher.cli.publish_backlinks import (
        adapter_publish,
        policy_enabled,
        publish_with_policy,
        _handle_auth_expired,
    )
    # ── Non-seam collaborators — import from their real modules. ─────────────
    from backlink_publisher.cli._publish_helpers import (
        _build_failure_row, _build_skip_row, _canary_gate,
        _check_row_reachability, _check_token_drift, _do_verify,
        _error_class, _medium_throttle_sleep, _publish_epilogue,
        _record_publish_failure, _record_publish_path, _try_update_ckpt_failed,
    )
    from backlink_publisher.cli._dedup_gate import (
        gate_with_force, record_done, record_failure,
    )
    from backlink_publisher.schema import supported_platforms
    from backlink_publisher._util.errors import (
        AuthExpiredError, BannerUploadError, ContentRejectedError,
        DependencyError, ExternalServiceError, emit_error,
    )
    from backlink_publisher._util.logger import publish_logger
    from ... import checkpoint
    from datetime import datetime, timezone

    _medium_throttle_sleep(
        row_idx, state.last_medium_success_idx,
        args.platform or row.get("platform", ""),
        throttle_min, throttle_max,
        dry_run=args.dry_run,
    )

    platform = args.platform or row.get("platform", "")
    mode = args.mode or row.get("publish_mode", "draft")

    canary_skip, canary_reason = _canary_gate(platform, warned=state.canary_warned)
    if canary_skip:
        row_id = row.get("id", "")
        publish_logger.warn(
            f"[publish-backlinks] row_id={row_id} platform={platform} "
            f"status=skipped_quarantined — {canary_reason}"
        )
        state.skipped_quarantined_count += 1
        return _ROW_CONTINUE

    if not args.dry_run and not args.skip_publish_time_check:
        ok, failing_url = _check_row_reachability(row)
        if not ok:
            row_id = row.get("id", "")
            publish_logger.warn(
                f"[publish-backlinks] row_id={row_id} "
                f"status=skipped_unreachable url={failing_url}"
            )
            state.outputs.append(_build_failure_row(
                "skipped_unreachable", row, platform,
                f"target unreachable at publish-time: {failing_url}",
                ts,
                failing_url=failing_url,
            ))
            state.skipped_unreachable_count += 1
            return _ROW_CONTINUE

    if platform not in supported_platforms():
        state.outputs.append(_build_failure_row(
            "failed", row, platform,
            f"unsupported platform: {platform}",
            ts, adapter=platform,
        ))
        state.fail_count += 1
        return _ROW_CONTINUE

    if args.dry_run:
        result = adapter_publish(
            payload={**row, "platform": platform},
            mode=mode,
            config=config,
            dry_run=True,
        )
        state.outputs.append({
            "id": row.get("id", ""),
            "platform": platform,
            "status": result.status,
            "title": row.get("title", ""),
            "draft_url": result.draft_url,
            "published_url": result.published_url,
            "created_at": ts,
            "adapter": result.adapter,
            "error": None,
            "_dry_run": True,
            "_command": result._command,
        })
        state.success_count += 1
        publish_logger.debug(
            f"dry-run: {platform} id={row.get('id', '')}",
            extra={"id": row.get("id"), "platform": platform},
        )
        return _ROW_CONTINUE

    publish_logger.info(
        f"publishing: {platform} id={row.get('id', '')}",
        extra={"id": row.get("id"), "platform": platform, "mode": mode},
    )

    _check_token_drift(initial_token_revs)

    verdict, drec = gate_with_force(
        row, platform, run_id=state.run_id, forced_keys=forced_keys, reason=args.reason
    )
    if verdict == "skip":
        state.outputs.append(_build_skip_row(
            row, platform, drec.live_url if drec else None, ts
        ))
        state.dedup_skip_count += 1
        publish_logger.info(
            f"dedup skip (already published): {platform} id={row.get('id', '')}",
            extra={"id": row.get("id"), "platform": platform},
        )
        return _ROW_CONTINUE
    if verdict == "hold":
        state.dedup_hold_count += 1
        publish_logger.warn(
            f"dedup hold (uncertain/in-flight): {platform} id={row.get('id', '')}",
            extra={"id": row.get("id"), "platform": platform},
        )
        return _ROW_CONTINUE

    try:
        if policy_enabled():
            result = publish_with_policy(
                platform,
                payload=row,
                config=config,
                mode=mode,
                banner_emit=banner_emit,
            )
        else:
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
    except AuthExpiredError as exc:
        record_failure(row, platform, error_class="auth_expired", run_id=state.run_id)
        _handle_auth_expired(exc, state.run_id, row, publish_logger)
        return _AUTH_ABORT
    except BannerUploadError as exc:
        state.fail_count += 1
        state.run_id = _record_publish_failure(
            state.outputs, row, platform, ts, state.run_id, exc,
            "banner_upload", f"banner upload failed: {exc}",
        )
        return _ROW_CONTINUE
    except ContentRejectedError as exc:
        state.fail_count += 1
        state.run_id = _record_publish_failure(
            state.outputs, row, platform, ts, state.run_id, exc,
            "content_rejected", f"content rejected: {exc}",
        )
        return _ROW_CONTINUE
    except DependencyError as exc:
        record_failure(row, platform, error_class="dependency", run_id=state.run_id)
        emit_error(str(exc), exit_code=3)
        return _ROW_CONTINUE  # unreachable (emit_error raises); keeps mypy happy
    except ExternalServiceError as exc:
        state.fail_count += 1
        state.run_id = _record_publish_failure(
            state.outputs, row, platform, ts, state.run_id, exc,
            _error_class(exc), f"service error: {exc}",
        )
        return _ROW_CONTINUE
    except Exception as exc:
        state.fail_count += 1
        state.run_id = _record_publish_failure(
            state.outputs, row, platform, ts, state.run_id, exc,
            "unexpected", f"unexpected error: {exc}",
        )
        return _ROW_CONTINUE

    state.outputs.append(result.to_publish_output(row, ts))
    if result.error:
        state.fail_count += 1
        record_failure(row, platform, error_class=None, run_id=state.run_id)
        _ckpt_error_class = (
            checkpoint.POLICY_SKIP
            if result.status in ("skipped_policy", "skipped_circuit_open")
            else "unexpected"
        )
        state.run_id = _try_update_ckpt_failed(
            state.run_id, row.get("id", ""), str(result.error), _ckpt_error_class
        )
    else:
        state.success_count += 1
        if result.post_publish_delay_seconds > 0:
            state.last_medium_success_idx = row_idx

        state.publish_path_drift_count += _record_publish_path(platform, result, row)

        verify_ok, verify_reason = _do_verify(
            args.no_verify, args.dry_run, result, row
        )
        if not verify_ok:
            state.outputs[-1]["status"] += "_unverified"
            publish_logger.warn(
                f"verification failed: id={row.get('id', '')} reason={verify_reason}",
                extra={"id": row.get("id"), "adapter": result.adapter},
            )

        record_done(
            row, platform,
            live_url=result.published_url or result.draft_url,
            verify_ok=verify_ok,
            run_id=state.run_id,
        )

        if state.run_id is not None:
            try:
                checkpoint.update_item(
                    state.run_id, row.get("id", ""), "done",
                    published_url=result.published_url,
                    article_urls=[u for u in (result.published_url, result.draft_url) if u],
                    adapter=result.adapter,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    verified=verify_ok,
                )
            except Exception as ckpt_exc:
                publish_logger.warning(f"[WARN] checkpoint update failed: {ckpt_exc}")
                state.run_id = None
        publish_logger.info(
            f"published: id={row.get('id', '')} status={result.status}",
            extra={"id": row.get("id"), "status": result.status},
        )

    return None
