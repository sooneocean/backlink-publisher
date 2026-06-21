---
title: "feat: Pre-publish Quality Gate — quality-gate"
type: feat
status: active
date: 2026-06-07
origin: docs/plans/2026-06-07-001-feat-backlink-remediation-queue-plan.md
claims:
  paths:
    - src/backlink_publisher/cli/quality_gate.py
    - src/backlink_publisher/events/kinds.py
    - pyproject.toml
  shas:
    - 983f5b2
---

# feat: Pre-publish Quality Gate — quality-gate

## Overview

Phase C of the quality-and-remediation initiative: adds a `quality-gate` CLI
verb that checks article quality before publish. Acts as a filter — reads
`plan-backlinks` seed JSONL on stdin, runs deterministic quality checks, and
emits passing rows on stdout. Blocked rows are emitted as stderr diagnostics
and optionally as `publish.quality_blocked` events to events.db.

Designed to compose in a shell pipeline:

```bash
... | plan-backlinks | quality-gate | publish-backlinks --publish
```

## Requirements

- **R1**: **Anchor density check** — per article, count external links / words
  in `article_content_markdown` (or generated body). If density > threshold
  (default 5%), emit `quality.anchor_density_high` and block the row.
- **R2**: **Content uniqueness check** — compare generated content SHA256 against
  events.db published articles. If fuzzy match > 70% overlap, block.
- **R3**: **AI-draft LLM scoring** (optional `--quality-llm`) — send article to
  LLM for a 0-100 quality score. Below threshold (default 40) → block.
- **R4**: Blocked rows are skipped (exit 0, no batch interruption). Blocked
  rows are logged on stderr with reason. If `--emit-events`, emit
  `publish.quality_blocked` event to events.db.
- **R5**: Deterministic checks (R1+R2) run by default; LLM check (R3) requires
  `--quality-llm` flag.
- **R6**: Composability — stdin JSONL seed, stdout JSONL (filtered passing rows),
  stderr diagnostics.

## Non-Goals

- No new LLM dependency (LLM check is optional, flagged opt-in).
- No modification to existing CLI entry points (plan-backlinks, validate-backlinks,
  publish-backlinks).
- No WebUI integration (deferred — operators can compose in CLI pipeline).

## Implementation

### Files created

- `src/backlink_publisher/cli/quality_gate.py` — the CLI verb entry point

### Files modified

- `pyproject.toml` — add `quality-gate` console_scripts entry
- `src/backlink_publisher/events/kinds.py` — add `PUBLISH_QUALITY_BLOCKED` event kind

### Algorithm

1. **Anchor density**: parse `article_content_markdown`, extract links via
   markdown-it-py (or regex `\[.*?\]\(.*?\)`). Count words (whitespace-split).
   If links/words > `--max-density` (default 0.05 = 5%), block.
2. **Content uniqueness**: compute SHA256 of stripped content. Query events.db
   for similar content via SHA256 prefix match (first 8 hex chars). If any
   match, compute Jaccard similarity on word-sets between candidate and stored
   body. If > `--max-similarity` (default 0.70), block.
3. **LLM scoring** (opt-in): send `{title, body, target_url}` to LLM with a
   quality-rating prompt. Expect a JSON response `{"score": N}`, N=0-100.
   If N < `--quality-min` (default 40), block.

### CLI interface

```
quality-gate [--max-density 0.05] [--max-similarity 0.70]
             [--quality-llm] [--quality-min 40]
             [--emit-events]
```

Reads seed JSONL on stdin, writes filtered seed JSONL on stdout.