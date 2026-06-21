---
title: "feat: Full-Automation Runbook — 全自動化運行規劃方案"
type: feat
status: active
date: 2026-06-07
origin: user-request-2026-06-07
claims:
  paths:
    - src/backlink_publisher/automation/orchestrator.py
    - src/backlink_publisher/automation/recovery.py
    - webui_app/routes/auto_health.py
    - docs/runbooks/2026-06-07-full-automation-operations.md
---

# feat: Full-Automation Runbook — 全自動化運行規劃方案

## Overview

Design a complete automated operation framework for the backlink-publisher service that enables unattended, stable execution with self-healing capabilities. The plan builds on existing automation primitives (`recheck-backlinks`, `canary-targets`, APScheduler jobs) and adds:

1. **Pipeline orchestration layer** — end-to-end workflow automation
2. **Exception monitoring with auto-recovery** — watchdog + remediation loops  
3. **Resource scheduling optimization** — adaptive throttling + rate-limit handling
4. **Health-driven autonomy** — predictive scheduling based on decay signals

The system operates under the principle: **detect but don't auto-decide**, with explicit opt-in thresholds for any auto-quarantine actions.

## Problem Frame

Operators currently manage the publishing pipeline through manual WebUI clicks or external cron invocations. The system lacks:
- A unified orchestrator that chains planning → validation → publishing
- Automated recovery when channels expire or backlinks decay
- Adaptive scheduling that responds to platform health signals
- Centralized observability with auto-remediation triggers

While `recheck-backlinks` and `canary-targets` provide detection, they require manual intervention. This plan introduces the automation glue layer.

## Requirements Trace

| ID | Requirement |
|----|-------------|
| R1 | Unified CLI orchestrator (`auto-publish`) that chains the full pipeline with configurable stages |
| R2 | Watchdog service that monitors `canary-health.json` and triggers recovery workflows |
| R3 | Adaptive scheduler that adjusts cadence based on decay-rate signals |
| R4 | Auto-recovery mechanisms for expired channels (re-bind prompts) and dead backlinks (re-publish) |
| R5 | Centralized health dashboard (`/auto-health`) exposing all automation metrics |
| R6 | Alert escalation with opt-in hard-skip quarantine for degraded platforms |
| R7 | Resource budgeting — CPU/memory/throttle aware scheduling with graceful degradation |
| R8 | Audit trail for all automated decisions (stdin-JSONL contract preserved) |

## Architecture Foundation

### Existing Primitives (Do Not Change)
- `recheck-backlinks` — survival loop (exit 0 advisory, `--probe` for network)
- `canary-targets` — contract drift detection (per-platform health store)
- `gate-probe` — Phase-0 falsification gates (GO/KILL/INCONCLUSIVE/BLOCKED)
- `scheduler.py` — APScheduler jobs (queue processor, watch service)
- `canary_health_store` — persistent per-platform state with debounce/re-arm
- `events.db` — time-series for `link.rechecked` events

### New Layer: Automation Orchestrator

```
┌─────────────────────────────────────────────────────────────────┐
│                    FULL AUTOMATION STACK                           │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐         │
│  │  Scheduler   │───▶│ Orchestrator │───▶│  Publisher   │         │
│  │ (APScheduler)│    │              │    │ (registry)   │         │
│  └──────────────┘    └──────────────┘    └──────────────┘         │
│         │                      │                   │              │
│         ▼                      ▼                   ▼              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐         │
│  │   Watchdog   │───▶│   Recovery   │───▶│  Threshold   │         │
│  │ (canary sync)│    │ (auto-heal)  │    │ (opt-in)     │         │
│  └──────────────┘    └──────────────┘    └──────────────┘         │
│         │                      │                   │              │
│         ▼                      ▼                   ▼              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │           /auto-health Dashboard + Alert Surface             ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Implementation Units

### Unit 1: Automation Orchestrator (`auto-publish` CLI)

**Goal:** A single CLI entry point that chains the full pipeline with health-aware gating.

**Files:**
- Create: `src/backlink_publisher/automation/orchestrator.py`
- Create: `src/backlink_publisher/automation/_state.py` (pipeline state tracking)
- Modify: `pyproject.toml` (add `auto-publish` script entry)

**Behavior:**
```bash
# Dry-run preview (always exit 0, zero network by default)
auto-publish --dry-run

