---
date: 2026-06-06
topic: automated-backlink-dashboard-wizard
---

# Automated Backlink Dashboard & Setup Wizard

## Summary

Transform the existing CLI-first backlink publisher into an automated dashboard product: a setup wizard guides the operator through binding channels and configuring seed sources, then an event-driven engine monitors those sources and auto-publishes backlinks to fill coverage gaps. A dashboard (Wave 2) surfaces score accumulation, backlink health, coverage gaps, and planning surfaces.

## Problem Frame

The operator currently interacts with the publisher as a manual CLI pipeline: plan → validate → publish, run per batch. There is no continuous automation — new target URLs require manually re-running the pipeline. Channel binding is functional but scattered across settings pages without guided onboarding. There is no persistent score or progress system to show cumulative impact, and no single dashboard that answers "how is the overall campaign doing right now?" without clicking through multiple screens and CLI logs.

The existing infrastructure (WebUI, APScheduler, queue store, PipelineAPI, events.db, channel binding) is 70% of what this needs — the missing pieces are the guided onboarding flow (wizard), the event-driven watch loop, the scoring projection, and the unified dashboard view.

## Actors

- **A1. Operator (single user)**: Runs the tool locally. Owns multiple publishing channels (Medium, Blogger, Velog, Telegraph, etc.). Manages target URLs and wants continuous automated publishing with visibility into results.

## Key Flows

### F1. First-Time Onboarding (Setup Wizard)
- **Trigger:** First launch of WebUI after install, or explicit `/wizard` entry
- **Actors:** A1
- **Steps:**
  1. Welcome screen — explain what the wizard does
  2. Configure seed sources — add sitemap URLs, manual target list, or import bookmarks
  3. Bind publishing channels — guided flow through each channel's binding (reuse existing browser-binding for velog/medium/blogger, token-paste for others)
  4. Set automation rules — per-channel dofollow preference, language filter, daily publish cap
  5. Review configuration summary
  6. Launch — starts the watch service and queue processor
- **Outcome:** All channels bound, seed sources configured, automation running. Operator lands on the main index page with a "system active" indicator.
- **Covered by:** R1, R2, R3, R4, R5

### F2. Incremental Channel Binding (Mini-Wizard)
- **Trigger:** Operator clicks "Add Channel" in settings after initial onboarding
- **Actors:** A1
- **Steps:**
  1. Select platform from registered platforms list
  2. Guided binding flow specific to that platform's auth type (browser / token-paste / OAuth)
  3. Set per-channel automation rules (dofollow preference, throttle band, language whitelist)
  4. Confirm binding — channel appears in dashboard with `已綁定 ✓` badge
- **Outcome:** New channel bound and immediately active in the automation engine.
- **Covered by:** R3, R4

### F3. Event-Driven Auto-Publish
- **Trigger:** Watch service detects a new URL in any configured seed source (sitemap diff, new bookmark, new manual entry)
- **Actors:** System, A1 (passive observer)
- **Steps:**
  1. Watch service (APScheduler job, configurable interval) polls all seed sources
  2. Compares current state against previously-seen URLs (stored in `seen_urls` store)
  3. For each new URL, checks coverage: which channels already have a backlink to this target?
  4. Selects best channel(s) for the gap (prioritize dofollow platforms, respect language/domain rules)
  5. Pushes publish task to queue_store with full config
  6. Queue worker (existing APScheduler) executes publish via PipelineAPI
  7. On success: records event, awards score, marks URL as covered for that channel
  8. On failure: retry with backoff, mark channel as expired if auth fails
- **Outcome:** New backlinks are published continuously without manual intervention. Operator sees them appear in history and score increments on dashboard.
- **Covered by:** R5, R6, R7, R8, R9

### F4. Dashboard Review (Wave 2)
- **Trigger:** Operator navigates to `/dashboard`
- **Actors:** A1
- **Steps:**
  1. Dashboard loads with last-projected aggregates
  2. Shows: total score + trend, per-channel score breakdown, backlink health (alive/drifted/dead), publish volume + success rate, coverage gap matrix
  3. Operator can filter by time window (7d / 30d / all)
  4. Operator can click into any channel to see detail
  5. "Plan ULW" section suggests next targets based on coverage gaps
- **Outcome:** Operator has a one-screen answer to "how is the campaign doing?"
- **Covered by:** R10, R11, R12, R13

