"""Resume interrupted publish run from checkpoint.

Extracted from ``publish_backlinks.py``.
"""

from __future__ import annotations

import sys
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
from ._dedup_gate import gate, record_done, record_failure


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

    # R19b: enforce refuses to resume until the dedup store covers the
    # back-catalogue (no-op in observe). Before acquiring any lease.
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

    to_process = [
        item for item in ckpt["items"]
        if item["status"] in ("pending", "failed")
        and item.get("error_class") not in (
            checkpoint.RETRO_LANGUAGE_FAILED,
            checkpoint.RETRO_ANCHOR_FAILED,
        )
    ]

    for item in to_process:
        if item.get("error_class") == "http_5xx":
            print(
                f"WARNING: item {item['id']} failed with HTTP 5xx — "
                f"post may already be live on {item['platform']}. Verify before resuming.",
                file=sys.stderr,
            )

    if not to_process:
        all_done = []
        for i in ckpt["items"]:
            if i["status"] == "done":
                out = item_to_publish_output(i)
                if not i.get("verified", True):
                    out["status"] += "_unverified"
                all_done.append(out)
        write_jsonl(all_done)
        sys.stdout.flush()
        # R2: project even on a no-op resume so a run whose checkpoint was
        # written but never projected (crash-before-projection) is recovered.
        from ..events import project_run_safe
        project_run_safe(run_id)
        checkpoint.mark_complete(run_id)
        raise SystemExit(0)

    throttle_min, throttle_max = _load_throttle_config()
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

    first_medium_in_resume = True
    last_medium_success_idx = -1
    unverified_ids: set[str] = set()
    dedup_skip_count = 0
    dedup_hold_count = 0

    from backlink_publisher.config import snapshot_token_revs
    initial_token_revs = snapshot_token_revs()

    for item_idx, item in enumerate(to_process):
        row = item["payload"]
        platform = ckpt.get("platform") or row.get("platform", "")
        mode = ckpt.get("mode") or row.get("publish_mode", "draft")

        if platform == "medium":
            if first_medium_in_resume:
                if not resume_elapsed_skip_throttle:
                    _sleep_with_throttle(throttle_min, throttle_max, "resume first Medium post")
                first_medium_in_resume = False
            elif last_medium_success_idx == item_idx - 1:
                _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")

        publish_logger.info(
            f"resume publishing: {platform} id={item['id']}",
            extra={"id": item["id"], "platform": platform},
        )

        # Token-drift check BEFORE the gate claim (see fresh seam): avoids
        # stranding a just-claimed `attempting` row when revocation raises SystemExit.
        _check_token_drift(initial_token_revs)

        # Dedup gate (U2 observe / U7 enforce) — R17: resume consults the dedup
        # record like a fresh run. skip -> mark the item done from the recorded
        # live_url; hold -> leave the item for adjudication; dispatch -> publish.
        verdict, drec = gate(row, platform, run_id=run_id)
        if verdict == "skip":
            dedup_skip_count += 1
            from .. import checkpoint as _ckpt
            _ckpt.update_item(
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
            continue
        if verdict == "hold":
            dedup_hold_count += 1
            publish_logger.warn(
                f"dedup hold (uncertain/in-flight): {platform} id={item['id']}",
                extra={"id": item["id"], "platform": platform},
            )
            continue

        try:
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
                from .. import checkpoint as _ckpt
                _ckpt.update_item(
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
            continue
        except DependencyError as exc:
            record_failure(row, platform, error_class="dependency", run_id=run_id)
            emit_error(str(exc), exit_code=3)
            return
        except ExternalServiceError as exc:
            _record_resume_failure(
                run_id, item, exc,
                _error_class(exc), f"service error: {exc}", platform,
            )
            continue
        except Exception as exc:
            _record_resume_failure(
                run_id, item, exc,
                "unexpected", f"unexpected error: {exc}", platform,
            )
            continue

        if result.error:
            # In-band adapter failure (returned, not raised) — record terminal so
            # the row doesn't strand as `done`, mark the checkpoint item failed,
            # and do NOT record done. Parity with the fresh seam (publish_backlinks
            # has the same guard); without it a returned-error result would seed a
            # `done` dedup row and enforce would permanently skip a post that never
            # landed.
            record_failure(row, platform, error_class=None, run_id=run_id)
            from .. import checkpoint as _ckpt
            _ckpt.update_item(
                run_id, item["id"], "failed",
                error=str(result.error), error_class="unexpected",
            )
            publish_logger.error(
                f"publish failed (in-band): {result.error}",
                extra={"id": item["id"], "platform": platform},
            )
            continue

        completed_at = datetime.now(timezone.utc).isoformat()
        # U3: advisory forward-path drift recording (Plan 2026-05-27-006).
        # Must run on both fresh and resume paths (R7). Never gating.
        _record_publish_path(platform, result, row)

        # Verify before the checkpoint write so the `done` record carries the
        # verification verdict (Plan 005 / D5). Previously verification ran
        # after the write and only updated the transient `unverified_ids` set,
        # so the projector could never tell a verified `done` from an
        # unverified one — and counted unverified publishes as successes.
        verify_ok, verify_reason = _do_verify(
            getattr(args, "no_verify", False), False, result, row
        )
        if not verify_ok:
            unverified_ids.add(item["id"])
            publish_logger.warn(
                f"verification failed: id={item['id']} reason={verify_reason}",
                extra={"id": item["id"], "adapter": result.adapter},
            )

        # Observe-only dedup terminal (U2): record done + verify flag (parity with
        # the fresh seam). verify_ok is orthogonal to dedup identity.
        record_done(
            row, platform,
            live_url=result.published_url or result.draft_url,
            verify_ok=verify_ok,
            run_id=run_id,
        )

        from .. import checkpoint as _ckpt
        _ckpt.update_item(
            run_id, item["id"], "done",
            published_url=result.published_url,
            adapter=result.adapter,
            completed_at=completed_at,
            verified=verify_ok,
        )
        if result.post_publish_delay_seconds > 0:
            last_medium_success_idx = item_idx

        publish_logger.info(
            f"published: id={item['id']} status={result.status}",
            extra={"id": item["id"], "status": result.status},
        )

    # R18/U7 dedup reconciliation line (resume seam) — counts only, no URLs.
    dispatched = len(to_process) - dedup_skip_count - dedup_hold_count
    publish_logger.recon(
        "dedup_reconciliation",
        skipped_already_published=dedup_skip_count,
        held_uncertain=dedup_hold_count,
        dispatched=dispatched,
    )

    from .. import checkpoint as _ckpt
    updated_ckpt = _ckpt.load_checkpoint(run_id)

    # R2: project the resumed run's outcomes into events.db before the
    # unverified SystemExit(5) below. Fail-safe; never affects the exit code.
    from ..events import project_run_safe
    project_run_safe(run_id)

    all_done = []
    for i in updated_ckpt["items"]:
        if i["status"] == "done":
            out = item_to_publish_output(i)
            # Suffix from the current resume's transient set OR the persisted
            # `verified` flag — so items completed unverified in a *prior*
            # resume keep the marker on re-emit (not just this run's items).
            if i["id"] in unverified_ids or not i.get("verified", True):
                out["status"] += "_unverified"
            all_done.append(out)
    write_jsonl(all_done)
    sys.stdout.flush()

    still_unfinished = [i for i in updated_ckpt["items"] if i["status"] in ("pending", "failed")]
    if not still_unfinished:
        _ckpt.mark_complete(run_id)
        if unverified_ids:
            for uid in unverified_ids:
                print(f"verification failed: id={uid}", file=sys.stderr)
            emit_envelope_and_exit(
                "InternalError", 5, f"{len(unverified_ids)} payload(s) failed verification"
            )
        raise SystemExit(0)
    else:
        for f in still_unfinished:
            print(f"publish failed: {f.get('error', 'unknown error')}", file=sys.stderr)
        emit_envelope_and_exit(
            "ExternalServiceError",
            4,
            f"{len(still_unfinished)} payload(s) still unfinished after resume",
        )