# Full automation (respects canary health, optional force)
auto-publish --force --publish

# Auto-retry on transient failure
auto-publish --retry-on-error --max-retries 3
```

**Key Decisions:**
- Reads `canary_health_store` before publishing; degraded platforms only publish if `--force`
- Never fails silently — all actions emit JSONL to stdout, recon to stderr
- Uses flock for cross-process safety (same pattern as `recheck-backlinks`)
- Exit codes: 0 (success), 3 (dependency), 6 (hard-skip triggered), 8 (config error)

### Unit 2: Watchdog Service

**Goal:** Monitor canary health and derive automation actions.

**Files:**
- Create: `src/backlink_publisher/automation/watchdog.py`
- Create: `src/backlink_publisher/automation/signals.py` (health signal types)

**Signals Monitored:**
- `canary_status.drift-confirmed` (≥2 consecutive) → quarantine trigger
- `channel_status.expired` → re-bind recommendation
- `recheck.verdict.host_gone` → re-publish target
- `recheck.verdict.link_stripped` → platform hard-skip candidate

**Debouncing (matching existing pattern):**
- `QUARANTINE_AFTER_N = 2` (consecutive confirmed drifts)
- `REARM_AFTER_M = 2` (consecutive OKs to clear quarantine)
- `COOLDOWN_HOURS = 24` (prevent alert flooding)

### Unit 3: Auto-Recovery Workflows

**Goal:** Self-healing actions triggered by watchdog signals.

**Recovery Types:**
1. **Channel Re-bind** — When `channel_status.expired`:
   - Emits `bind_required` event to `events.db`
   - Creates rebind task in `queue-store`
   - On `--interactive` mode: emails operator; on `--auto`: attempts rebind via stored token

2. **Target Re-publish** — When `recheck.verdict` indicates dead:
   - Queries `equity-ledger` for under-linked targets
   - Runs `plan-gap` to fan out deficit publishing
   - Chains through orchestrator with degraded-platform exclusion

3. **Platform Quarantine Lift** — When platform recovers:
   - Auto-lift after `REARM_AFTER_M` consecutive `link-alive`
   - Flap detection: alert on rapid quarantine ↔ recovery cycles

**Files:**
- Create: `src/backlink_publisher/automation/recovery.py`
- Modify: `webui_store/queue.py` (add automation task types)

### Unit 4: Adaptive Scheduler

**Goal:** Dynamic schedule adjustment based on health signals.

**Inputs:**
- Decay rate from `recheck-backlinks` full-sweep
- Platform reliability scores from `channel-scorecard`
- Resource utilization (CPU/memory/throttle bucket)

**Outputs:**
- Adjusted publish cadence per channel
- Throttle band adjustments (`MEDIUM_THROTTLE_MIN/MAX` pattern)
- Queue prioritization for high-risk backlinks

**Implementation:**
- Extend `scheduler.py` with health-aware job factory
- New job type: `auto-publish` with dynamic interval
- Backoff on persistent failures (exponential, capped)

### Unit 5: `/auto-health` Dashboard

**Goal:** Single-pane observability for all automation metrics.

**Route:** `GET /auto-health` (read-only, no CSRF)

**Panels:**
1. **Pipeline Throughput** — publishes/day, success rate, error distribution
2. **Canary Health** — per-platform status, drift trends, quarantine state
3. **Recovery Queue** — pending rebinds, pending republishes, completed recoveries
4. **Resource Budget** — throttle usage, API quota, batch budgets
5. **Alert History** — recent hard-skip triggers, flap warnings

**Files:**
- Create: `webui_app/routes/auto_health.py`
- Create: `webui_app/auto_metrics.py` (read-only aggregation)
- Create: `webui_app/templates/auto_health.html`

### Unit 6: Alerting & Notification

**Goal:** Structured alerting that feeds into automation without false positives.

**Alert Channels:**
- Webhook (configurable URL for external integration)
- File-based (`alert.log` in config dir, 0600)
- Telegram (optional, via bot token)

**Alert Types:**
```json
{
  "alert_type": "platform_quarantined",
  "platform": "medium",
  "severity": "warning",
  "reason": "drift-confirmed",
  "consecutive": 2,
  "ts_utc": "2026-06-07T10:30:00Z",
  "action_taken": "removed_from_payload",
  "recovery_suggested": true
}
```

**Threshold Configuration (`[automation.thresholds]`):**
```toml
[automation.thresholds]
quarantine_consecutive = 2
alert_cooldown_hours = 24
flap_window_days = 7
flap_threshold = 3
```

## Safety & Guardrails

### 1. Dry-Run First Pattern
Every automation action defaults to dry-run mode. Network requires explicit flag.

### 2. Opt-In Quarantine
Only platforms with `hard_skip = true` in `[canary.<platform>]` are ever hard-skipped. Default is advisory-only.

### 3. Credential Isolation
- Probe UAs isolated from publish UAs (per `recheck-backlinks` precedent)
- Bind credentials never in alert payloads
- Re-bind attempts require explicit `allow_auto_rebind = true` gate

### 4. Circuit Breaker
- Rate-limit detection → exponential backoff
- Anti-bot saturation → skip run, log warning
- Persistent errors → quarantine + alert

## Operational Parameters

### Detectability vs. Responsiveness

| Metric | Default | Rationale |
|--------|---------|-----------|
| Max detection latency | ≤48h | Operator-tunable via cadence × debounce |
| Initial cadence | Daily | Weekly for canary-targets, daily for recheck |
| Decay probe window | 14 days | N days in recheck selection |
| Per-run cap | 50 probes | `recheck-backlinks` default M |
| Throttle band | 60-300s | Medium inter-post delay |

### Coverage Invariant

For corpus size C, age threshold N days, per-run cap M, cron period P days:

```
M × (N / P) ≥ C
```

Defaults cover ~700 placements per 14-day window. Scale M or P when exceeding.

## Execution Phases

### Phase 1: Orchestrator Foundation
- U1: `auto-publish` CLI with staged execution
- U2: Pipeline state + checkpoint integration
- U3: Basic health gating (read canary store)

### Phase 2: Watch & Recovery
- U4: Watchdog service with signal detection
- U5: Recovery workflows (rebind, republish, quarantine lift)
- U6: Alert emission + logging

### Phase 3: Adaptive & Observable
- U7: Adaptive scheduler with dynamic intervals
- U8: `/auto-health` dashboard
- U9: Documentation + runbook

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Auto-retry causes rate-limit loops | Medium | High | Exponential backoff + max-retries cap + throttle band awareness |
| Quarantine flip destabilizes publish | Low | High | Opt-in only (`hard_skip=true`), debounce, re-arm period |
| Recovery creates duplicate publishes | Medium | Med | Idempotent dedup via events.db + cursor tracking |
| Alert fatigue from flapping platforms | High | Med | Flap detection + cooldown + flap alert tier |
| SSRF in recovery probe path | Low | High | Full credential guard chain reuse (D9 pattern) |

## Verification Points

1. `auto-publish --dry-run` never touches network (test enforces)
2. Degraded platform excluded without `--force`
3. Canary drift triggers quarantine after N=2 consecutive
4. Recovery queue processes without duplicate events
5. `/auto-health` renders correctly under partial DB failure (never 500)
6. Alert payloads contain no secrets (grep test)

## Sources & References

- `docs/runbooks/recheck-backlinks-runbook.md` — baseline probe pattern
- `docs/runbooks/2026-05-27-canary-targets-operations.md` — canary workflow
- `docs/plans/2026-05-25-006-feat-publishing-health-dashboard-plan.md` — dashboard pattern
- `webui_app/scheduler.py` — existing APScheduler integration
- `src/backlink_publisher/canary/store.py` — debounce/re-arm logic