## Requirements

### Setup Wizard

- **R1.** The WebUI MUST provide a multi-step setup wizard at `/wizard` that guides the operator through first-time configuration. The wizard MUST be skippable (returning operators can use settings pages directly).
- **R2.** The wizard MUST support at least these seed source types: (a) sitemap URL polling, (b) manual target URL list (text input), (c) bookmark file import (HTML format). Additional source types MAY be added via the same plugin pattern.
- **R3.** The wizard MUST integrate with the existing channel binding flow — for each selected platform, the operator goes through the correct auth flow (browser-based for velog/medium/blogger, token-paste for others) without leaving the wizard sequence.
- **R4.** After wizard completion, a configuration summary MUST be persisted and the automation engine MUST start automatically. The operator MUST see a "system active" status indicator on the main page.

### Automation Engine (Watch + Queue)

- **R5.** A new `watch_service` background service (APScheduler job) MUST poll configured seed sources on a configurable interval (default: every 6 hours). It MUST detect new URLs by diffing against a persisted `seen_urls` store.
- **R6.** When a new URL is detected, the service MUST check coverage: for each active channel, does a backlink to this target already exist? The coverage check SHOULD use `equity-ledger` data when available, and MUST NOT re-publish to channels that already cover the target.
- **R7.** The service MUST select the best channel(s) for each uncovered target based on: dofollow preference (prioritize dofollow platforms), language compatibility, platform authority tier, and per-channel daily publish cap. If no suitable channel exists, the URL MUST be logged as "uncovered" with reason.
- **R8.** Selected publish tasks MUST be pushed to the existing `queue_store` and consumed by the existing APScheduler queue worker. The watch service MUST NOT execute publishes directly.
- **R9.** The automation engine MUST respect the existing rate-limit/retry/backoff mechanisms in the queue worker (429 handling, AuthExpiredError detection, channel status flip).

### Scoring System

- **R10.** A new scoring engine MUST compute and persist scores per publish event. The base formula: `score = base(1) × platform_weight × dofollow_multiplier × survival_bonus`. Initial defaults: platform_weight from dofollow tier (dofollow=True=1.0, False=0.3, uncertain=0.5), survival_bonus=1.0 (1.2 if recheck confirms alive after 30 days).
- **R11.** Scores MUST be stored in a new `score_store` (JSON store under `webui_store/`) or as new event types in `events.db`. The choice between JSON and events.db is a planning decision.
- **R12.** Score computation MUST be retroactively applicable — existing successful publishes in history SHOULD be scored when the scoring engine first runs (backfill).

### Dashboard (Wave 2)

- **R13.** The dashboard at `/dashboard` (or extending `/ce:health`) MUST show: total accumulated score with trend chart, per-channel score breakdown, publish volume + success rate (7d/30d), backlink health summary (alive / dofollow-lost / dead), and coverage gap matrix (which targets are under-covered on which platforms).
- **R14.** The dashboard MUST include a "Plan ULW" section that surfaces the top N uncovered or under-covered targets sorted by priority (equity deficit, domain authority), with one-click action to push them to the queue.
- **R15.** Every dashboard view MUST distinguish "no data yet" from zero values, and MUST stamp freshness ("as of" timestamp).

## Acceptance Examples

- **AE1. Covers R1, R2, R3, R4.** First-time operator launches WebUI. The wizard starts automatically. They add a sitemap URL, bind Medium via browser login, set daily cap to 5. After completing, the status indicator shows active. New URLs from the sitemap appear in queue within the next polling cycle.
- **AE2. Covers R5, R6, R7, R8.** A new target URL is added to the manual target list. Within the next polling interval, the watch service detects it, sees no existing backlink on any channel, selects the highest-dofollow channel, and pushes a publish task to the queue. The queue worker executes it within seconds.
- **AE3. Covers R10, R11.** After a successful publish, the operator checks the score store and sees base 1 point × platform_weight 1.0 (dofollow) = 1.0 points. After 30 days, the recheck confirms the link is alive; score updates to 1.2.
- **AE4. Covers R13, R14.** Operator opens dashboard after two weeks of automated publishing. They see: 47 total points, Medium contributes 22 points, backlink health shows 42 alive / 3 drifted / 1 dead. The "Plan ULW" section lists 8 under-covered targets. They click one to push it to queue.

