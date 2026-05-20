---
title: "AI banner image generation as a separate JSONL field (not body markdown), with per-platform CDN re-upload at publish time"
date: 2026-05-20
category: docs/solutions/best-practices
module: cli/plan_backlinks + publishing/adapters/image_gen
problem_type: best_practice
component: image_gen_pipeline
severity: medium
applies_when:
  - "Adding AI-generated images to multi-platform publishing pipelines"
  - "Image source CDN has a TTL that's shorter than the lifetime of the embedded link"
  - "API key for the image-gen endpoint is sensitive enough to warrant out-of-config-toml storage"
  - "Operator wants opt-in image-gen with a cost ceiling"
tags:
  - image-generation
  - openai-compatible
  - credential-rotation
  - sec-3
  - degraded-mode
  - per-platform-upload
  - jsonl-schema
  - content-addressed-storage
---

# AI banner image generation as a separate JSONL field

## The trap we avoided

The naive integration of an image-gen API into a backlink publisher
looks like: prepend `![title](source_url)` to the article body at
plan time and call it done. **This will leave older backlinks broken
within weeks.** Image-gen providers return CDN URLs with TTLs ranging
from a few hours (free-tier providers) to a few weeks (paid). The
moment the URL expires, every backlink article that embedded it shows
a broken image — and the host platform's SEO crawler tags the
article down.

A second trap: putting the API key in `config.toml`. Configuration
files get backed up, screenshotted, pushed to dotfile repos, and
shared. Image-gen keys typically bill per call with no cap; a leak
that goes unnoticed for hours can rack up a five-figure bill.

## The pattern that works

Decompose into five seams. Each is opt-in. Each can be replaced.

### 1. API key in a 0600 file, not in TOML

A dedicated CLI (`frw-login` in our case) prompts via `getpass`
and writes the key to a 0600 JSON file at a path that re-reads
the config-dir env var on every call (not frozen at import time).
The credential rotation pattern from a sibling adapter (in our case
`telegraph_api.py`) provides six load-bearing components: path
resolver / fail-loud load / atomic write / `flock` with jitter /
orphan archive with μs precision / rotate-under-lock. **Copy, don't
abstract** — two adapters with similar credential mechanisms still
diverge in fail-mode taxonomy and migration semantics; lift only
after a fourth file appears.

### 2. Adapter returns bytes, not a URL

The adapter (`ImageGenAdapter.generate(prompt) -> BannerArtifact`)
follows the OpenAI-compatible `/images/generations` contract,
handles both `data[].url` (follow-up GET) and `data[].b64_json`
(inline decode) response shapes, sniffs MIME from magic bytes
(trust bytes over Content-Type — providers mis-report), and enforces
a hard size cap (5 MB default) on both paths. Auth: `Bearer <key>`
header. The follow-up GET does **NOT** forward the Bearer header —
provider CDNs are typically unauthenticated and leaking the key to a
third-party host would be a privacy regression.

Error taxonomy:

- 401 → fail-loud `RuntimeError` naming the rotate CLI in the message
- 429 / 5xx / Timeout / ConnectionError → retryable via marker exception
- Other 4xx / malformed response → fail-loud `ExternalServiceError`
- After retries exhaust → re-wrap marker as `ExternalServiceError`

### 3. Content-addressed local storage

`save_banner(artifact) -> Path` writes to
`<config_dir>/banners/<YYYY-MM>/<sha>.<ext>` where `sha` is the first
16 hex chars of `sha256(prompt)`. Same prompt → same path → skip
rewrite. Critical: the prompt is itself LLM-generated from the
article (title + body[:500] → English image prompt), so identical
inputs across two `plan-backlinks` runs of the same article produce
the same banner without re-paying the bill. Atomic via
`tmp + os.replace` so a crash never leaves a half-written file.

### 4. Caps as events, not counters

Daily + per-run usage caps are evaluated by querying the existing
event store: `COUNT(*) WHERE kind = 'image_gen_invoked' AND
substr(ts_utc, 1, 10) = today`. Per-run is an in-process int
(plan-backlinks is one process). Per-run is checked first because
it's the tighter cap and signals to the operator that THIS RUN is
done — daily headroom is irrelevant if the per-run guard fires.

Cap hits emit a distinct event kind (`image_gen_capped`) that does
NOT count toward the daily quota — missed attempts aren't consumed
quota.

An in-process `AutoDisableTracker` defuses the key-revocation
scenario: 5 consecutive failures → tracker.disabled, no more adapter
calls for the rest of the run. A success resets the counter.
Without this, a revoked key would burn through the entire daily cap
on auth-failed retries.

### 5. Banner as JSONL field, NOT body prefix

The plan-output JSONL row gains a `banner` field with six shapes:

```jsonc
{ ..., "banner": null }                                              // not configured
{ ..., "banner": { "path": "/...", "alt": "...", "mime": "image/png", "sha": "..." } }  // success
{ ..., "banner": { "path": null, "status": "capped:per_run_cap" } }
{ ..., "banner": { "path": null, "status": "capped:daily_cap" } }
{ ..., "banner": { "path": null, "status": "auth_failed" } }
{ ..., "banner": { "path": null, "status": "auto_disabled" } }
{ ..., "banner": { "path": null, "status": "gen_failed" } }
```

The body markdown is never prepended with `![](url)` at plan time.
Per-platform CDN upload happens at publish time via an optional
`embed_banner(artifact_path, alt) -> str | None` method on each
adapter — duck-typed via `hasattr`, no protocol class, no central
registration. Adapters that can't upload (e.g. text-only platforms)
return `None` and the dispatcher falls back to source URL or
gracefully omits.

## Why this matters

The seam between "plan time" and "publish time" is load-bearing. At
plan time we don't know which platform's CDN the banner will live on
— that's a per-publish decision. At publish time we shouldn't be
generating new content. The JSONL banner field carries the bytes
forward; the per-adapter embedder turns them into the final embed.

The decision to NOT cache the banner across plan-backlinks reruns
of *different* articles is intentional: two different articles
SHOULD have different banners. The decision to cache across reruns
of the *same* article (via content-addressed sha) is also
intentional: operators rerun plan-backlinks all the time when
debugging seed-list issues, and burning image-gen quota on those
reruns is a paper-cut that compounds.

## What we'd do differently next time

- The `[image_gen]` section landing in `config.toml` while the API
  key lives in a separate file means operators have two places to
  edit. Future improvement: route both through `save_config` so the
  WebUI's settings form is the single write surface.
- Storage path under `<config_dir>/banners/` collocates banners with
  credentials, which complicates the perms model (credentials want
  0600, banners are fine at default umask). Future split: banners
  under `<cache_dir>/banners/` keeps the perms model clean.
- Per-adapter embedder via `hasattr` is concise but invisible to
  static type checkers. A `runtime_checkable` Protocol class would
  give us LSP completion and mypy warnings at the cost of one extra
  import per adapter.
