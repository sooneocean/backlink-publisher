---
title: "--strict-markers in pyproject addopts silently no-ops when conftest does module-load work; enforce on the CLI"
date: 2026-06-01
category: test-failures
module: backlink-publisher (pytest config / CI)
problem_type: test_failure
component: testing_framework
severity: medium
symptoms:
  - "A typo'd @pytest.mark.NAME emits PytestUnknownMarkWarning instead of erroring, even with addopts including --strict-markers in [tool.pytest.ini_options]"
  - "pytest exits 0 on an unknown marker although --strict-markers is configured"
  - "The identical --strict-markers flag errors correctly when passed on the command line"
root_cause: config_load_order
resolution_type: config_fix
applies_when:
  - "Enforcing pytest marker hygiene (--strict-markers / --strict-config) in a repo whose conftest.py does heavy module-level work"
  - "conftest imports application modules or mutates os.environ at import time rather than inside pytest_configure"
related_components:
  - tooling
  - ci_cd
tags:
  - pytest
  - strict-markers
  - strict-config
  - addopts
  - conftest
  - ci
  - false-gate
---

# `--strict-markers` from `addopts` silently no-ops; enforce it on the CLI

## Problem

Adding `addopts = ["--strict-markers", "--strict-config"]` to `[tool.pytest.ini_options]`
to enforce marker hygiene **looks** correct but does not fire: an unknown / mistyped
`@pytest.mark.foo` still only emits a `PytestUnknownMarkWarning` and the run exits `0`.
The identical flag passed on the command line correctly fails collection with
``'foo' not found in `markers` configuration option`` (exit 1).

This is a **false gate** — the worst kind, because CI shows green while the protection
you believe you added is inert.

## `addopts` *is* being read — that's the trap

Confirm it: drop a bogus flag into `addopts` (e.g. `["--zzz"]`). pytest exits 4 with
`unrecognized arguments: --zzz` and names the `inifile`. So `addopts` is parsed and
`--strict-markers` does reach pytest — the strictness simply does not take effect.

## Root cause: conftest module-load order

When `conftest.py` runs heavy work at **module-load** time — importing application
modules, mutating `os.environ`, freezing path constants — that work happens during the
initial conftest-collection phase, *before* the `addopts`-sourced `--strict-markers`
has been applied to the marker-validation machinery, so the unknown-mark check runs in
warn mode. A command-line `--strict-markers` is parsed in the initial argument set
*before* conftest loading, so it is already active when collection validates marks.

This repo's conftest states the trigger directly: *"pytest_configure is too late — the
conftest body imports ... at module load"* (it performs a HOME-redirect guardrail plus
`registry`/`adapters` imports at import time).

## Resolution

Put the strict flags on the **CI / CLI invocation**, not in `addopts`:

```yaml
# .github/workflows/ci.yml — Run tests step
run: |
  python -m pytest tests/ -v --tb=short --timeout=30 -n auto --strict-markers --strict-config
```

Verified under `-n auto` (pytest-xdist): a bogus marker fails collection (exit 1) while
the clean suite — including a file that legitimately applies a registered module-level
marker — passes with zero false positives.

## Verification recipe

```bash
printf 'import pytest\n@pytest.mark.zzz_unreg\ndef test_x(): assert True\n' > tests/test__proof.py
python -m pytest tests/test__proof.py --strict-markers          # exit 1: gate ACTIVE (CLI)
python -m pytest tests/test__proof.py                            # exit 0, warn-only if relying on addopts: gate INERT
rm tests/test__proof.py
```

## Takeaway

`--strict-markers` / `--strict-config` belong on the **CLI / CI command** in any repo
whose `conftest.py` runs import-time side effects. Treat *"added a strict flag to
`addopts`"* as unverified until a deliberately bad marker actually fails the run.
