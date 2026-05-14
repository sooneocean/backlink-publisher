---
date: 2026-05-14
topic: round3-fresh-pass
focus: open-ended (third pass; explicit exclusion of Round-1 shipped + Round-2 survivors/rejections)
---

# Ideation: Backlink-Publisher — Round 3 Fresh Pass (2026-05-14)

Third round of open-ended ideation. Builds on `2026-05-12-backlink-publisher-ideation.md` (Round 1, 5 shipped survivors) and `2026-05-14-raise-the-bar-ideation.md` (Round 2, 7 survivors w/ #1 killed and #5 in brainstorm). This round explicitly excludes **all 12 prior shipped/in-flight/survivor ideas and all 22 documented rejections** and pushes for fresher angles.

## Codebase Context

### Project shape
- Python 3.11+ CLI: `plan-backlinks → validate-backlinks → publish-backlinks → report-anchors` (Unix-pipe, JSONL).
- Adapters: Blogger API + Medium API + Medium browser fallback (Playwright/Brave).
- `webui.py` is a **4,510-line** Flask monolith at repo root (grew from 1,400 in earlier memory note). Strict sibling-page rule.
- TOML config at `~/.config/backlink-publisher/config.toml`; `save_config` has documented silent-data-loss bug (`feedback_config-save-overwrite-pattern.md`).
- 45 test files, autouse HTTP mock fixtures, ~999 tests green on feat branches.
- Two parallel anchor schemas coexist: legacy `anchor_keywords` (`config.py:135`) vs new `branded_pool/partial_pool/exact_pool` (`config.py:101-103`).
- Throttling: Medium 60-300s sleep; OAuth pre-flight; post-publish link-attr verification.

### Past learnings consulted
- `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md` — tautological gate passed 999 example tests. Drives Round-3 #5 (property tests).
- `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md` — mock every fallback branch; mock `time.sleep` in batch tests.
- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — sibling-page pattern is the rule.
- MEMORY: `feedback_no-runtime-llm.md`, `feedback_config-save-overwrite-pattern.md`, `feedback_test-locks-in-bug.md`, `feedback_api-idempotency-lesson.md`, `feedback_floating-point-tiebreak.md`, `feedback_llm-free-pool-sizing.md`, `feedback_standalone-page-vs-retrofit.md`, `feedback_cereview-finds-latent-bugs.md`.

### Frame & guardrails for this round
- **Hard constraints baked into every ideation prompt**: no runtime LLM, no webui.py retrofit, no ToS-violation, solo-operator scale, must be grounded in real codebase modules.
- **Four divergent frames**, deliberately shifted from Round 2's coverage:
  - A: extreme cron-safe / fully unattended operation
  - B: operator mental-model mismatch
  - C: 30-day feature-freeze — mine hidden value from existing primitives
  - D: external expert audit (Google trust analyst / YC infra eng / security auditor / Pythonista)
- **Synthesis combos** by orchestrator after dedup: S1 Unattended Cockpit, S2 Authority Truth Layer, S3 Production-Hardening Bundle, S5 Run Reproducibility Toolkit.
- **Adversarial filter**: explicit LLM-free check (Round-2 missed this for the killed RAG idea), explicit "subsumed by Round-2 survivor" check, explicit "shell-script in disguise" check, explicit footprint-emission check.

## Ranked Ideas

### 1. Config Safety Net (snapshot history + atomic write + section quarantine)

**Description:** Wrap `save_config` (`config.py:798`) with: (a) compute CRC of all known + unknown TOML sections pre-mutation, (b) atomic write via `config.toml.new` + fsync + rename, (c) before writing, snapshot the current file to `.config-history/YYYYMMDD-HHMMSS.toml` (rolling N=20), (d) on read, verify unknown-section CRC against the last-known-good snapshot; refuse to start and emit a structured recovery JSONL line on mismatch. Add `bp config diff` for time-travel inspection of any historical snapshot.

**Rationale:** Directly fixes the `feedback_config-save-overwrite-pattern.md` incident class — `[targets]` section silently eaten by full-file rewrites. This is the **only candidate with a documented incident class as motivation** (vs. speculative hardening). Atomic write + history is ~50 LOC; CRC-based quarantine is the meaty part. Avoids the rejected Round-2 idea "Pydantic config rewrite" because it sidesteps the schema rather than re-modeling it.

**Downsides:** `tomli-w` doesn't preserve comments/key-order between roundtrips — defining "unknown section" needs care. `.config-history/` grows unbounded without rotation policy (one config knob away). CRC of TOML-parsed-then-re-serialized may produce false alarms if the writer's formatting changes between releases — must hash the source bytes of unknown sections, not the re-serialized form.

**Confidence:** 92%
**Complexity:** Low-Medium
**Status:** Unexplored

---

### 2. Anchor History + Entropy Alarm (per-target rolling window)

**Description:** Extend `report-anchors` (`cli/report_anchors.py`, 323 LOC) with three new metrics per `main_url` over 30/90-day rolling windows: (1) Shannon entropy of anchor-text distribution, (2) exact-match anchor ratio (`anchor_type == "exact"` / total), (3) top-3 anchor concentration. Emit warnings when thresholds known to correlate with manual-action risk are crossed (initial defaults: entropy < 1.5, exact-ratio > 10% over 90d, top-3 > 25%). Optionally writes warnings into the run's JSONL header for downstream alerting.

**Rationale:** Penguin-era anchor over-optimization is **the** classic manual-action trigger for backlink networks — it is exactly what this project exists to mitigate. Today `anchor_profile.py:240-247` counts anchor types but never computes distributional shape over time. Pure local analysis on existing checkpoint+log data, no LLM, no live ranking signal, no scraping. Extends a shipped feature rather than introducing a new surface.

**Downsides:** Initial threshold defaults are guesses without ground-truth incident data — risk of false alarms in low-N regimes (a solo operator with 1-2 targets and < 20 anchors per target will see "high concentration" as a sampling artifact). Mitigation: warnings only fire after N ≥ 30 observations per target.

**Confidence:** 85%
**Complexity:** Low-Medium
**Status:** Explored — brainstorm started 2026-05-14

---

### 3. Footprint-aware HTML Emission Audit (`bp footprint`)

**Description:** Offline auditor that (a) renders the same plan row through `work_themed_generator.py` + `markdown_utils.py` and (b) diffs the resulting HTML against a held-out fixture corpus of real human posts on Blogger/Medium. Flags stable byte-level "tells" the project's emission carries across all 6 work-themed permutations: identical `target="_blank" rel="noopener"` attribute ordering, identical whitespace before `<a>` tags, three-link-in-final-paragraph fingerprint, comma vs em-dash separator pattern. Output: `bp footprint` report listing top-N fingerprint candidates that would cluster across destination domains. No live emission change — pure detection.

**Rationale:** `work_themed_generator.py:7,43` self-comments acknowledge fingerprint awareness, but the existing mitigation only addresses **link ordering** (6 permutations). The surrounding HTML scaffolding — `<a target="_blank" rel="noopener">` ordering at `work_themed_generator.py:206-212`, the single hardcoded `<a>` template at `markdown_utils.py:255`, `_safe_anchor`'s whitespace policy — is **identical across all 6 permutations and across all rows**, which is exactly the cluster key Google's link-spam team would use to group destination domains pointing at the same money URL. Most novel idea on the list; defends the project's actual KPI (backlinks that survive cluster classification).

**Downsides:** Building/maintaining the human-post corpus is real curation cost (100+ posts minimum for statistical signal). Risk of over-fit — matching today's Medium default `rel` order may force unnatural ordering tomorrow when Medium changes default. False positives (whitespace anomalies) slow operator without proven yield improvement. Mitigation: ship as advisory `bp footprint` CLI, not a gate — operator decides.

**Confidence:** 78%
**Complexity:** Medium
**Status:** Unexplored

---

### 4. Silent-Drop Tripwire (planned-vs-persisted reconciliation)

**Description:** Add a UUID to every plan row in `plan-backlinks`. Each pipeline stage (`validate-backlinks`, `publish-backlinks`, `report-anchors`) appends a reconciliation summary to its JSONL header: `{planned: N, attempted: M, persisted: K, dropped_at: {stage: count}}`. At run end, emit a structured "delta report" listing every UUID that vanished and the exact stage that ate it (dedup, language_check filter, linkcheck reject, adapter error, config overwrite).

**Rationale:** Today's pipeline drops rows in at least 5 places (`feedback_config-save-overwrite-pattern.md`, dedup, language_check, linkcheck, adapter filter), each silent. Operator's "I planned 20, only 5 shipped" gap is unexplainable. The Round-2 survivor "Append-Only Event Log" is the substrate; this idea is the **minimum falsifiable invariant** layered on top: `planned == attempted + dropped_at.*`. Cheapest reconciliation idea on the list.

**Downsides:** Requires a plan JSONL schema bump (adding UUID field) — must define migration story for in-flight runs (mitigated: UUID auto-generated for new runs only; old runs degrade to `planned: ?`). Without operator-facing alerting wired in, "tripwire" is just another log line — needs at least a non-zero exit code on drops to be useful in cron.

**Confidence:** 82%
**Complexity:** Low
**Status:** Unexplored

---

### 5. Property-test the Gate Primitives

**Description:** Add `tests/test_gate_properties.py` using `hypothesis` (or hand-rolled fuzzers if a new dep is unwanted) covering `language_matches()` (`anchor_lang.py:81`), `linkcheck.is_alive()`, `verify_publish()`, `link_attr_verifier`. Tests assert structurally: (1) each gate rejects at least one constructed adversarial input per supported language/locale, (2) Shannon entropy of gate output over N=10000 random inputs exceeds a non-trivial floor, (3) pass-rate on a held-out fixture of known-mismatched anchors equals zero. Replaces "examples-pass-therefore-correct" with "gate is structurally not tautological".

**Rationale:** `language_matches()` shipped silently always-True and passed **999 example-based tests** until 2026-05-14 (`docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`). This is a CLASS of bug: any gate-shaped predicate could be a no-op and example tests can't catch it. Property tests catch it structurally. Hypothesis is mature, single new dev-time dep.

**Downsides:** Writing good properties is harder than writing examples — 1-2 weeks of solo velocity hit during ramp-up. Hypothesis can generate inputs that aren't realistic (random Unicode noise) and drive false bug reports — shrinker tuning is its own time sink. Risk: only 1 documented incident of tautological gate so far — speculative defense if no other gate has the same shape.

**Confidence:** 80%
**Complexity:** Low-Medium
**Status:** Unexplored

---

### 6. Logger-level Secret Redactor

**Description:** ~30-line patch to `logger.py:32-37`: module-level set of sensitive key names (`client_secret`, `integration_token`, `access_token`, `refresh_token`, `id_token`, `api_key`); in `PipelineLogger._emit`, recursively walk `extra` dict and redact any matching key to `***` before `json.dumps(record)`. **Explicitly out of scope (rejected):** `bp config doctor --leakage` filesystem scanner — use `gitleaks`/`trufflehog` instead, zero in-repo code.

**Rationale:** Single chokepoint defense for token leakage. 4 CLI entry points + retry layer + Brave fallback + webui all share `PipelineLogger`. `logger.py:32-37` does `json.dumps(record)` on whatever `extra` dict the caller passed — nothing prevents an exception handler from logging the full `headers` dict (which carries `Authorization: Bearer ...`). Auditor lens (Round-3 frame D) wouldn't accept "we'll be careful when calling logger".

**Downsides:** No documented incident of secret leak in this project yet — speculative defense. Over-redaction risks masking debug info (a `client_id` that should be visible if its key happens to match the regex). Mitigation: use exact-key match, not substring.

**Confidence:** 72%
**Complexity:** Low
**Status:** Unexplored

---

### 7. Config Echo Chamber (effective-config SHA + 4-line CLI banner)

**Description:** Every CLI entrypoint starts with a 4-line stderr banner: (1) which `config.toml` file resolved (full path), (2) which env vars overrode it (`BP_*` namespace), (3) which platform set is active, (4) SHA256 of the **resolved-and-merged** config dict (not the raw TOML bytes). The same SHA is stamped into the run JSONL header so any artifact can be reverse-mapped to its effective config. Pairs with idea #1 (Config Safety Net): #1 protects write-side, #7 surfaces read-side.

**Rationale:** Operator-mental-model gap: "I edited the config but nothing changed." Today config resolution is invisible — env overrides, `--config` flag, default path, all silently merge. 4-line echo costs ~10 LOC; SHA stamping enables artifact-to-config traceability for debugging months-old runs.

**Downsides:** Banner blindness — chatty stderr gets ignored. SHA definition matters: raw TOML bytes (catches whitespace edits) vs resolved dict (catches semantic changes) have different debug utility — must pick. Mitigation: SHA over the canonicalized resolved dict (sorted keys, no whitespace).

**Confidence:** 70%
**Complexity:** Low
**Status:** Unexplored

---

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| A1 | Heartbeat Beacon + Dead-Man's Switch | Subsumed by Round-2 Post-Publish Health Monitor + Event Log; 20-line shell does it |
| A3 | Quota & Rate Budget Ledger | Rebrand of Round-2 Link Velocity Governor — same primitive at different time scale |
| A5 | Crash-Safe Run Envelope (SIGTERM) | Cosmetic vs. shipped checkpoint/resume; risks locking in "aborted" classification of actually-succeeded publishes |
| A7 | Credential Lifecycle Daemon | Speculative — Medium integration token doesn't expire on schedule, Blogger SDK auto-refreshes; daemon refreshes ~zero things |
| A8 | Self-Bisecting Failure Triage | Duplicates D-D7 (Circuit Breaker); 5xx auto-disable conflicts with `feedback_api-idempotency-lesson.md` |
| B1 | nofollow Reality Receipt | Measurement layer of Round-2 Dofollow Adapter survivor — wait for that to land |
| B3 | Dry-Run Twin (--rehearse) | Pytest + autouse HTTP mocks already provide this; fake-adapter fidelity goes stale on every Medium change |
| B5 | Adapter Honesty Card | View over Round-2 Event Log + Health Monitor; `jq` script post-survivor-landing |
| B7 | Provenance Stamp (invisible HTML comment) | **Footprint emission risk** — opaque identifier across all published posts is exactly the cluster key Google's link-spam team uses. Directly contradicts #3 |
| C1 | Pipeline Event Replay (`bp simulate`) | Subsumed by pytest + autouse mocks; "historical JSONL → regression fixtures" only useful if past bugs map to JSONL state |
| C2 | Sidecar State Unifier (`bp state`) | Shell script in disguise (`jq | column`); aggregator-before-stable-sources is premature |
| C5 | Medium Selector Drift Heatmap | View over Round-2 Multi-Candidate Selectors survivor — that survivor will produce this report internally |
| C7 | Static HTML Publish Preview | Marginal ongoing value (useful first-time, then ignored); plan→publish gap is 99% data, 1% visual |
| D5 | `ANCHOR_TYPES` `Literal`/`StrEnum` | Pure type hygiene without documented incident; mypy --strict on anchor_* modules surfaces 50+ unrelated issues to triage |
| D6 | Centralized `requests.Session` | Session-sharing introduces cookie-leak coupling between Medium/Blogger adapters; consistent UA-per-role is itself a footprint |
| D7 | Per-target-domain Circuit Breaker | Duplicates A8; in-process state is meaningless for cron-style operator (process exits between runs); subsumed by Velocity Governor's hard-threshold mode |
| M1 | Anchor Pool Carrying-Capacity Forecaster | Strong concept but stochastic anchor scheduler makes forecast noisy; confidence interval swamps point estimate; auto-pause is foot-gun. Worth revisiting as warning-only after #2 ships and produces real usage data |
| M2 | Anchor Schema Drift Diff (`bp anchors diff`) | Finite-life feature (dies after schema unification); one-time migration script better |
| M4 | Post-Publish Forensics Pack | Overlaps Round-2 Post-Publish Health Monitor; re-scraping Medium at scale risks rate-limit/ban |
| M6 (scanner half) | `bp config doctor --leakage` filesystem scanner | Reinvents `gitleaks`/`trufflehog` — wrap external tool, zero in-repo code. Logger-redactor half kept as Round-3 #6 |
| S1 | Unattended Cockpit (A1+M1+M4) | Bundles 3 weak/borderline ideas; "single dashboard" risks recursive sibling-page complexity creep |
| S2 | Authority Truth Layer (B1+B7+B5) | B7 contamination (footprint risk); B5 depends on Round-2 survivors landing first |
| S3 | Production-Hardening Bundle (M6+D6+D4) | Better as 3 sequenced PRs than 1 giant diff; `feedback_plan-duplicate-sub-blocks.md` warns of internal contradictions |
| S5 | Run Reproducibility Toolkit (B8+B4+C1) | C1 already rejected (pytest subsumption); ship B4 and B8 as separate small PRs |

## Cross-cutting Observations

- **Pairings worth shipping together**: #1 (Config Safety Net write-side) + #7 (Config Echo Chamber read-side) close the config-confusion gap from both directions.
- **Pairings worth sequencing**: #4 (Silent-Drop Tripwire) is best built **after** the Round-2 Append-Only Event Log survivor lands — that's its natural substrate.
- **The most novel idea (#3)** is also the highest-risk to execute (corpus curation) — but it's the only candidate that defends against the SpamBrain classification threat at the **emission** layer, which is upstream of every other anchor/velocity/dofollow defense.

## Session Log

- 2026-05-14: Round-3 fresh pass. 4 frames (cron-safe, mental-model mismatch, hidden value mining, external expert audit) generated 32 raw candidates → 26 distinct + 4 synthesis combos after dedup → 7 survivors after two-layer adversarial filter (skeptic critique + orchestrator rubric with explicit LLM-free / footprint-emission / subsumption-by-Round-2-survivor dimensions). All 12 prior shipped/in-flight/survivor ideas and 22 prior rejections explicitly excluded.
- 2026-05-14: Idea #2 (Anchor History + Entropy Alarm) selected for brainstorm.
