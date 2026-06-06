"""Banner image generation runtime and per-payload generation.

Extracted from ``core.py`` in the Unit 3 monolith decomposition.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.logger import plan_logger


def _build_banner_runtime(cfg: Config) -> dict[str, Any] | None:
    if cfg.image_gen is None or not cfg.image_gen.use_image_gen:
        return None

    try:
        from backlink_publisher._util.secrets import load_frw_token
        api_key = load_frw_token()
    except RuntimeError as exc:
        plan_logger.warn(f"image_gen disabled for this run: {exc}")
        return None

    from backlink_publisher.publishing.adapters.image_gen import ImageGenAdapter
    from backlink_publisher.publishing.adapters.image_gen.caps import (
        AutoDisableTracker,
    )
    from backlink_publisher.events.store import EventStore

    adapter = ImageGenAdapter(
        base_url=cfg.image_gen.base_url,
        model=cfg.image_gen.model,
        banner_size=cfg.image_gen.banner_size,
        api_key=api_key,
        timeout_s=cfg.image_gen.timeout_s,
        max_retries=cfg.image_gen.max_retries,
    )
    tracker = AutoDisableTracker(threshold=cfg.image_gen.auto_disable_threshold)
    store = EventStore()
    return {
        "adapter": adapter,
        "tracker": tracker,
        "store": store,
        "config": cfg.image_gen,
        "run_counter": [0],
    }


def _generate_banner_for_payload(
    payload: dict[str, Any],
    *,
    runtime: dict[str, Any],
    llm_provider: Any | None,
) -> dict[str, Any]:
    from backlink_publisher.publishing.adapters.image_gen.caps import (
        check_caps,
        record_cap_hit,
        record_invocation,
    )
    from backlink_publisher.publishing.adapters.image_gen.storage import save_banner
    from backlink_publisher._util.errors import ExternalServiceError

    tracker = runtime["tracker"]
    if tracker.disabled:
        return {"path": None, "status": "auto_disabled"}

    decision = check_caps(
        runtime["store"],
        runtime["config"],
        run_counter=runtime["run_counter"][0],
    )
    if not decision.allowed:
        record_cap_hit(runtime["store"], decision.reason or "unknown")
        return {"path": None, "status": f"capped:{decision.reason}"}

    title = payload.get("title", "")
    body = payload.get("content_markdown", "")
    payload_prompt = str(payload.get("cover_prompt") or "").strip()

    if payload_prompt:
        prompt = payload_prompt
    elif llm_provider is not None:
        try:
            prompt = llm_provider.generate_image_prompt(title, body)
        except Exception as exc:
            plan_logger.warn(f"image prompt LLM failed, falling back: {exc}")
            prompt = f"Professional article cover for: {title}"
    else:
        prompt = f"Professional article cover for: {title}"

    try:
        artifact = runtime["adapter"].generate(prompt)
    except RuntimeError as exc:
        tracker.record_failure()
        msg = str(exc)
        if "401" in msg or "frw-login" in msg:
            return {"path": None, "status": "auth_failed"}
        return {"path": None, "status": "gen_failed"}
    except ExternalServiceError:
        tracker.record_failure()
        return {"path": None, "status": "gen_failed"}
    except Exception as exc:
        plan_logger.warn(f"image_gen unexpected failure: {exc}")
        tracker.record_failure()
        return {"path": None, "status": "gen_failed"}

    try:
        saved_path = save_banner(artifact)
    except Exception as exc:
        plan_logger.warn(f"banner storage failed: {exc}")
        return {"path": None, "status": "storage_failed"}

    record_invocation(runtime["store"], artifact.prompt_sha)
    runtime["run_counter"][0] += 1
    tracker.record_success()

    return {
        "path": str(saved_path),
        "alt": title,
        "mime": artifact.mime,
        "sha": artifact.prompt_sha,
        "source_url": artifact.source_url,
    }
