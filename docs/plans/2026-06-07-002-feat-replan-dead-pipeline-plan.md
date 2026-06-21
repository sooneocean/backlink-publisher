---
title: "feat: 死鏈自動補發 pipeline — replan-dead"
type: feat
status: active
date: 2026-06-07
origin: docs/plans/2026-06-07-001-feat-backlink-remediation-queue-plan.md
claims:
  paths:
    - src/backlink_publisher/cli/replan_dead.py
    - src/backlink_publisher/events/kinds.py
    - pyproject.toml
  shas:
    - 983f5b2
---

# feat: 死鏈自動補發 pipeline — replan-dead

## Overview

Phase B of the quality-and-remediation initiative: adds a `replan-dead` CLI verb
that reads `link.rechecked` events from events.db, selects recent deterministic
dead links (host_gone/link_stripped), extracts the target_url, and emits
`plan-backlinks`-compatible seed JSONL on stdout — one seed per dead link per
platform.

Designed to compose in a shell pipeline:

```bash
recheck-backlinks --probe | replan-dead | plan-backlinks | publish-backlinks --publish
```

## Requirements

- **R1**: Read `link.rechecked` events from events.db — filter to deterministic
  dead (HOST_GONE / LINK_STRIPPED) within a configurable recency window.
- **R2**: For each dead link, emit a `plan-backlinks` seed JSONL row targeting
  the original `target_url` with fields: `target_url`, `language`, `url_mode`,
  `publish_mode`.
- **R3**: Respect remediation events — skip dead links already `resolved`.
- **R4**: Composability — stdout = JSONL only; stderr = diagnostics.
- **R5**: Configurable recency via `--days N` (default 7) and `--min-gap M`
  (minimum live-dofollow count before re-planning, default 3).
- **R6**: Exit 0 advisory; no non-zero exit for data conditions.

## Non-Goals

- No automatic write to the pipeline (operator schedules cron chain).
- No modification to recheck verdicts or remediation event kinds.
- No new event kind needed — reuses `link.rechecked` and `remediation.event`.

## Implementation

### Files created

- `src/backlink_publisher/cli/replan_dead.py` — the CLI verb entry point

### Files modified

- `pyproject.toml` — add `replan-dead` console_scripts entry

### Algorithm

1. Connect to events.db.
2. Query `link.rechecked` events within `--days N` with verdict in
   deterministic dead (host_gone/link_stripped).
3. For each unique `live_url`, check remediation events: if the latest
   action is `resolve`, skip.
4. From the event payload, extract `target_url` and `host`. Group by
   `target_url`.
5. For each unique `target_url`, count how many live-dofollow links already
   exist (by querying `publish.confirmed` or using `--min-gap` heuristic).
   If count >= `--min-gap`, skip (under-linked check satisfied).
6. Emit one seed JSONL row per dead link: `{target_url, language, url_mode,
   publish_mode, platform}` — the platform is the one where the dead link
   was originally published.

### CLI interface

```
replan-dead [--days 7] [--min-gap 3] [--language en] [--url-mode A]
            [--publish-mode draft]
```

Reads events.db directly (no stdin). Emits seed JSONL on stdout.