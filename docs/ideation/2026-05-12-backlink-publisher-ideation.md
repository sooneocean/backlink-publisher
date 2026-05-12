---
date: 2026-05-12
topic: backlink-publisher-improvements
focus: open-ended
---

# Ideation: backlink-publisher Improvements

## Codebase Context

**Project**: Python 3.11+ CLI tool + Flask web UI for publishing SEO backlink articles to Blogger and Medium.

**Architecture**: Three composable CLI commands (`plan-backlinks` → `validate-backlinks` → `publish-backlinks`) pipe JSONL; Flask webui.py wraps them via subprocess. Adapters: Blogger API v3, Medium API + Playwright browser fallback. Config: TOML file at `~/.config/backlink-publisher/config.toml`.

**Current pain points**: no retry logic on API failures; no batch/campaign UX; config requires manual TOML editing; OAuth expiry causes mid-batch failures; no article editing before publish; CLI pipeline has no checkpoint/resume for partial failures; publish-history is a flat JSON file.

**Key leverage points**: CLI is fully composable over JSONL; adapter pattern makes new platforms straightforward; Playwright already installed; `linkcheck.py` and `language_check.py` exist but underused; `_medium_selectors.py` isolates Medium selector drift.

---

## Ranked Ideas

### 1. Auto-Retry with Exponential Backoff
**Description:** Wrap every adapter network call (Blogger API, Medium API, Playwright publish) in a `@retry_transient(max_attempts=3, backoff_base=2)` decorator in `adapters/base.py`. Catches `requests.Timeout`, `ConnectionError`, HTTP 429, and HTTP 5xx. Each retry waits `2^attempt` seconds with ±10% jitter. Only escalates to `ExternalServiceError` after all attempts fail.
**Rationale:** Transient network errors currently abort the entire batch with exit code 4 — already-completed items are written, but remaining items are lost. Retry makes individual article failures self-healing without user intervention. Low burden because the error hierarchy contract is already established.
**Downsides:** Masks real configuration errors if jitter isn't tuned. Need to distinguish retryable (429, timeout) vs. non-retryable (401, 403) errors carefully.
**Confidence:** 92%
**Complexity:** Low
**Status:** Explored — brainstorm started 2026-05-12

---

