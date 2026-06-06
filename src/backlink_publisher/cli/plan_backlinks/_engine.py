"""Pure plan-backlinks engine — no process-global side effects.

Thin-WebUI Phase 2 Unit 7 (plan ``2026-05-27-004``). Extracted from
``cli/plan_backlinks/core.py`` so the CLI shell and the in-process
``PipelineAPI`` bridge share one generation kernel.

This module is PURE compute. It MUST NOT:
- touch ``sys.stdout`` / ``sys.stderr`` (H3 — caller owns I/O);
- call ``set_log_level`` (H1 — flips verbosity for the shared scheduler thread);
- call ``content_fetch.reset_stats()`` (H2 — caller manages; in-process path
  accepts process-aggregate stats and documents it);
- raise ``SystemExit`` / call ``emit_envelope_and_exit`` (caller maps exit codes);
- emit the config_echo banner (caller's responsibility).

It returns a :class:`PlanOutcome`. The shell (``core.py``:``main()``) and the
in-process ``PipelineAPI.plan()`` both call :func:`plan_rows`, differing only in:
- stdout serialization (shell calls ``write_jsonl``; API builds a StringIO JSONL);
- ``content_fetch.reset_stats()`` (shell resets before calling; API does not);
- config_echo banner emission (shell emits; API skips — SHA computed directly).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Iterator

# Importing the adapters package populates the registry via ``register()``
# side effects. The engine triggers registration itself rather than relying
# on the caller — mirrors validate/engine.py so the engine is correct
# in-process even if no shell imported adapters first.
import backlink_publisher.publishing.adapters  # noqa: F401
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.config import Config, get_anchor_pool_v2, get_three_url_config
from backlink_publisher.config_echo import compute_config_sha
from backlink_publisher.content import fetch as content_fetch
from backlink_publisher.publishing.adapters.llm_anchor_provider import (
    OpenAICompatibleProvider,
)
from backlink_publisher.schema import validate_input_payload

from ._banners import _build_banner_runtime, _generate_banner_for_payload
from ._links import (
    _ContentGateRowFailure,
    _SUPPORTING_URLS_FOR_PREFETCH,
    _collect_candidate_urls_for_row,
)
from ._payload import _generate_payload, dofollow_tier_metadata


@dataclass
class PlanOutcome:
    """Result of a plan-backlinks generation run.

    - ``outputs``: generated payload dicts (stream to stdout).
    - ``errors``: human-readable per-row errors (caller → exit-2 / PipeResult).
    - ``*_drops``: 1-based row indices that vanished at each gate.
    - ``content_fetch_stats``: snapshot taken AFTER the run; reflects cumulative
      fetch counters from this process lifetime (the CLI shell resets them before
      calling the engine; the in-process path does not — documented acceptable).
    """

    outputs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validation_drops: list[int] = field(default_factory=list)
    generation_drops: list[int] = field(default_factory=list)
    content_gate_drops: list[int] = field(default_factory=list)
    cell_gate_drops: list[int] = field(default_factory=list)
    content_fetch_stats: dict[str, Any] = field(default_factory=dict)


# ── helpers (moved here from core.py; core.py re-exports for backward compat) ─


def _cell_gate_drop(
    main_domain: str,
    platform: str,
    cell_assignments: dict[str, list[str]],
) -> bool:
    """Return True if the row should be dropped by the cell admission gate.

    A row is dropped only when the site is enrolled (has a ``[cells.*]``
    entry) AND the platform is not in that cell. Sites without a cell entry
    pass through unchanged — opt-in semantics. An empty ``cell_assignments``
    dict never drops any row.

    ``main_domain`` is normalised (trailing slash stripped) to match the
    parse-time normalisation in ``config/parsers/cells.py``.
    """
    domain = main_domain.rstrip("/")
    if domain not in cell_assignments:
        return False  # unenrolled site — unrestricted
    return platform not in cell_assignments[domain]


def _emit_link_count_recon(payload: dict[str, Any], *, branch: str) -> None:
    links = payload.get("links") or []
    kinds = sorted({lk.get("kind", "?") for lk in links})
    plan_logger.recon(
        "link_count_at_plan",
        branch=branch,
        count=len(links),
        kinds=kinds,
        main_domain=payload.get("main_domain", ""),
        article_id=payload.get("id", ""),
    )


def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    from backlink_publisher.cli.plan_backlinks import _scheduler_enabled_for as _inner
    return _inner(config, main_domain)


def _apply_zero_cost_and_emit(
    payload: dict[str, Any],
    row: dict[str, Any],
    branch: str,
) -> dict[str, Any]:
    """Apply zero-cost citability levers (freshness + entity_claim) to payload.

    Used for zh_short and work_themed branches (R11). Mutates content_markdown
    in-place and records applied levers in metadata.
    """
    from ._citability import apply_zero_cost_levers
    from ._templates import _domain_label_of
    lang = payload.get("language") or row.get("language", "en")
    main_domain = payload.get("main_domain") or row.get("main_domain", "")
    domain_label = _domain_label_of(main_domain.rstrip("/"))
    body = payload.get("content_markdown") or ""
    augmented, applied = apply_zero_cost_levers(body, domain_label, language=lang)
    payload = dict(payload)
    payload["content_markdown"] = augmented
    payload["_citability_levers"] = applied
    return payload


def _dispatch_row(
    row: dict[str, Any],
    config: Config,
    *,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None,
    work_count: int,
    fetch_verify_enabled: bool = True,
) -> Iterator[dict[str, Any]]:
    three_url_cfg = get_three_url_config(config, row["main_domain"])
    target_language = row.get("target_language", row["language"])
    use_native_schedulers = target_language == row["language"]
    if three_url_cfg is not None and use_native_schedulers:
        from backlink_publisher.cli.plan_backlinks import _plan_work_themed_row
        for payload in _plan_work_themed_row(row, three_url_cfg, count=work_count):
            payload = _apply_zero_cost_and_emit(payload, row, "work_themed")
            _emit_link_count_recon(payload, branch="work_themed")
            yield payload
        return

    payload: dict[str, Any] | None = None
    if (
        row["language"] == "zh-CN"
        and use_native_schedulers
        and _scheduler_enabled_for(config, row["main_domain"])
    ):
        from backlink_publisher.cli.plan_backlinks import _plan_zh_short_row
        payload = _plan_zh_short_row(row, config, llm_provider, rng=rng)
        if payload is not None:
            payload = _apply_zero_cost_and_emit(payload, row, "zh_short")
            _emit_link_count_recon(payload, branch="zh_short")
            yield payload
            return
    if payload is None:
        payload = _generate_payload(
            row, config=config, fetch_verify_enabled=fetch_verify_enabled,
        )
    _emit_link_count_recon(payload, branch="long_form")
    yield payload


# ── public engine entry-point ──────────────────────────────────────────────────


def plan_rows(
    rows: list[dict[str, Any]],
    cfg: Config,
    *,
    work_count: int = 10,
    fetch_verify_enabled: bool = True,
) -> PlanOutcome:
    """Generate backlink article payloads from ``rows`` against ``cfg``. Pure compute.

    Caller responsibilities (NOT done here, per hazard audit):
    - **H1**: call ``set_log_level`` before invoking if verbosity matters.
    - **H2**: call ``content_fetch.reset_stats()`` before invoking if per-run
      stats are required; otherwise, the snapshot in ``outcome.content_fetch_stats``
      reflects process-lifetime cumulative counters.
    - **H3**: write ``outcome.outputs`` to ``sys.stdout`` (the caller owns I/O).

    Does NOT call ``emit_envelope_and_exit`` / ``SystemExit``; the caller maps
    ``outcome.errors`` to an exit code / ``PipeResult.error``.
    """
    outcome = PlanOutcome()

    # Build per-call derived config objects (pure: no shared mutable state).
    llm_provider: OpenAICompatibleProvider | None = None
    if cfg.llm_anchor_provider is not None:
        llm_provider = OpenAICompatibleProvider(
            base_url=cfg.llm_anchor_provider.base_url,
            api_key=cfg.llm_anchor_provider.api_key,
            model=cfg.llm_anchor_provider.model,
            timeout_s=cfg.llm_anchor_provider.timeout_s,
            temperature=cfg.llm_anchor_provider.temperature,
            system_prompt=cfg.llm_anchor_provider.system_prompt,
        )

    image_gen_runtime = _build_banner_runtime(cfg)
    config_sha = compute_config_sha(cfg)
    rng = random.Random()
    cells = cfg.cell_assignments

    # ── Cell gate summary recon (before row loop) ──────────────────────────
    if cells:
        run_domains = {row.get("main_domain", "") for row in rows}
        enrolled = sorted(run_domains & set(cells))
        unrestricted = sorted(run_domains - set(cells))
        plan_logger.recon(
            "cell_gate_summary",
            enrolled=enrolled,
            unrestricted=unrestricted,
            n_enrolled=len(enrolled),
            n_unrestricted=len(unrestricted),
        )

    # ── Prefetch batch (optional, controlled by caller flag) ───────────────
    if fetch_verify_enabled:
        validated_rows: list[dict[str, Any]] = []
        for row in rows:
            if not validate_input_payload(row, 0):
                validated_rows.append(row)
        prefetch_set: set[str] = set()
        for row in validated_rows:
            prefetch_set.update(_collect_candidate_urls_for_row(row, cfg))
        prefetch_set.update(_SUPPORTING_URLS_FOR_PREFETCH)
        if prefetch_set:
            content_fetch.verify_urls_batch(list(prefetch_set), max_workers=10)
            plan_logger.recon(
                "content_fetch_prefetch",
                n_urls_prefetched=len(prefetch_set),
                n_rows=len(validated_rows),
            )

    # ── Per-row generation loop ────────────────────────────────────────────
    for line_num, row in enumerate(rows, start=1):
        errs = validate_input_payload(row, line_num)
        if errs:
            outcome.errors.extend(errs)
            outcome.validation_drops.append(line_num)
            continue

        if _cell_gate_drop(row.get("main_domain", ""), row.get("platform", ""), cells):
            plan_logger.recon(
                "cell_gate_drop",
                main_domain=row.get("main_domain", ""),
                platform=row.get("platform", ""),
                line_num=line_num,
                cell=cells.get(row.get("main_domain", ""), []),
            )
            outcome.cell_gate_drops.append(line_num)
            continue

        try:
            for payload in _dispatch_row(
                row, cfg,
                llm_provider=llm_provider,
                rng=rng,
                work_count=work_count,
                fetch_verify_enabled=fetch_verify_enabled,
            ):
                branded_pool = get_anchor_pool_v2(
                    cfg, payload["main_domain"], "home", "branded"
                )
                metadata = dict(payload.get("metadata") or {})
                metadata["branded_pool"] = list(branded_pool)
                metadata["config_sha"] = config_sha
                metadata.update(dofollow_tier_metadata(payload["platform"]))
                # Promote _citability_levers from payload top-level into metadata.
                if "_citability_levers" in payload:
                    metadata["citability_levers"] = payload.pop("_citability_levers")
                payload["metadata"] = metadata

                if image_gen_runtime is not None:
                    payload["banner"] = _generate_banner_for_payload(
                        payload,
                        runtime=image_gen_runtime,
                        llm_provider=llm_provider,
                    )
                else:
                    payload["banner"] = None

                plan_logger.debug(
                    f"generated payload: id={payload['id']} platform={payload['platform']}",
                    extra={"id": payload["id"], "platform": payload["platform"]},
                )
                outcome.outputs.append(payload)
        except _ContentGateRowFailure as exc:
            outcome.errors.append(
                f"line {line_num}: content-gate failure: kind={exc.kind} "
                f"url={exc.url} reason={exc.reason}"
            )
            outcome.content_gate_drops.append(line_num)
        except Exception as exc:
            outcome.errors.append(f"line {line_num}: generation error: {exc}")
            outcome.generation_drops.append(line_num)

    # ── Reconciliation recon log ───────────────────────────────────────────
    plan_logger.recon(
        "plan_reconciliation",
        input_rows=len(rows),
        output_rows=len(outcome.outputs),
        delta=len(rows) - len(outcome.outputs),
        dropped={
            "validation": len(outcome.validation_drops),
            "generation": len(outcome.generation_drops),
            "content_gate": len(outcome.content_gate_drops),
            "cell_gate": len(outcome.cell_gate_drops),
        },
        dropped_line_numbers={
            "validation": outcome.validation_drops,
            "generation": outcome.generation_drops,
            "content_gate": outcome.content_gate_drops,
            "cell_gate": outcome.cell_gate_drops,
        },
    )

    outcome.content_fetch_stats = content_fetch.stats_snapshot()
    plan_logger.recon("content_fetch_stats", **outcome.content_fetch_stats)

    return outcome
