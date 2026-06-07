# Full-Automation Operations Runbook — 全自動化運行規劃

## Overview

The automation subsystem provides end-to-end pipeline orchestration with health-aware
gating, auto-recovery, and observability. This runbook covers operational procedures.

## CLI Entry Points

### `auto-publish`

Unified orchestrator that chains plan → validate → publish with health gating.

```bash
# Dry-run preview (never touches network)
auto-publish --dry-run < seeds.jsonl

# Full automation with network
auto-publish --probe < seeds.jsonl

# Force publish for degraded platforms
auto-publish --probe --force < seeds.jsonl

# Auto-retry on transient failures
auto-publish --probe --retry-on-error --max-retries 3 < seeds.jsonl
```

**Exit Codes:**
- `0` — Success
- `3` — Dependency error (platform unavailable)
- `6` — Hard-skip triggered (opt-in platform quarantined)
- `8` — Config error

### `auto-publish-watchdog`

Runs the watchdog service once, emitting health signals for threshold crossings.

```bash
# Run watchdog once
auto-publish-watchdog

# Check canary health
auto-publish-watchdog 2>/dev/null | jq 'select(.signal_type == "canary_status.drift-confirmed")'
```

## Watchdog Monitoring

The watchdog monitors:

1. **Canary drift signals** — `canary_status.drift-confirmed` triggered when a platform
   reaches `QUARANTINE_AFTER_N` (default: 2) consecutive confirmed drifts.

2. **Channel expiration** — `channel_status.expired` when bind-required credentials
   are detected missing or invalid.

## Recovery Workflows

### Channel Rebind

When a channel expires:
- Emits `bind_required` event to `events.db`
- Creates rebind task in queue-store
- Visible in `/auto-health` recovery panel

### Target Republish

When backlinks are detected dead:
- Queries `equity-ledger` for under-linked targets
- Runs `plan-gap` to fan out deficit publishing
- Chains through orchestrator with degraded-platform exclusion

### Platform Quarantine Lift

When a platform recovers:
- Auto-lift after `REARM_AFTER_M` (default: 2) consecutive `link-alive`
- Flap detection alerts on rapid cycles

## Alerting

Alert configuration in `config.toml`:

```toml
[automation.thresholds]
quarantine_consecutive = 2
alert_cooldown_hours = 24
flap_window_days = 7
flap_threshold = 3
```

Alerts are emitted when:
- Platform enters quarantine (drift-confirmed × N)
- Channel expires (bind-required)
- Recovery action taken

## Dashboard Access

The `/auto-health` dashboard provides:

1. **Pipeline Throughput** — publishes/day, success rate, error distribution
2. **Canary Health** — per-platform status, drift trends, quarantine state
3. **Recovery Queue** — pending rebinds, pending republishes
4. **Resource Budget** — throttle settings, batch caps
5. **Recent Alerts** — hard-skip advisories, recovery actions

## Safety & Guardrails

### Dry-Run First Pattern

Every automation action defaults to dry-run. Network requires explicit `--probe`.

### Opt-In Quarantine

Only platforms with `hard_skip = true` in `[canary.<platform>]` are ever hard-skipped.
Default is advisory-only.

### Circuit Breaker

- Rate-limit detection → exponential backoff
- Anti-bot saturation → skip run, log warning
- Persistent errors → quarantine + alert

## Operational Parameters

| Metric | Default | Rationale |
|--------|---------|-----------|
| Max detection latency | ≤48h | Configurable via cadence × debounce |
| Initial cadence | Daily | Weekly for canary-targets |
| Per-run cap | 50 probes | `recheck-backlinks` default M |
| Throttle band | 60-300s | Medium inter-post delay |

## Troubleshooting

### Q: What does "hard-skip triggered" mean?

A: A platform with `hard_skip=true` in its canary config has been quarantined.
Check `[canary.<platform>]` in config.toml and review the canary runbook.

### Q: Channel shows expired in `/auto-health`

A: Credentials need rebinding. Visit `/settings` and click "重新绑定" for that channel.

### Q: Recovery tasks not being processed

A: Check that the scheduler is running and the queue processor job is active.
The WebUI scheduler shows as "System Active" in the top-right corner.