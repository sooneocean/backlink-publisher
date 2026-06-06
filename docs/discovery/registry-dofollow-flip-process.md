---
date: 2026-06-06
kind: process-doc
builds_on: docs/brainstorms/2026-06-06-dofollow-channel-expansion-requirements.md
status: active
---

# Registry Dofollow Status — Flip Process (R4)

When a `dofollow="uncertain"` platform is confirmed as dofollow (or nofollow)
by an OUR-pipeline canary, update the registry accordingly.

## Confirmed Dofollow

### Step 1: Flip the flag

In `src/backlink_publisher/publishing/adapters/__init__.py`, locate the
`register()` call for the platform and change:

```python
# Before: uncertain
register("platform", PlatformAdapter, dofollow="uncertain",
         rationale="...≥80 chars...")

# After: dofollow
register("platform", PlatformAdapter, dofollow=True)
```

- Change `dofollow="uncertain"` to `dofollow=True`
- Remove the `rationale=` argument entirely
- Remove any `referral_value=` argument if present (it is required only when
  `dofollow` is not `True`)

The `dofollow=True` keyword is a **required** positional-adjacent argument
(enforced by `tests/test_adapter_dofollow_gate.py`). It must always be present.

### Step 2: Add canary config

Create a `[canary.<platform>]` entry in `~/.config/backlink-publisher/config.toml`
(or the relevant config file) so the platform is included in ongoing evergreen
drift detection:

```toml
[canary.<platform>]
post_url = "https://<platform>/.../canary-post"
expected_target = "<your-target-domain>/..."
marker = "canary-marker-text"
```

Without this entry, `canary-targets` will report the platform as
`not-configured` — a loud coverage gap but not a functional error.

### Step 3: Update tracking artifacts

- In `docs/discovery/canary-pending.md`: change the platform's status from
  `pending` to `flipped`
- In `docs/discovery/canary-blitz-verdicts.md`: fill in the verdict row with
  date, published URL, rel observed, and status = dofollow
- Run `canary-targets --platform <name>` to confirm the platform appears in
  the dofollow cohort

### Step 4: Commit

```bash
git add src/backlink_publisher/publishing/adapters/__init__.py
# Also any manifest files if the platform's dofollow field is mirrored there
git commit -m "feat: flip <platform> dofollow uncertain→True after canary"
```

## Confirmed Nofollow

### Step 1: Leave or flip to False

```python
# Option A: Leave as "uncertain" (if evidence is weak)
register("platform", PlatformAdapter, dofollow="uncertain",
         rationale="...≥80 chars...")

# Option B: Flip to False (if evidence is definitive)
register("platform", PlatformAdapter, dofollow=False,
         rationale="Canary confirmed nofollow on ... evidence: ...")
```

If flipping to `False`, the `rationale=` argument is still required (enforced
by `tests/test_adapter_dofollow_gate.py` for any non-`True` dofollow status).

No `[canary.<platform>]` config entry is needed for nofollow platforms.

### Step 2: Update tracking artifacts

- In `docs/discovery/canary-pending.md`: change to `retired`
- In `docs/discovery/canary-blitz-verdicts.md`: record the nofollow result

## Inconclusive Result

If the canary could not confirm dofollow or nofollow:

1. Document the reason in `docs/discovery/canary-blitz-verdicts.md` notes
2. Leave the platform as `dofollow="uncertain"`
3. Consider whether to:
   - Retry with a different post or account
   - Use a browser-based fetch instead of HTTP fetch (for client-side rendered pages)
   - Retire if the platform is unreliable (paywalls, constant layout changes)

## What consumers pick up

Downstream systems read the registry dynamically at runtime:

| Consumer | Reads | Impact of flip |
|---|---|---|
| `plan-gap` | `dofollow_status() is True` | Newly confirmed platforms become targets for deficit-driven replan |
| `canary-targets` | `_build_cohort()` | Newly confirmed platforms join the evergreen drift-detection cohort |
| `channel-scorecard` | `dofollow_status()` | Platform appears in the dofollow-signal vector |
| WebUI `/ce:health` | registry metadata | Health cards reflect the updated status |

No code changes are needed in any consumer — they read the registry at runtime.
