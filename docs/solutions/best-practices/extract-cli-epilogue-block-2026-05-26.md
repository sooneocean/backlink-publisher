---
title: "Extract CLI Epilogue Block When Monolith Budget Tightens"
date: 2026-05-26
category: best-practices
module: cli / publish_backlinks
problem_type: best_practice
component: tooling
severity: low
applies_when:
  - "A CLI `main()` has a post-loop block handling stats, output writing, and exit-code dispatch"
  - "Monolith budget headroom is tight (<30 SLOC) and the epilogue is the largest remaining self-contained block"
  - "The epilogue references a fixed set of local variables that can be cleanly parameterized"
  - "A helper module already exists in the same package (e.g. `_publish_helpers.py`)"
tags:
  - cli
  - monolith-budget
  - refactoring
  - extraction
  - epilogue
  - publish-backlinks
---

# Extract CLI Epilogue Block When Monolith Budget Tightens

## Context

`publish_backlinks.py` had a 292-SLOC `main()` approaching its `monolith_budget.toml` ceiling of 310 SLOC. The **post-loop epilogue** — reconciliation stats, JSONL output, exit-code dispatch — was a self-contained ~35-SLOC block at the very end of `main()`. It had a single entry point (after the per-row loop finished), no backward control-flow dependencies into the loop body, and referenced only a fixed set of local variables (`outputs`, `rows`, `args`, `run_id`, counters).

It was a natural extraction candidate. After extraction, `main()` dropped to 258 SLOC, and the ceiling was tightened from 310 → 290.

## Guidance

Identify self-contained **epilogue blocks** in CLI `main()` functions and extract them to the existing helpers module. An epilogue block is the code after the main per-row or per-item loop that handles:

1. **Post-loop side effects** — project run outcomes, event store commits
2. **Statistics computation** — successful/failed/unverified counts
3. **Output writing** — JSONL dump to stdout
4. **Exit-code dispatch** — `SystemExit(4)` for failures, `SystemExit(5)` for unverified, `emit_error(5)` for empty

**Extraction criteria** — the block is a good candidate when:

- **Single entry point**: the block runs exactly once, after the loop ends, and has no backward references into loop-internal state
- **Fixed parameter set**: all local variables it touches can be listed on one function signature (7 params in the `_publish_epilogue` case)
- **No return value needed**: the function raises `SystemExit` for non-zero exit codes, so no result object is needed
- **It never modifies loop-internal state**: counters are passed by value; the block only *reads* them

**Signature pattern**:

```python
def _publish_epilogue(
    outputs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    args: Any,
    run_id: str | None,
    success_count: int,
    fail_count: int,
    skipped_unreachable_count: int,
) -> None:
```

Place the function in the existing helpers module (e.g. `_publish_helpers.py`) and import it. After extraction, remember to tighten the `monolith_budget.toml` ceiling: `round_up_to_10(new_SLOC + 30)`.

## Why This Matters

- **Keeps `main()` focused** on the core per-row publish loop rather than post-loop bookkeeping
- **Extracted functions are independently testable** — no mocking of half of `main()` required
- **Consistent pattern** across CLI files makes the codebase easier to navigate and review
- **SLOC headroom** — this extraction freed ~35 SLOC, lowering `main()` from 292 → 258, creating 32 SLOC of headroom against the new ceiling of 290 (`round_up_to_10(258+30)`)
- **Reviewable diffs** — extracting a self-contained block produces a clean diff: the block moves with minimal edits, and the ceiling change is a one-line TOML adjustment

## When to Apply

- The CLI `main()` function is approaching its `monolith_budget.toml` ceiling
- A post-loop epilogue block exists that handles stats/output/exit-code
- The epilogue can take all its inputs as function parameters (no shared mutable state with the loop)
- An appropriate helpers module already exists in the same package
- The extraction would free enough SLOC to keep headroom ≥ 30 after tightening

## Examples

### Before

End of `publish_backlinks.py::main()` — the post-loop epilogue inline:

```python
    # R2: project this run's outcomes into events.db
    if run_id is not None:
        from ..events import project_run_safe
        project_run_safe(run_id)

    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]
    unverified = [s for s in successful if s.get("status", "").endswith("_unverified")]

    publish_logger.recon(...)

    if successful:
        write_jsonl(successful)
    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        raise SystemExit(4)
    if not args.dry_run and not successful:
        emit_error("no payloads were published", exit_code=5)
    if unverified:
        ...
        raise SystemExit(5)

    publish_logger.info(...)
```

### After

The epilogue block replaced by a single call:

```python
    _publish_epilogue(
        outputs,
        rows,
        args,
        run_id,
        success_count,
        fail_count,
        skipped_unreachable_count,
    )
```

The function body in `_publish_helpers.py`:

```python
def _publish_epilogue(
    outputs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    args: Any,
    run_id: str | None,
    success_count: int,
    fail_count: int,
    skipped_unreachable_count: int,
) -> None:
    if run_id is not None:
        from ..events import project_run_safe as _project_run_safe
        _project_run_safe(run_id)

    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]
    unverified = [s for s in successful if s.get("status", "").endswith("_unverified")]

    publish_logger.recon(...)

    if successful:
        from backlink_publisher._util.jsonl import write_jsonl
        write_jsonl(successful)
    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        raise SystemExit(4)
    if not args.dry_run and not successful:
        from backlink_publisher._util.errors import emit_error
        emit_error("no payloads were published", exit_code=5)
    if unverified:
        ...
        raise SystemExit(5)

    publish_logger.info(...)
```

### Budget update

In `monolith_budget.toml`:

```toml
[files."src/backlink_publisher/cli/publish_backlinks.py"]
# Before: ceiling = 310
# After:
ceiling = 290
rationale = "Post-loop epilogue extracted to _publish_epilogue in _publish_helpers.py (2026-05-26). publish_backlinks.py dropped from 280 to 258 SLOC. Ceiling 290 = round_up_to_10(258+30)."
```

## Related

- [`docs/solutions/best-practices/standalone-page-vs-retrofit-webui-2026-05-15.md`](standalone-page-vs-retrofit-webui-2026-05-15.md) — analogous monolith decomposition pattern for Flask/Jinja templates (sibling page over retrofit)
- [`docs/plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md`](../../plans/2026-05-18-006-feat-monolith-sloc-ceiling-plan.md) — plan that established the monolith budget system
- [`docs/plans/2026-05-18-009-refactor-cli-extension-readiness-plan.md`](../../plans/2026-05-18-009-refactor-cli-extension-readiness-plan.md) — CLI decoupling and extraction patterns
- `monolith_budget.toml` — SLOC ceiling policy with rationales for all monitored files
