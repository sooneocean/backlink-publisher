---
title: Inverted negative-shape assertion enshrined the save_config data-loss bug
date: 2026-05-14
category: test-failures
module: config_persistence
problem_type: test_failure
component: testing_framework
symptoms:
  - "`save_config()` silently dropped `[sites.*]`, `[anchor.proportions]`, `[llm.anchor_provider]`, `anchor_pools` sections on every write"
  - "Test suite reported green for weeks while the documented v2-fields data-loss bug remained live in production"
  - "Fixing `save_config` in PR #12 (commit `a4534f9`) flipped the previously-green test to red, exposing the inverted contract"
root_cause: logic_error
resolution_type: test_fix
severity: high
related_components:
  - tooling
  - documentation
tags:
  - negative-assertion
  - test-locks-in-bug
  - config-persistence
  - data-loss
  - assertion-inversion
  - round-trip
  - toml
  - regression-defense
---

# Inverted negative-shape assertion enshrined the save_config data-loss bug

## Problem

`tests/test_config_v2_pools.py::test_save_config_does_not_round_trip_v2_fields` asserted that `save_config()` would NOT emit `[sites.*]`, `[anchor.proportions]`, `[llm.anchor_provider]`, or `anchor_pools` sections after writing. The docstring claimed this was a "critical contract: new fields are read-only — save_config must not emit them". The test passed green for weeks.

What was actually happening: `save_config()` hand-rolled TOML output for four known section roots (`[blogger]/[medium]/[targets]/+oauth`) and silently dropped every other section on disk. The "must not emit" contract was a rationalization of broken behavior. The test was load-bearing for the documented `feedback_config-save-overwrite-pattern.md` data-loss bug (auto memory [claude]) — fixing `save_config` would have turned this test red, making it an active barrier to the fix.

## Symptoms

- A test docstring confidently describing what the code "must not" do, when the underlying behavior is itself documented as a bug elsewhere.
- Multiple `assert "..." not in rewritten` lines as the test's only assertions, with no positive complement.
- A planning artifact (`docs/plans/2026-05-14-003-feat-config-safety-net-plan.md`) and a feedback memory note (`feedback_config-save-overwrite-pattern.md`, auto memory [claude]) that describe the very behavior the test claims is correct, but as a bug to fix.

## What Didn't Work

- **Trusting the test docstring.** "Critical contract: new fields are read-only" looks authoritative until you ask why the contract should exist. There was no architectural reason for it — only the absence of a way to safely serialize the fields.
- **Treating the test as immutable when fixing `save_config`.** First instinct on the failing test after the fix was "the test caught a regression — back off". Inverting that read of the failure was the recovery step: the test caught the right code path, only its polarity was wrong.
- **Searching for the bug in `save_config` source alone.** The test was equally load-bearing — finding only one of (broken code, locked-in test) would have left the other to silently re-enshrine the bug in a future review.

## Solution

When a doc-review surfaces a buggy contract enshrined by negative-shape assertions, **invert the assertion** as part of the fix. Do not delete the test — the test caught the right code path, only its polarity is wrong. The inverted assertion now defends against regression to the bug.

Concrete diff from this incident:

**Before** (test was green only because `save_config` was dropping fields):

```python
def test_save_config_does_not_round_trip_v2_fields(tmp_path, monkeypatch):
    """Critical contract: new fields are read-only — save_config must not emit them."""
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg_path = _write_toml(tmp_path, FULL_FIXTURE)
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path)
    rewritten = cfg_path.read_text(encoding="utf-8")
    assert "[sites." not in rewritten              # all four assertions
    assert "anchor_pools" not in rewritten         # green only because the
    assert "[anchor.proportions]" not in rewritten # data-loss bug was live
    assert "[llm.anchor_provider]" not in rewritten
```

**After** (same test, inverted — defends against regression):

```python
def test_save_config_preserves_v2_fields_verbatim(tmp_path, monkeypatch):
    """save_config must preserve unknown sections byte-for-byte (Config Safety Net).

    Previously save_config silently dropped any section it didn't know how to
    serialize, which was the documented data-loss bug class behind
    feedback_config-save-overwrite-pattern.md. The new contract: those same
    sections survive a save_config call verbatim (bytes copied from disk).
    """
    monkeypatch.delenv("BACKLINK_LLM_API_KEY", raising=False)
    cfg_path = _write_toml(tmp_path, FULL_FIXTURE)
    cfg = load_config(cfg_path)
    save_config(cfg, path=cfg_path)
    rewritten = cfg_path.read_text(encoding="utf-8")
    # Previously-dropped sections now survive
    assert "[sites." in rewritten
    assert "anchor_pools" in rewritten
    assert "[anchor.proportions]" in rewritten
    assert "[llm.anchor_provider]" in rewritten
    # Round-trip: data survives semantically, not just textually
    cfg2 = load_config(cfg_path)
    assert cfg2.site_url_categories == cfg.site_url_categories
    assert cfg2.target_anchor_pools_v2 == cfg.target_anchor_pools_v2
    assert cfg2.anchor_proportions == cfg.anchor_proportions
```

