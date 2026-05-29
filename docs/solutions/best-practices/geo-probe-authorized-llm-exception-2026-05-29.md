---
title: "Authorized no-runtime-LLM exception: the GEO citation probe (operator-invoked, no required credential)"
date: 2026-05-29
category: docs/solutions/best-practices
module: geo (citation probe) / project-policy
problem_type: best_practice
component: project_policy
severity: high
applies_when:
  - "Reviewing the GEO citation-probe seam (`src/backlink_publisher/geo/`, `scripts/geo_refusal_spike.py`) against the no-runtime-LLM hard constraint"
  - "Triaging a brainstorm/plan that wants to add or extend AI-answer-engine probing"
  - "Evaluating whether the GEO probe provider (`[geo.probe_provider]`) may be promoted onto a default/cron path"
related_components:
  - tooling
  - project_policy
tags:
  - project-policy
  - llm-free
  - runtime-constraint
  - authorized-exception
  - geo-probe
  - operator-verb
---

# Authorized no-runtime-LLM exception: the GEO citation probe

## Guidance

`no-runtime-llm-2026-05-15.md` is a **P0 hard constraint**: no LLM/AI-engine
call may sit on any shipped publish / plan / validate / generation / cron path,
and the product must remain installable and runnable with **no** AI credential.

The GEO citation probe (Plan `docs/plans/2026-05-29-006-feat-geo-ai-citation-closed-loop-plan.md`)
calls an external AI answer engine (Perplexity, OpenAI-compatible) to ask
*"does this engine cite my target site when answering about its topic?"*. The
owner has **authorized this as an opt-in, operator-invoked, development/operator-
time tool** under the exact guardrails of the no-runtime-LLM doc. Do **not**
re-flag the GEO probe seam as a P0 no-runtime-LLM violation.

## When to Apply

- **Code review of the GEO seam** — `src/backlink_publisher/geo/engines.py`,
  `src/backlink_publisher/geo/perplexity.py`, and the U4 pre-flight script
  `scripts/geo_refusal_spike.py` are the *only* code permitted to issue an
  AI-engine call. They are an opt-in side path, never load-bearing for any
  shipped pipeline feature.
- **Plan / brainstorm triage** — extensions that keep the probe operator-invoked
  and human-reviewed (a new engine adapter, a richer credit gate) stay inside
  this exception. Anything that wires the probe onto a default/cron/`--replay`
  path, or that feeds probe output back into content auto-generation without a
  human in the loop, falls **back outside** the exception and is a P0 blocker.
- **Config review** — `[geo.probe_provider]` and `BACKLINK_GEO_API_KEY` must
  stay optional. Absent config → the verb errors clearly (exit 2) and the rest
  of the tool is unaffected.

## Why This Qualifies (the guardrails, all satisfied)

The probe preserves every shipped-product invariant the constraint protects:

- **Operator-invoked verb, never default-wired.** The probe is reached only
  through an explicit operator command and the U4 pre-flight
  (`scripts/geo_refusal_spike.py`). It is **never** imported by publish / plan /
  validate, and **never** placed on a cron / `--replay` / automated default
  path. Automated probing stays default-OFF (cf.
  `medium-liveness-probe-partial-spike-2-2026-05-19.md`).
- **Product runnable with NO GEO credential.** GEO probing is optional:
  `geo_probe_provider` is `None` when `[geo.probe_provider]` is absent, and there
  is **no** fallback to the LLM anchor key (`BACKLINK_LLM_API_KEY`) — a populated
  LLM key alone never enables GEO probing (D0). The key is never a required env
  var; absent config → clear error, the rest of the tool runs fine.
- **Content levers stay deterministic — no runtime LLM in the content path.**
  The "lever" half of the GEO feature (Phase C) shapes content with
  **deterministic** templating / config-driven synthesis. No runtime AI call
  touches body generation, so cost-determinism, reproducibility (`--replay`,
  property tests), and footprint-resistance are intact.
- **Output is a human-reviewed artifact.** The probe prints a Markdown table +
  per-target verdict for a human to read; nothing it returns is auto-published.
  A human curates before anything reaches a publish path.
- **Reuses the existing opt-in primitives + guard chain.** The adapter reuses
  `generate-backlink-text`'s credential guard chain (userinfo reject → normalize
  base → `guard_llm_endpoint`) and the `_util/llm_allowlist.py` allowlist; it
  does not make the AI engine load-bearing for any shipped pipeline feature.
- **Secrets never persisted.** `ProbeResult.raw_response` is in-memory only (D8)
  — upstream error bodies can echo `Authorization`/`Bearer`, so only bounded
  parsed fields are ever appended downstream.

## Why The Value Gate Comes First (D12)

The U4 refusal-spike is a **feature-level go/no-go** run *before* building the
measurement half. It measures **citation** refusal (not generation refusal —
review A2): a target is excluded when its refused-rate is high **OR** its
cited-rate among answered probes is ~0 (answering-but-never-citing is equally
worthless). If the GEO-eligible target set is empty, the verdict recommends
shipping **Phase C (deterministic levers) only** and **deferring Phase B
(measurement)** — so the LLM-touching half is built only when it would actually
measure something (cf. `grep-dofollow-map-before-shipping-adapter-2026-05-20.md`).

## Authorized Exception (record)

- **2026-05-29 — GEO citation probe** (plan:
  `docs/plans/2026-05-29-006-feat-geo-ai-citation-closed-loop-plan.md`; policy:
  `docs/solutions/best-practices/no-runtime-llm-2026-05-15.md`). Owner authorized
  as an opt-in, operator-invoked, human-reviewed pre-flight/measurement tool,
  decoupled from the shipped pipeline, runnable with no GEO credential, never
  default-wired to cron. Do not re-flag the GEO probe seam as a P0
  no-runtime-LLM violation.

## Related Issues

- `no-runtime-llm-2026-05-15.md` — the P0 hard constraint and its
  "Authorized Exceptions" section (the `generate-backlink-text` precedent
  authorized 2026-05-27 under identical guardrails).
- `medium-liveness-probe-partial-spike-2-2026-05-19.md` — automated probes
  default-OFF; isolate probe state; windowed rate budgets.
- `grep-dofollow-map-before-shipping-adapter-2026-05-20.md` — validate an engine
  is *worth* wiring before building it (U4 is the GEO value gate, D12).
