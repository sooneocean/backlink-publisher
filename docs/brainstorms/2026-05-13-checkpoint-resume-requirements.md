---
date: 2026-05-13
topic: checkpoint-resume
---

# Checkpoint & Resume for Batch Pipeline

## Problem Frame

`publish-backlinks` processes N articles sequentially and only flushes results to stdout when the entire batch completes. If the process is killed mid-batch (crash, OOM, Ctrl+C, network loss), all in-flight state is lost. On the next run, already-published articles are re-submitted — Blogger has no server-side dedup, so this causes duplicate live posts.

This becomes the **hard prerequisite for #5 Bulk Input**: a 50-URL batch crashing at item 13 loses 37 articles' generation cost without checkpointing, and may re-publish the first 12 as duplicates.

## Requirements

**CLI Checkpoint**

- R1. On `publish-backlinks` start (non-dry-run), generate a `run_id` (`YYYYMMDDTHHMMSS-<4hex>`) and write a checkpoint file to `~/.cache/backlink-publisher/checkpoints/<run_id>.json` containing all input payloads with `status: "pending"` before processing begins.
- R2. After each successful publish, atomically update the item's checkpoint entry to `status: "done"` with `published_url`, `adapter`, and `completed_at`.
- R3. After each failed publish, atomically update the item's checkpoint entry to `status: "failed"` with `error`.
- R4. On normal (or partial-failure) completion, the checkpoint file remains for audit and potential manual resume.
- R5. The `run_id` is emitted to stderr on start: `publish-backlinks: run_id=20260513T152301-a3f2`.

**CLI Resume**

- R6. Add `--resume <run_id>` flag: load the checkpoint, skip items with `status: "done"`, re-process `status: "failed"` and `status: "pending"` items using the original `platform` and `mode` stored in the checkpoint.
- R7. On resume, the output to stdout is the union of previously-done items (from checkpoint) and newly-published items — downstream pipeline receives the full batch.
- R8. On resume, apply the minimum necessary throttle delay for the first Medium article: if the elapsed time since the last Medium `completed_at` in the checkpoint exceeds the maximum throttle window (300s), skip the sleep; otherwise apply the full throttle interval.
- R9. After any `--resume` invocation where all items in the checkpoint are `done` at exit — whether newly completed or already done at load time — mark the checkpoint as `status: "complete"` so the WebUI banner dismisses automatically.

**CLI Housekeeping**

- R10. Add `publish-backlinks --list-runs`: print incomplete runs (any `pending` or `failed` items) from `~/.cache/backlink-publisher/checkpoints/`, showing `run_id`, `started_at`, item counts by status.
- R11. Add `publish-backlinks --cleanup <run_id>`: delete a specific checkpoint file. Add `--cleanup-all`: delete all checkpoint files with `status: "complete"`.

**WebUI Resume Banner**

- R12. On page load, scan checkpoint files; if any run has unresolved (`pending` or `failed`) items, show a dismissible banner above the form: _"Unfinished run from [timestamp] — N articles pending/failed. [Resume]"_
- R13. The "Resume" button triggers `publish-backlinks --resume <run_id>` with the same subprocess invocation as a normal run. Output is handled identically (results parsed and shown, appended to history).
- R14. The banner is dismissed when the underlying checkpoint is marked `complete` or manually deleted via `--cleanup`.

## Success Criteria

- A process killed at any point after R1 completes leaves enough state to resume without re-publishing already-`done` items.
- `--resume` on a fully-completed checkpoint is a safe no-op (0 items processed, full output emitted from cache).
- The WebUI banner appears within one page load of an incomplete run existing.
- No change to downstream pipeline: output JSONL format is identical to a fresh run.

## Scope Boundaries

- Checkpoint covers only `publish-backlinks` — `plan-backlinks` and `validate-backlinks` are stateless and not checkpointed.
- The checkpoint file format is internal; no public API or schema versioning in v1.
- Dry-run (`--dry-run`) does not create or read checkpoints.
- Checkpoint max age / auto-expiry: deferred to a future cleanup job.
- WebUI does not expose a "view checkpoint details" page in v1 — banner + resume only.
- `--resume` does not re-run `plan-backlinks` or `validate-backlinks`; it operates only on the saved input payloads already in the checkpoint.

## Key Decisions

- **JSON not JSONL for checkpoint file**: rewrite-per-item is safe for N ≤ 100 articles; avoids last-entry-wins ambiguity of append-only JSONL.
- **Atomic write via tempfile+rename**: prevents corrupt checkpoint if killed during write.
- **run_id format `YYYYMMDDTHHMMSS-<4hex>`**: human-readable for `--list-runs`; 4-hex suffix generated via `os.urandom(2).hex()`, giving 1/65536 collision probability for concurrent runs started in the same second — acceptable for single-user CLI. On collision, the second run overwrites the first checkpoint.
- **Failed items always retried on resume**: matches confirmed product decision; adapter retry logic (PR #1) already handles transient errors during each attempt.
- **Original params stored in checkpoint**: `platform` and `mode` are saved in checkpoint header so `--resume` doesn't require re-specifying them.

## Dependencies / Assumptions

- PR #1 (adapter retry with exponential backoff) is already merged — resume retries at the article level, retry handles transient errors within each attempt.
- `~/.cache/backlink-publisher/` is accessible via `config.py`'s `_cache_dir()`, but the `checkpoints/` subdirectory must be created at first use (`mkdir(parents=True, exist_ok=True)`).
- WebUI subprocess invocation pattern is unchanged; `--resume` is a drop-in new flag.

## Outstanding Questions

### Resolve Before Planning

_(none — all product decisions are resolved)_

### Deferred to Planning

- [Affects R2][Technical] Atomic write: use `tempfile.NamedTemporaryFile` + `Path.replace()` or write to `.tmp` sibling — confirm cross-platform behavior on Windows. On macOS APFS, rename is POSIX-atomic but not fsync-durable; consider fsyncing the parent directory post-rename for power-loss safety (adds latency per item).
- [Affects R7][Technical] Resume stdout union: load `done` items' `to_publish_output` data from checkpoint and emit first, then stream new results — verify downstream parsers handle mixed-timestamp JSONL.
- [Affects R12][Needs research] WebUI checkpoint scan: called on every page load — confirm `_load_history()` pattern (JSON read) is fast enough or add a lightweight glob count first.

## Next Steps

→ `/ce:plan` for structured implementation planning
