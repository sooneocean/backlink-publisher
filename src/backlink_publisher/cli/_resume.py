"""Resume interrupted publish run from checkpoint.

Extracted from ``publish_backlinks.py``. ``_run_resume`` was decomposed into a thin
orchestration shell plus phase helpers in 2026-05-29 (Plan 2026-05-29-005 Unit 3) to drop
it below the cyclomatic-complexity backstop; behavior is preserved (see
tests/test_resume_characterization.py + tests/test_publish_backlinks_resume*.py).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backlink_publisher.config import load_config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    DependencyError,
    ExternalServiceError,
    emit_envelope_and_exit,
    emit_error,
)
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.logger import publish_logger
from backlink_publisher.publishing.adapters import publish as adapter_publish, verify_adapter_setup
from backlink_publisher.publishing.adapters.base import carry_link_attr_verification
from backlink_publisher.publishing.reliability.policy import policy_enabled, publish_with_policy
from ..schema import supported_platforms

from ._publish_helpers import (
    _acquire_publish_leases,
    _check_token_drift,
    _do_verify,
    _error_class,
    _load_throttle_config,
    _make_banner_emit,
    _record_publish_path,
    _sleep_with_throttle,
)
from ._dedup_gate import gate, is_crashed_in_flight, record_done, record_failure


def item_to_publish_output(item: dict[str, Any]) -> dict[str, Any]:
    article_urls = item.get("article_urls")
    if not isinstance(article_urls, list) or not article_urls:
        article_urls = [u for u in (
            item.get("published_url") or "",
            item.get("draft_url") or "",
        ) if u]
    out = {
        "id": item.get("id", ""),
        "platform": item.get("platform", ""),
        "status": item.get("status", ""),
        "title": item.get("title", ""),
        "target_url": item.get("target_url", ""),
        "article_urls": article_urls,
        "draft_url": "",
        "published_url": item.get("published_url") or "",
        "created_at": item.get("completed_at", ""),
        "adapter": item.get("adapter") or "",
        "error": None,
    }
    # Forward-compatible: emit the link-attribute verdict if a checkpoint item
    # carries it. The checkpoint does not persist _provider_meta today, so a
    # resumed publish will not have it — the canary must be run as a single
    # fresh (non-resumed) publish (see the canary-closeout runbook). Shares the
    # emitter helper with the fresh path so the two stay byte-identical.
    return carry_link_attr_verification(out, item)


def _record_resume_failure(
    run_id: str,
    item: dict[str, Any],
    exc: Exception,
    err_class: str,
    err_msg: str,
    platform: str = "",
) -> None:
    from .. import checkpoint

    checkpoint.update_item(run_id, item["id"], "failed", error=err_msg, error_class=err_class)
    # Observe-only dedup terminal (U2): map this failure to failed/uncertain using the
    # loop-resolved platform so the key matches the record_intent write.
    record_failure(item.get("payload") or {}, platform, error_class=err_class, run_id=run_id)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": item["id"], "platform": item.get("platform", "")},
    )


@dataclass
class _ResumeLoopState:
    """Loop-carried state for the resume publish loop.

    Threaded into _publish_one_resume_item rather than captured by a closure so the
    per-item helper stays module-level (the CC gate only sees top-level blocks) and the
    medium-throttle adjacency / counters survive across iterations exactly as before.
    """

    resume_elapsed_skip_throttle: bool = False
    initial_token_revs: dict[str, int] = field(default_factory=dict)
    first_medium_in_resume: bool = True
    last_medium_success_idx: int = -1
    unverified_ids: set[str] = field(default_factory=set)
    dedup_skip_count: int = 0
    dedup_hold_count: int = 0


def _revalidate_checkpoint_items(run_id: str, ckpt: dict[str, Any], config: Any) -> None:
    """Phase 2: re-validate pending/failed items and reclassify retro language/anchor failures.

    Mutates the checkpoint (persisted) AND the in-memory ckpt items in place so the
    subsequent _select_resume_items filter observes the post-reclassification state.
    """
    from .. import checkpoint
    from .validate_backlinks import _enhance_payload

    retro_lang = 0
    retro_anchor = 0
    for item in ckpt["items"]:
        if item["status"] not in ("pending", "failed"):
            continue
        if item.get("error_class") in (
            checkpoint.RETRO_LANGUAGE_FAILED,
            checkpoint.RETRO_ANCHOR_FAILED,
        ):
            continue
        re_validated = _enhance_payload(dict(item["payload"]), config)
        if re_validated["validation"]["status"] != "failed":
            continue
        errors_list = re_validated["validation"]["errors"]
        if errors_list and errors_list[0].startswith("body language"):
            err_class = checkpoint.RETRO_LANGUAGE_FAILED
            retro_lang += 1
        else:
            err_class = checkpoint.RETRO_ANCHOR_FAILED
            retro_anchor += 1
        checkpoint.update_item(
            run_id, item["id"], "failed",
            error="; ".join(errors_list),
            error_class=err_class,
        )
        item["status"] = "failed"
        item["error_class"] = err_class

    if retro_lang or retro_anchor:
        publish_logger.info(
            f"resume: re-validated checkpoint — reclassified "
            f"{retro_lang} retro_language_failed + {retro_anchor} retro_anchor_failed"
        )


def _select_resume_items(ckpt: dict[str, Any]) -> list[dict[str, Any]]:
    """Phase 3: items still to process (pending/failed minus retro/policy-skip); warn on
    5xx and on crashed-in-flight (stale-attempting) rows that may already be live."""
    from .. import checkpoint

    to_process = [
        item for item in ckpt["items"]
        if item["status"] in ("pending", "failed")
        and item.get("error_class") not in (
            checkpoint.RETRO_LANGUAGE_FAILED,
            checkpoint.RETRO_ANCHOR_FAILED,
            checkpoint.POLICY_SKIP,
        )
    ]

    # One shared dedup store for the per-item crashed-in-flight reads below, so resume
    # does not reopen a connection per item. None if it can't be opened — then
    # is_crashed_in_flight falls back per-call and stays observe-safe.
    try:
        from ..idempotency import DedupStore
        dedup_store: Any = DedupStore()
    except Exception:
        dedup_store = None

    ckpt_platform = ckpt.get("platform")
    for item in to_process:
        row = item.get("payload") or {}
        platform = ckpt_platform or item.get("platform") or row.get("platform", "")
        if item.get("error_class") == "http_5xx":
            print(
                f"WARNING: item {item['id']} failed with HTTP 5xx — "
                f"post may already be live on {platform}. Verify before resuming.",
                file=sys.stderr,
            )
            # http_5xx already carries the "may be live — verify" signal, so the
            # crashed-in-flight check below would be redundant for it: skip it.
            continue
        # A hard crash (SIGKILL/OOM/power loss) mid-dispatch never set an error_class,
        # so the item stays `pending` and is silent above — but its dedup row is a stale
        # `attempting`, meaning the post may already be live. Warn with the same guidance
        # (both observe and enforce). In enforce the gate additionally HOLDS it as
        # uncertain; observe still dispatches by contract, so this warning is the
        # operator's signal there.
        if is_crashed_in_flight(row, platform, store=dedup_store):
            print(
                f"WARNING: item {item['id']} was interrupted mid-publish — "
                f"post may already be live on {platform}. Verify before resuming.",
                file=sys.stderr,
            )

    return to_process


def _emit_noop_resume(ckpt: dict[str, Any], run_id: str) -> None:
    """Phase 4: nothing to process — emit the done-union, project, mark complete, exit 0.

    R2: project even on a no-op resume so a run whose checkpoint was written but never
    projected (crash-before-projection) is recovered.
    """
    from .. import checkpoint
    from ..events import project_run_safe

    all_done = []
    for i in ckpt["items"]:
        if i["status"] == "done":
            out = item_to_publish_output(i)
            if not i.get("verified", True):
                out["status"] += "_unverified"
            all_done.append(out)
    write_jsonl(all_done)
    sys.stdout.flush()
    project_run_safe(run_id)
    checkpoint.mark_complete(run_id)
    raise SystemExit(0)


def _resume_throttle_skip(ckpt: dict[str, Any]) -> bool:
    """Phase 5: whether to skip the first-Medium throttle, from the last done Medium item's age.

    Overwritten per matching item, so it reflects the LAST done-medium item in checkpoint
    order — matching the pre-decomposition behavior exactly.
    """
    resume_elapsed_skip_throttle = False
    for item in ckpt["items"]:
        if item["status"] == "done" and item.get("platform") == "medium":
            try:
                last_ts = datetime.fromisoformat(item["completed_at"])
                elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                if elapsed >= 300:
                    resume_elapsed_skip_throttle = True
                else:
                    resume_elapsed_skip_throttle = False
            except (ValueError, TypeError):
                pass
    return resume_elapsed_skip_throttle


def _publish_one_resume_item(
    item: dict[str, Any],
    item_idx: int,
    state: _ResumeLoopState,
    *,
    ckpt: dict[str, Any],
    config: Any,
    banner_emit: Any,
    run_id: str,
    args: Any,
    throttle_min: float,
    throttle_max: float,
) -> None:
    """Phase 6 body: process exactly one resume item.

    ``return`` here is the original loop ``continue`` (skip/hold/recoverable-failure → next
    item). The run-aborting arms (AuthExpired/Dependency) call ``emit_error`` which raises
    ``SystemExit``; because that raise happens inside an ``except`` arm (and SystemExit is a
    BaseException), it is not caught by the sibling ``except`` arms and propagates out of this
    helper to abort the whole run — identical to the pre-decomposition flow, where the
    ``return`` after ``emit_error`` was already dead code.
    """
    from .. import checkpoint

    row = item["payload"]
    platform = ckpt.get("platform") or row.get("platform", "")
    mode = ckpt.get("mode") or row.get("publish_mode", "draft")

    if platform == "medium":
        if state.first_medium_in_resume:
            if not state.resume_elapsed_skip_throttle:
                _sleep_with_throttle(throttle_min, throttle_max, "resume first Medium post")
            state.first_medium_in_resume = False
        elif state.last_medium_success_idx == item_idx - 1:
            _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")

    publish_logger.info(
        f"resume publishing: {platform} id={item['id']}",
        extra={"id": item["id"], "platform": platform},
    )

    # Token-drift check BEFORE the gate claim (see fresh seam): avoids stranding a
    # just-claimed `attempting` row when revocation raises SystemExit.
    _check_token_drift(state.initial_token_revs)

    # Dedup gate (U2 observe / U7 enforce) — R17: resume consults the dedup record like a
    # fresh run. skip -> mark the item done from the recorded live_url; hold -> leave for
    # adjudication; dispatch -> publish.
    verdict, drec = gate(row, platform, run_id=run_id)
    if verdict == "skip":
        state.dedup_skip_count += 1
        checkpoint.update_item(
            run_id, item["id"], "done",
            published_url=(drec.live_url if drec else None),
            adapter=platform,
            completed_at=datetime.now(timezone.utc).isoformat(),
            verified=True,
        )
        publish_logger.info(
            f"dedup skip (already published): {platform} id={item['id']}",
            extra={"id": item["id"], "platform": platform},
        )
        return
    if verdict == "hold":
        state.dedup_hold_count += 1
        publish_logger.warn(
            f"dedup hold (uncertain/in-flight): {platform} id={item['id']}",
            extra={"id": item["id"], "platform": platform},
        )
        return

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
        record_failure(row, platform, error_class="auth_expired", run_id=run_id)
        try:
            from webui_store.channel_status import mark_expired
            mark_expired(exc.channel)
        except Exception as flip_exc:
            publish_logger.warning(
                f"mark_expired({exc.channel!r}) failed: {flip_exc}"
            )
        try:
            checkpoint.update_item(
                run_id, item["id"], "failed",
                error=str(exc),
                error_class="auth_expired",
            )
        except Exception as ckpt_exc:
            print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
        publish_logger.error(
            f"auth expired: {exc}",
            extra={"id": item["id"], "platform": platform},
        )
        # Surface the real class (AuthExpiredError), not exit-3's "DependencyError".
        emit_error(str(exc), exit_code=3, error_class=type(exc).__name__)
        return
    except BannerUploadError as exc:
        _record_resume_failure(
            run_id, item, exc,
            "banner_upload", f"banner upload failed: {exc}", platform,
        )
        return
    except DependencyError as exc:
        record_failure(row, platform, error_class="dependency", run_id=run_id)
        emit_error(str(exc), exit_code=3)
        return
    except ExternalServiceError as exc:
        _record_resume_failure(
            run_id, item, exc,
            _error_class(exc), f"service error: {exc}", platform,
        )
        return
    except Exception as exc:
        _record_resume_failure(
            run_id, item, exc,
            "unexpected", f"unexpected error: {exc}", platform,
        )
        return

    if result.error:
        # In-band adapter failure (returned, not raised) — record terminal so the row
        # doesn't strand as `done`, mark the checkpoint item failed, and do NOT record done.
        # Without this a returned-error result would seed a `done` dedup row and enforce
        # would permanently skip a post that never landed.
        record_failure(row, platform, error_class=None, run_id=run_id)
        _ckpt_error_class = (
            checkpoint.POLICY_SKIP
            if result.status in ("skipped_policy", "skipped_circuit_open")
            else "unexpected"
        )
        checkpoint.update_item(
            run_id, item["id"], "failed",
            error=str(result.error), error_class=_ckpt_error_class,
        )
        publish_logger.error(
            f"publish failed (in-band): {result.error}",
            extra={"id": item["id"], "platform": platform},
        )
        return

    completed_at = datetime.now(timezone.utc).isoformat()
    # U3: advisory forward-path drift recording (Plan 2026-05-27-006). Must run on both
    # fresh and resume paths (R7). Never gating.
    _record_publish_path(platform, result, row)

    # Verify before the checkpoint write so the `done` record carries the verification
    # verdict (Plan 005 / D5) — otherwise the projector cannot tell a verified `done` from
    # an unverified one and would count unverified publishes as successes.
    verify_ok, verify_reason = _do_verify(
        getattr(args, "no_verify", False), False, result, row
    )
    if not verify_ok:
        state.unverified_ids.add(item["id"])
        publish_logger.warn(
            f"verification failed: id={item['id']} reason={verify_reason}",
            extra={"id": item["id"], "adapter": result.adapter},
        )

    # Observe-only dedup terminal (U2): record done + verify flag (parity with the fresh
    # seam). verify_ok is orthogonal to dedup identity.
    record_done(
        row, platform,
        live_url=result.published_url or result.draft_url,
        verify_ok=verify_ok,
        run_id=run_id,
    )

    checkpoint.update_item(
        run_id, item["id"], "done",
        published_url=result.published_url,
        adapter=result.adapter,
        completed_at=completed_at,
        verified=verify_ok,
    )
    if result.post_publish_delay_seconds > 0:
        state.last_medium_success_idx = item_idx

    publish_logger.info(
        f"published: id={item['id']} status={result.status}",
        extra={"id": item["id"], "status": result.status},
    )


def _finalize_resume(
    run_id: str, state: _ResumeLoopState, to_process: list[dict[str, Any]]
) -> None:
    """Phase 7: dedup reconciliation line, reload checkpoint, project, emit union, exit 0/4/5."""
    from .. import checkpoint
    from ..events import project_run_safe

    # R18/U7 dedup reconciliation line (resume seam) — counts only, no URLs.
    dispatched = len(to_process) - state.dedup_skip_count - state.dedup_hold_count
    publish_logger.recon(
        "dedup_reconciliation",
        skipped_already_published=state.dedup_skip_count,
        held_uncertain=state.dedup_hold_count,
        dispatched=dispatched,
    )

    updated_ckpt = checkpoint.load_checkpoint(run_id)

    # R2: project the resumed run's outcomes into events.db before the unverified
    # SystemExit(5) below. Fail-safe; never affects the exit code.
    project_run_safe(run_id)

    all_done = []
    for i in updated_ckpt["items"]:
        if i["status"] == "done":
            out = item_to_publish_output(i)
            # Suffix from the current resume's transient set OR the persisted `verified`
            # flag — so items completed unverified in a *prior* resume keep the marker on
            # re-emit (not just this run's items).
            if i["id"] in state.unverified_ids or not i.get("verified", True):
                out["status"] += "_unverified"
            all_done.append(out)
    write_jsonl(all_done)
    sys.stdout.flush()

    still_unfinished = [i for i in updated_ckpt["items"] if i["status"] in ("pending", "failed")]
    if not still_unfinished:
        checkpoint.mark_complete(run_id)
        if state.unverified_ids:
            for uid in state.unverified_ids:
                print(f"verification failed: id={uid}", file=sys.stderr)
            emit_envelope_and_exit(
                "InternalError", 5, f"{len(state.unverified_ids)} payload(s) failed verification"
            )
        raise SystemExit(0)
    else:
        # Held-uncertain rows (dedup gate held a stale/crashed-in-flight key this run)
        # stay `pending` with no error. Surface the adjudication path explicitly so the
        # operator does not blindly re-run --resume into the same hold forever (mirrors
        # the fresh seam's exit-3 guidance). Per-item "publish failed" prints only for
        # rows that actually carry an error, so a held row is no longer mislabeled
        # "unknown error".
        if state.dedup_hold_count > 0:
            print(
                f"{state.dedup_hold_count} row(s) held by the dedup gate "
                "(uncertain / crashed-in-flight — a prior run may have already published "
                "them); adjudicate with --list-uncertain / --adjudicate-uncertain, then "
                "re-run --resume",
                file=sys.stderr,
            )
        for f in still_unfinished:
            err = f.get("error")
            if err:
                print(f"publish failed: {err}", file=sys.stderr)
        emit_envelope_and_exit(
            "ExternalServiceError",
            4,
            f"{len(still_unfinished)} payload(s) still unfinished after resume",
        )


def _run_resume(args: Any) -> None:
    """Handle --resume <run_id>: load checkpoint, process pending/failed items, emit union output."""
    from .. import checkpoint

    run_id = args.resume

    try:
        ckpt = checkpoint.load_checkpoint(run_id)
    except (ValueError, FileNotFoundError) as exc:
        emit_error(str(exc), exit_code=2)
        return

    config = load_config()
    banner_emit = _make_banner_emit()

    # R19b: enforce refuses to resume until the dedup store covers the back-catalogue
    # (no-op in observe). Before acquiring any lease.
    from ._dedup_gate import enforce_precondition_or_exit
    enforce_precondition_or_exit()

    platforms_in_ckpt = {item["platform"] for item in ckpt["items"] if item.get("platform")}
    _acquire_publish_leases(platforms_in_ckpt, getattr(args, "dry_run", False))
    for plat in platforms_in_ckpt:
        if plat in supported_platforms():
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    _revalidate_checkpoint_items(run_id, ckpt, config)

    to_process = _select_resume_items(ckpt)
    if not to_process:
        _emit_noop_resume(ckpt, run_id)  # raises SystemExit(0)

    throttle_min, throttle_max = _load_throttle_config()
    from backlink_publisher.config import snapshot_token_revs
    state = _ResumeLoopState(
        resume_elapsed_skip_throttle=_resume_throttle_skip(ckpt),
        initial_token_revs=snapshot_token_revs(),
    )

    for item_idx, item in enumerate(to_process):
        _publish_one_resume_item(
            item, item_idx, state,
            ckpt=ckpt, config=config, banner_emit=banner_emit, run_id=run_id,
            args=args, throttle_min=throttle_min, throttle_max=throttle_max,
        )

    _finalize_resume(run_id, state, to_process)  # raises SystemExit
