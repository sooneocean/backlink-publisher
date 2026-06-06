# GEO Probe Runbook

_Plan 2026-05-29-006 U9 — AI Citation Share (GEO) operational guide._

---

## What this panel measures

The **AI Citation Share** panel shows, per owned target, how often an LLM (Perplexity
or similar) cites that target in responses to representative queries.

**Share = cited / (cited + absent)**

- `cited` — verdict is `site_cited` or `article_cited`.
- `absent` — verdict is `absent`.
- `refused` is **excluded from the denominator** (counting refusals toward absent
  would structurally zero targets on topics where the engine declines to answer).

Refusal rate and `possibly_cited_unresolved` rate are shown separately as auxiliary
signals.

---

## How to run a probe

```bash
# Dry-run: verify config, print planned queries, make zero network calls.
geo-probe --target https://your-site.com --dry-run

# Live probe: requires a configured GEO engine API key.
geo-probe --target https://your-site.com

# Probe all configured targets.
geo-probe
```

Events are written to `events.db` as `citation.observed` rows and appear in the
`/ce:health` dashboard on the next page load.

---

## Pre-flight: refusal-spike check

Before treating low share as meaningful, check the refused rate:

- **refused_rate > 0.5** → the engine is declining more than half the queries.
  This is a topic-sensitivity signal, not an absence-of-citation signal.
  Do **not** draw conclusions about share until the refusal rate normalises.

- **refused_rate < 0.2** → safe to interpret share directionally.

The panel shows refused rate per target. If it spikes, investigate query wording
before acting on any share number.

---

## D12 go / no-go decision

After the first probe run:

1. Count targets whose `state == "measured"` (≥ 5 denominator probes).
2. If **zero eligible targets** reach measured state after 2 probe rounds, the GEO
   measurement program has insufficient signal for this portfolio. Park measurement
   and ship only the deterministic content levers (freshness, entity clarity).
3. If **≥ 1 target** reaches measured state, continue.

---

## Reading the dashboard without fooling yourself (A8)

### Minimum movement before acting

A share change is noise unless:

- `n ≥ 10` (full-confidence threshold), **and**
- The absolute change is ≥ 0.10 (10 percentage points) between two windows, **and**
- The direction holds across at least 2 consecutive measurement windows.

A single-window jump below these thresholds is sampling variance, not a signal.

### Low-confidence badge

When `n ≥ 5` but `n < 10` the panel shows a **low confidence** badge. The share
number is computed correctly but has wide uncertainty; do not act on it alone.

### States — what they mean

| State | Meaning |
|---|---|
| `measured` | ≥ 5 denominator probes; share is a real estimate. |
| `warming_up` (N probes) | Fewer than 5 usable probes; share shown as `—`. Never 0%. |
| `Not yet probed` | No `citation.observed` events for this target yet. |
| `Excluded from measurement` | Target is explicitly excluded (e.g. high refusal rate). |

**Never interpret a `—` as 0%.** It means there is insufficient data to estimate.

### Correlation ≠ causation

The panel is **advisory**. A share improvement after publishing more content on a
topic is a correlation. It does not establish that the publication caused the
improvement. Other factors (engine updates, query drift, competitor changes) affect
share independently.

Do not write operator notes, reports, or communications that claim a causal link.

---

## D13 shaped vs. unshaped cohort comparison

When both a shaped cohort (content modified by GEO levers) and an unshaped cohort
exist for the same target, the panel can show a within-target comparison. This is a
quasi-experiment, not an RCT.

**Gate**: both cohorts must clear the `min_sample` (5) and `low_confidence`
(10) thresholds before the comparison is shown. Below that threshold, the comparison
is suppressed to avoid false confidence.

Even above the gate, treat the comparison as a lever signal, not proof.

---

## Known traps

- **events.db WAL lag**: events written by a concurrent probe may not appear
  immediately in the dashboard. Refresh after 5–10 seconds.
- **At-least-once delivery**: if a probe crashes mid-run, re-probing the same
  `(target, query, run_id)` triple does not inflate counts (read-time dedup, D11).
- **Redirect-wrapped source URLs**: some engines wrap citations in redirect URLs.
  These appear in the `possibly_cited_unresolved` auxiliary rate, not in the main
  share. A high unresolved rate means the share may be understated.

---

## Falsifiable baseline

Before making any content changes for GEO, record:

```
Baseline date: <YYYY-MM-DD>
Target: <url>
Share: <N>% (n=<K>)
Refused rate: <R>%
```

A post-change measurement is only meaningful if compared against this baseline
with the same query set and engine configuration.
