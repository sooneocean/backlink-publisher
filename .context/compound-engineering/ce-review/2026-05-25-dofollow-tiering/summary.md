# ce:review (autofix) — dofollow-tiering Phase 1

Scope: feat/dofollow-tiering vs origin/main (Units 1-3). 8 reviewers (correctness, testing, maintainability, api-contract, kieran-python, adversarial, project-standards, learnings). agent-native skipped — no UI/agent-parity surface.

## Verdict: Ready with fixes (applied)

## Applied safe_auto fixes
- registry.register(): runtime-validate referral_value ∈ {high,low} (was None-only check; typo "HIGH" would silently mis-bucket). +2 gate tests.
- _report_format._resolve_row_tier docstring: corrected "single source of truth" contradiction — clarified plan-time mark intentionally wins over live registry; register() is the only writer.
- Added branch tests: unclassified referral bucket, markdown unknown-row show/omit, markdown tier_summary=None path.

## Dismissed (by-design)
- api-contract P0 "register() gate breaks import for nofollow-without-referral_value": that IS the gate's purpose; caught by standing test; existing platforms updated same-PR.
- adversarial "loud import failure" / "metadata is plan-time snapshot": intentional; docstring now states it.

## Accepted residual (no change)
- Three snapshot-fixture coupling: documented deferred cost → RegistryEntry dataclass at capability #3.
- referral_value kwarg vs accessor name overlap: scoped, no bug.
- status→tier mapping duplicated across _payload/_report_format: intentional divergence (plan marks only registered platforms; report handles unknown).

## Result: full suite 3698 passed pre-fix; 99 targeted passed post-fix.
