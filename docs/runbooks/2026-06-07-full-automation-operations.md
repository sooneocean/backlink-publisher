# Full-Automation Operations Runbook — 本機全自動外鏈服務

## Overview

The automation subsystem turns the existing planning, validation, publishing,
recheck, canary, gap, and WebUI surfaces into a local-first service loop.
Default operation is deterministic and safe: no live publish and no live probe
happen unless the operator passes `--probe` or explicitly runs a probe command.

Claim boundary: draft, dry-run, and unverified HTTP 200 are not live dofollow
success. Treat them as planning or preview evidence only.

## CLI Entry Points

### `auto-publish`

Unified orchestrator that chains plan → validate → publish with health gating.

```bash
# Dry preview (default; zero-network preview path)
auto-publish < seeds.jsonl

# Network-enabled run; requires explicit operator approval
auto-publish --probe < seeds.jsonl

# Force override for degraded/quarantined platform checks; operator-only
auto-publish --probe --force < seeds.jsonl

# Auto-retry on transient failures
auto-publish --probe --retry-on-error --max-retries 3 < seeds.jsonl
```

**Exit Codes:**
- `0` — Success
- `3` — Dependency error (platform unavailable)
- `6` — Hard-skip triggered (opt-in platform quarantined)
- `8` — Config error

### `quality-gate`

Pre-publish quality gate. It reads JSONL on stdin and emits only passing rows on
stdout. Blocked rows are diagnostics on stderr; `--emit-events` records
`publish.quality_blocked` in events.db.

```bash
cat planned.jsonl | quality-gate > passing.jsonl
cat planned.jsonl | quality-gate --emit-events > passing.jsonl
```

### `replan-dead`

Dead-link replan helper. It reads deterministic `link.rechecked` events from
events.db and emits `plan-backlinks` seed JSONL for `host_gone` and
`link_stripped`.

```bash
replan-dead --days 30 --min-gap 3 > replan-seeds.jsonl
replan-dead --days 30 --emit-stderr > replan-seeds.jsonl
```

### Watchdog module

There is no installed `auto-publish-watchdog` console script in this repo. Run
the watchdog module through Python when you need one local cycle.

```bash
python3 - <<'PY'
from backlink_publisher.automation.watchdog import run_watch_cycle
raise SystemExit(0 if run_watch_cycle() >= 0 else 1)
PY
```

stdout is health-signal JSONL. RECON diagnostics are allowed on stderr.

## Watchdog Monitoring

The watchdog monitors:

1. **Canary drift signals** — `canary_status.drift-confirmed` triggered when a platform
   reaches `QUARANTINE_AFTER_N` (default: 2) consecutive confirmed drifts.

2. **Channel expiration** — `channel_status.expired` when bind-required credentials
   are detected missing or invalid.

## Recovery Workflows

### Channel Rebind

When a channel expires:
- Emits a `channel_status.expired` health signal
- Creates a rebind task in queue-store when recovery handles the signal
- Visible in `/auto-health` recovery panel

### Target Republish

When backlinks are detected dead:
- `recheck-backlinks --probe` emits `link.rechecked` events
- `replan-dead` emits follow-up seed JSONL for deterministic dead links
- `auto-publish` can preview or process those seeds, respecting health gates

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

Start the WebUI:

```bash
python3 webui.py
```

Open `http://127.0.0.1:8888/auto-health`.

The `/auto-health` dashboard provides:

1. **Pipeline Throughput** — publishes/day, success rate, error distribution
2. **Canary Health** — per-platform status, drift trends, quarantine state
3. **Recovery Queue** — pending rebinds, pending republishes
4. **Resource Budget** — throttle settings, batch caps
5. **Recent Alerts** — hard-skip advisories, recovery actions

## Safety & Guardrails

### Dry-Run First Pattern

`auto-publish` defaults to dry preview. Network requires explicit `--probe`.
`recheck-backlinks` also defaults to zero-network preview; live checking requires
`recheck-backlinks --probe`.

### Opt-In Quarantine

Only platforms that are both quarantined and configured with `hard_skip = true`
in `[canary.<platform>]` are hard-skipped. `hard_skip = true` alone is not a
block; it is the operator's opt-in that turns a confirmed quarantine into a
publish stop.

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

## Local Service Loop

Recommended deterministic loop before any live write:

```bash
# 1. Generate or collect seeds.
cat seeds.jsonl | plan-backlinks | validate-backlinks | quality-gate > passing.jsonl

# 2. Preview automation without network.
auto-publish < passing.jsonl > auto-preview.jsonl

# 3. Inspect control plane.
python3 webui.py
# open http://127.0.0.1:8888/auto-health

# 4. Dry-preview decay candidates.
recheck-backlinks
replan-dead --days 30 --min-gap 3 > replan-seeds.jsonl
```

Live publish path, requiring explicit operator approval:

```bash
auto-publish --probe < passing.jsonl
```

Live recheck path, requiring explicit operator approval:

```bash
recheck-backlinks --probe
```

## Troubleshooting

### Q: What does "hard-skip triggered" mean?

A: A platform with `hard_skip=true` in its canary config has been quarantined.
Check `[canary.<platform>]` in config.toml and review the canary runbook.

### Q: `hard_skip=true` exists but auto-publish still previews the platform

A: That is correct. `hard_skip=true` is only an opt-in policy. The platform must
also be quarantined by canary evidence before auto-publish blocks it.

### Q: Channel shows expired in `/auto-health`

A: Credentials need rebinding. Visit `/settings` and click "重新绑定" for that channel.

### Q: Recovery tasks not being processed

A: Check that the scheduler is running and the queue processor job is active.
The WebUI scheduler shows as "System Active" in the top-right corner.

### Q: `/auto-health` says data is incomplete

A: Treat the dashboard as degraded observability, not as proof of service
failure. Run focused checks:

```bash
python3 -m pytest tests/test_auto_health_dashboard.py -q
python3 -m pytest tests/test_automation_watchdog.py -q
```