### 2. Bulk URL Batch Input (CSV / Paste / Sitemap)
**Description:** Accept `plan-backlinks --from-csv urls.csv` or `--from-sitemap https://example.com/sitemap.xml` to process N target URLs in one invocation. Each URL becomes one JSONL payload flowing through the existing pipeline unchanged. Web UI adds a "paste multiple URLs" text area as a zero-config entry point.
**Rationale:** Running the full plan→validate→publish flow manually for each URL is the dominant time cost for any real campaign. The CLI pipeline is already composable over JSONL streams — this is an input multiplier with no pipeline changes.
**Downsides:** Sitemap parsing requires an XML dependency (or stdlib `xml.etree`). Large batches need the Checkpoint & Resume idea (#5) to be useful in production.
**Confidence:** 90%
**Complexity:** Low
**Status:** Unexplored

---

### 3. Named Campaign Profiles
**Description:** Introduce named profiles that bundle platform, language, mode, tags, title, and blog_id settings under a short name (e.g., `client-acme-en-blogger`). Stored in `config.toml` under `[profiles.<name>]`. CLI: `plan-backlinks --profile client-acme`. Web UI: dropdown populates all form fields instantly. Creating a profile is a one-click "Save as profile" from any completed run.
**Rationale:** Every repeat run for the same client requires re-entering identical settings. Agencies managing multiple clients today have to manually swap `config.toml` entries or risk cross-client publishing. Profiles eliminate that entirely.
**Downsides:** Config schema extension required. Profiles with stale blog_ids will fail silently until validated.
**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

---

### 4. Proactive OAuth Health Management
**Description:** In `_build_credentials()` (blogger_api.py L17-63), add a pre-flight check: if token expires within 5 minutes, refresh immediately before any API call. In the webui, expose a `/api/token-status` endpoint that returns token health; nav bar displays a colored badge ("Token OK" / "Expires in 3 days" / "Expired — click to re-auth"). Clicking the badge triggers the in-page OAuth flow without leaving current context.
**Rationale:** Current code only refreshes after `creds.expired` is True — which has a 60-second tolerance window that still causes first-call 401 failures mid-batch. Users don't discover expiry until a batch fails. Proactive visibility converts a surprise failure into a planned 30-second re-auth.
**Downsides:** UI polling adds a lightweight background request. Requires storing token expiry separately (already in the token JSON from Google).
**Confidence:** 88%
**Complexity:** Low
**Status:** Unexplored

---

### 5. Checkpoint & Resume for Batch Pipeline
**Description:** `publish-backlinks` writes each payload's `id` to `~/.cache/backlink-publisher/checkpoints/<run_id>.jsonl` as `pending` before processing, then updates to `done` or `failed`. Add `publish-backlinks --resume <run_id>` to skip `done` items and retry only `failed`/`pending`, preserving throttle intervals. In the web UI, a "Resume" banner appears on page load if an unfinished run exists.
**Rationale:** A crash or network failure mid-batch (at article 13/20) currently requires restarting from scratch — risking duplicate publishes on already-completed articles (Blogger has no dedup). Checkpoint resume makes reruns idempotent and saves hours of regeneration time for large batches.
**Downsides:** Checkpoint files accumulate; needs a `--cleanup` flag. Browser session state (generated articles) still needs separate persistence from CLI state.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

---

### 6. Config Init Wizard (First-Run Setup)
**Description:** A `backlink-publisher config init` CLI command (and matching `/setup` webui route) walks users through each required config field: paste API keys, authorize Blogger (browser popup), paste Medium token. Immediately validates each credential before writing. If validation fails, prints the exact URL to the relevant API console page. Writes `config.toml` automatically on completion.
**Rationale:** Manually editing TOML with no validation is the highest abandonment point. Users commonly spend an hour debugging a token typo before getting their first successful publish. `save_config()` in `config.py` already handles writing — this is purely a guided front-end.
**Downsides:** OAuth popup in terminal context requires browser availability. Medium token validation requires a live API call.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

---

### 7. Inline Article Editor Before Publish
**Description:** After `plan-backlinks` generates articles, render each article's `content_markdown` in an editable `<textarea>` (or lightweight CodeMirror instance) in the web UI review step. Edits are serialized back into the JSONL payload before passing to `validate-backlinks`. A "Reset to original" button restores the AI-generated content.
**Rationale:** Users currently have no agency over generated content before it goes live. Editing opportunities exist between generation and validation — a clear dead zone in the current flow. This fills the most obvious missing capability in the web UI pipeline.
**Downsides:** Rich editor (CodeMirror) adds ~100KB JS. Plain `<textarea>` with no preview is a simpler alternative. Round-tripping edited markdown back into JSONL requires careful escaping.
**Confidence:** 82%
**Complexity:** Medium
**Status:** Unexplored

---

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | SQLite full state migration | Covered by targeted checkpoint/history improvements without migration risk |
| 2 | Async Job Queue (Celery/threads) | High architecture burden; loading overlay already addresses immediate UX need |
| 3 | Google Search Console indexing watchdog | GSC scraping violates ToS and is technically fragile |
| 4 | Playwright as platform scouter | Scope creep — this is a different product (link prospecting vs. publishing) |
| 5 | Language-sharded publishing strategy | High complexity, niche audience |
| 6 | Content quality feedback loop | Speculative LLM engineering with long feedback cycles |
| 7 | Pre-publish browser preview | Expensive (full Playwright render per article); niche benefit |
| 8 | Article snapshot export | Low value; manual clipboard copy is sufficient for occasional need |
| 9 | Multi-platform fanout | Premature — Substack/Dev.to/LinkedIn adapters don't exist yet |
| 10 | Plain-English Dry-Run Report Card | Good UX polish but lower priority than filling structural gaps |
| 11 | Publish Scheduler (per-day budget) | Overlaps with Campaign Profiles + Bulk Input; can be added as campaign attribute later |
| 12 | Correlation ID thread-through | Low user-facing value; internal debugging improvement for later |
| 13 | Content recycling via re-generation | Niche power-user scenario; low priority given structural gaps |
| 14 | Quality gate pre-flight scoring | Overlaps with validate-backlinks; marginal improvement |
| 15 | Webhook-triggered publish mode | Interesting leverage but low immediate user demand |
| 16 | Medium CAPTCHA interactive recovery | High complexity Playwright interaction; medium value |
| 17 | SEO risk scoring from history | Speculative; no clear threshold data to calibrate alerts |
| 18 | Post-publish link health monitor | Low burden (linkcheck.py exists) — close call; deferred as V2 polish |

---

## Session Log
- 2026-05-12: Initial open-ended ideation — 38 raw candidates generated (5 agents), 25 unique after dedup, 7 survivors after adversarial filtering
- 2026-05-12: Idea #1 (Auto-Retry with Exponential Backoff) selected for brainstorm
