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
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import publish_logger
from backlink_publisher.publishing.adapters import publish as adapter_publish, verify_adapter_setup
from backlink_publisher.publishing.registry import registered_platforms
from ..schema import supported_platforms, validate_publish_payload

from ._publish_helpers import (
    _acquire_publish_leases,
    _check_token_drift,
    _do_verify,
    _error_class,
    _load_throttle_config,
    _make_banner_emit,
    _sleep_with_throttle,
)


def item_to_publish_output(item: dict[str, Any]) -> dict[str, Any]:
    article_urls = item.get("article_urls")
    if not isinstance(article_urls, list) or not article_urls:
        article_urls = [u for u in (
            item.get("published_url") or "",
            item.get("draft_url") or "",
        ) if u]
    return {
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


def _record_resume_failure(
    run_id: str,
    item: dict[str, Any],
    exc: Exception,
    err_class: str,
    err_msg: str,
) -> None:
    from .. import checkpoint

    checkpoint.update_item(run_id, item["id"], "failed", error=err_msg, error_class=err_class)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": item["id"], "platform": item.get("platform", "")},
    )


def _run_resume(args: Any) -> None:
    """Handle --resume <run_id>: load checkpoint, process pending/failed items, emit union output."""
    from .. import checkpoint, config_echo

    run_id = args.resume

    try:
        ckpt = checkpoint.load_checkpoint(run_id)
    except (ValueError, FileNotFoundError) as exc:
        emit_error(str(exc), exit_code=2)
        return

    config = load_config()
    banner_emit = _make_banner_emit()

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
        all_done = [item_to_publish_output(i) for i in ckpt["items"] if i["status"] == "done"]
        write_jsonl(all_done)
        sys.stdout.flush()
        checkpoint.mark_complete(run_id)
        raise SystemExit(0)

    # Re-fetch after processing
    updated_ckpt = checkpoint.load_checkpoint(run_id)
    
    # Count failed items that were actually processed in this resume run
    # (or that remain failed from before).
    # Logic: If item is failed, count it as a failure unless it was marked
    # failed in the original checkpoint AND wasn't retried.
    new_failures = [
        i for i in updated_ckpt["items"] 
        if i["status"] == "failed" 
        and i not in [orig for orig in ckpt["items"] if orig["status"] == "failed"]
    ]
    
    if new_failures:
        raise SystemExit(4)
    checkpoint.mark_complete(run_id)

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

        try:
            _check_token_drift(initial_token_revs)
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
        except AuthExpiredError as exc:
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
            emit_error(str(exc), exit_code=3)
            return
        except BannerUploadError as exc:
            _record_resume_failure(
                run_id, item, exc,
                "banner_upload", f"banner upload failed: {exc}",
            )
            continue
        except DependencyError as exc:
            emit_error(str(exc), exit_code=3)
            return
        except ExternalServiceError as exc:
            _record_resume_failure(
                run_id, item, exc,
                _error_class(exc), f"service error: {exc}",
            )
            continue
        except Exception as exc:
            _record_resume_failure(
                run_id, item, exc,
                "unexpected", f"unexpected error: {exc}",
            )
            continue

        completed_at = datetime.now(timezone.utc).isoformat()
        from .. import checkpoint as _ckpt
        _ckpt.update_item(
            run_id, item["id"], "done",
            published_url=result.published_url,
            adapter=result.adapter,
            completed_at=completed_at,
        )
        if result.post_publish_delay_seconds > 0:
            last_medium_success_idx = item_idx

        row = item["payload"]
        verify_ok, verify_reason = _do_verify(
            getattr(args, "no_verify", False), False, result, row
        )
        if not verify_ok:
            unverified_ids.add(item["id"])
            publish_logger.warn(
                f"verification failed: id={item['id']} reason={verify_reason}",
                extra={"id": item["id"], "adapter": result.adapter},
            )

        publish_logger.info(
            f"published: id={item['id']} status={result.status}",
            extra={"id": item["id"], "status": result.status},
        )

    from .. import checkpoint as _ckpt
    updated_ckpt = _ckpt.load_checkpoint(run_id)
    all_done = []
    for i in updated_ckpt["items"]:
        if i["status"] == "done":
            out = item_to_publish_output(i)
            if i["id"] in unverified_ids:
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
            raise SystemExit(5)
        raise SystemExit(0)
    else:
        for f in still_unfinished:
            print(f"publish failed: {f.get('error', 'unknown error')}", file=sys.stderr)
        raise SystemExit(4)