Three changes, in order: (1) rename the function and docstring to describe the *correct* contract; (2) flip every `not in` to `in`; (3) add a round-trip semantic assertion that the data survives through `load_config` again, not just the bytes through `read_text`.

The behavioral fix in `src/backlink_publisher/config.py` is independent — it added `_preserve_unknown_sections()` to walk existing config bytes and copy any section whose root is not in `_SAVE_CONFIG_KNOWN_ROOTS = {"blogger", "medium", "targets"}` verbatim. See commit `a4534f9` for the full implementation.

## Why This Works

A negative-shape assertion (`assert X not in output`, `assert stderr == ""`, `assert len(results) == 0`) is structurally indistinguishable from "the code is incorrectly failing to produce X". The test passes green either way — by the correct contract (X really shouldn't be there) or by the bug (X should be there but isn't). The docstring is the only signal that distinguishes the two, and docstrings rationalize whatever the code does.

Inverting the assertion moves the test from "asserts the bug" to "asserts the absence of the bug". The same test position, same fixture, same exercise of the code path — only the polarity flips. The test's value (catching changes in the relevant code path) is preserved; its semantic meaning is corrected.

The round-trip assertion (`cfg2 = load_config(cfg_path); assert cfg2.X == cfg.X`) is load-bearing: byte preservation alone could pass via raw-text concatenation while subtly mangling structure (mismatched quotes, wrong section depth). The semantic round-trip verifies the parser can read back what the writer emitted — which is the contract operators actually depend on.

## Prevention

Scan for these signals during code review, before any P0 fix, and as part of `/ce:review` passes. Reference: `feedback_test-locks-in-bug.md` (auto memory [claude]) is the canonical short-form warning; `feedback_cereview-finds-latent-bugs.md` (auto memory [claude]) confirms multi-persona review surfaces these inversions reliably.

**Audit grep recipe** — run before any fix in a documented bug class:

```bash
# Negative-shape assertion patterns
rg -n 'assert\s+.+\s+not\s+in\b' tests/
rg -n 'assert\s+\w*(stderr|stdout|errors?)\s*==\s*""' tests/
rg -n 'assert\s+len\(.+\)\s*==\s*0' tests/
rg -n 'def test_.*(does_not|must_not|should_not|is_read_only|is_dropped|is_ignored)' tests/
```

For each hit, ask:

> If the behavior this test is "protecting against" were actually the correct behavior, would this test go red?

If yes, the test is a candidate for inversion the day that behavior gets fixed — flag it now, do not silently delete it later. If no, the test is fine.

**Defensive-over-explanation smell**: a test docstring that explains *why* the negative behavior is correct (vs simply describing it) is a smell. Real contracts are rarely so politely rationalized — they are stated. A docstring containing "must not emit", "is read-only", "we deliberately drop", "intentionally absent" should trigger a closer read.

**Pair every negative-shape assertion with a positive complement** when writing new tests. If you find yourself writing `assert X not in result`, ask whether there's also a positive thing the code *should* be doing that you can assert in the same test. The complement protects against the same gate going tautological — if the gate stops doing X, the negative still passes, but the positive will fail.

**Examples test, not just example-test**: when the failure mode is "gate returns True for every input", example-based tests cannot catch it. Property-based tests (e.g. `hypothesis`) with structural invariants are the structural defense. PR #14 (`feat/property-test-gates`, commit `3780c61`) ships the pattern for the verify_publish gate primitives and `anchor_metrics.normalize`. Apply the same shape to any newly-fixed gate going forward.

## Related Issues

- **Memory note** `feedback_test-locks-in-bug.md` (auto memory [claude]) — the short-form warning this incident validates. Cites this exact test as the example.
- **Memory note** `feedback_config-save-overwrite-pattern.md` (auto memory [claude]) — the underlying data-loss bug the inverted test was protecting.
- **Memory note** `feedback_cereview-finds-latent-bugs.md` (auto memory [claude]) — confirms multi-persona review surfaces these inversions; the second-pass `document-review` on PR #12's plan flagged the test contract collision before code was written.
- **Plan** `docs/plans/2026-05-14-003-feat-config-safety-net-plan.md` — the executed fix that triggered the inversion. Lines 13-19, 56, 85 reference both feedback notes by name.
- **Plan** `docs/plans/2026-05-14-002-feat-anchor-entropy-alarm-plan.md` — sibling application of the same testing discipline in the anchor-distribution domain; cites `feedback_test-locks-in-bug.md` four times (lines 69, 74, 155, 239, 440).
- **Ideation** `docs/ideation/2026-05-14-round3-fresh-pass-ideation.md` (idea #5) — Property-test Gate Primitives, the forward-looking structural defense against the broader bug class. Shipped as PR #14.
- **PR #12** (`feat/config-safety-net`) — the behavioral fix to `save_config`.
- **PR #14** (`feat/property-test-gates`) — the property-based testing pattern that prevents the broader "tautological gate" failure mode.
- **Commit `a4534f9`** (`feat(config): preserve unknown sections + atomic write + snapshot history`) — fix + assertion inversion landed together.