## Success Criteria

- An operator can go from fresh install to automated publishing running in under 5 minutes, following the wizard.
- After setup, new target URLs added to any seed source are automatically published to the best available channel within `polling_interval + queue_latency` without any manual pipeline command.
- The dashboard answers "how is the overall campaign?" in under 10 seconds from a single screen.
- A reconciliation of published rows against scored points matches exactly (no missed or double-counted scores).
- Channel health warnings (expired auth, persistent failures) are surfaced before the next automated publish attempt.

## Scope Boundaries

### Deferred for later

- **Full calendar-based schedule UI**: v1 uses interval-based polling and queue consumption. Precise per-post scheduling UI is deferred.
- **Multi-user / multi-account**: Stays single-user local-first. Multi-tenant support would be a separate product.
- **Real-time webhook listeners**: v1 uses polling. Webhook endpoints for GitHub/GitLab integration are deferred.
- **Mobile notifications**: Desktop WebUI only. Push notifications for publish failures are deferred.
- **Advanced analytics / ML prediction**: Score decay curves, optimal publish time prediction, content optimization suggestions are post-v2.
- **Public REST API**: v1 exposes no external API. Internal API (`/api/*`) stays as-is.

### Outside this product's identity

- **SaaS platform**: This is explicitly a local-first tool for a single operator. Cloud deployment, user accounts, billing, team collaboration are not part of this product.
- **Full-featured SEO tool suite**: We build backlink automation and health tracking, not keyword research, rank tracking, or site audit. Adjacent SEO tooling is complementary but out of scope.
- **Outreach CRM**: Comment outreach, email outreach, guest post pitching are adjacent workflows that this tool does not handle.

## Key Decisions

- **KD1. Incremental extension over new engine**: The wizard, watch service, score store, and dashboard all extend existing WebUI infrastructure instead of building a new automation engine. Rationale: maximum code reuse, lowest risk, fastest time to value.
- **KD2. Polling-based event detection**: New URLs are detected by polling seed sources on a configurable interval, not via webhook. Rationale: local-first architecture has no server to receive webhooks; polling is simpler and sufficient for the use case.
- **KD3. Queue-mediated publish**: The watch service never executes publishes directly — it enqueues tasks for the existing queue worker. Rationale: reuses existing rate-limit handling, retry logic, backoff, and channel status management.
- **KD4. Score as new store or events.db extension**: Scoring data lives either in a new `score_store` JSON file or as new event types in `events.db`. Final choice deferred to planning based on query needs. Rationale: avoids premature storage architecture decision.
- **KD5. Wave separation**: Wizard + automation (Wave 1) ships before dashboard (Wave 2). Rationale: the automation must produce data before the dashboard has anything to show. Getting the pipeline running is the highest-leverage first step.

## Dependencies / Assumptions

- The existing PipelineAPI, queue_store, APScheduler, and channel binding infrastructure are stable and correct. No major refactoring of existing code is expected.
- `equity-ledger` data is the primary source for coverage gap detection. If equity-ledger has gaps/unavailable data, the watch service falls back to simple "published to this channel before" check via history store.
- Channel binding for all desired platforms is already functional through the existing binding flows. No new auth types are required for v1.
- The events.db projector defects (D1–D3 from publishing-health-dashboard-requirements) are fixed before Wave 2 dashboard work, since dashboard queries depend on correct event data.

## Outstanding Questions

### Resolve Before Planning

- **Q1. [Affects R5]** What is the default polling interval for seed sources? (Suggested: 6 hours. Too frequent = wasted polls on unchanged sources. Too infrequent = slow reaction to new targets.)
- **Q2. [Affects R10]** What are the initial platform_weight values for each channel? (Suggested: dofollow=True=1.0, dofollow=False=0.3, uncertain=0.5. Can be user-configurable later.)

### Deferred to Planning

- **Q3. [Technical]** Should the score store be a new JSON file or new event types in events.db? JSON is simpler but less queryable; events.db enables SQL aggregation for the dashboard.
- **Q4. [Technical]** What is the exact data shape for the `seen_urls` store? Simple URL set with hash, or full record including discovered-at, source, coverage state?
- **Q5. [Technical]** How does the watch service integrate with `plan-gap` CLI? Should it invoke `plan-gap` as a subprocess or share the gap engine directly?
