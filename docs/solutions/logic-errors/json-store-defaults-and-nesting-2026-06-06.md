---
title: "JSON store default dicts, mutation safeguards, and nesting discipline"
date: 2026-06-06
category: docs/solutions/logic-errors
module: webui_store, webui_app/routes/wizard.py, webui_app/services/watch_service.py
problem_type: logic_error
component: webui_store
symptoms:
  - "WizardConfigStore.get_seed_sources() returns non-empty list from a fresh store"
  - "Cross-test contamination: one test's add_seed_source spills into another's assertions"
  - "Wizard routes' save_* endpoints write data at JSON root instead of under wizard_config key"
  - "AttributeError: 'WebUIStores' object has no attribute 'channel_status'"
  - "select_best_channel returns None when channels look valid"
  - "enqueue_publish test assertions KeyError on target_url / platform / source"
root_cause: wrong_api
resolution_type: fixed
severity: medium
tags:
  - json-store
  - mutable-default
  - deepcopy
  - shallow-copy
  - nested-json
  - webui-store
  - wizard
  - watch-service
  - test-isolation
---

# JSON store default dicts, mutation safeguards, and nesting discipline

## Problem

Wave 1 implementation (Setup Wizard + Watch Service) introduced five distinct bugs
from a common failure mode: **assuming JSON store APIs are self-documenting about
nesting and ownership.**

### Bug 1 — `_DEFAULT_CONFIG` shallow copy shares nested lists

`WizardConfigStore._get()` used `dict(_DEFAULT_CONFIG)` to create a defaults
template. This is a **shallow copy** — the `seed_sources: []` and `channels: []`
lists are the *same objects* shared across every `_get()` call. When
`add_seed_source()` did `cfg.setdefault("seed_sources", []).append(record)`, it
appended to *the list inside `_DEFAULT_CONFIG`*, not to a store-specific copy.

**Effect**: Any call to `add_seed_source` permanently contaminated the module-level
`_DEFAULT_CONFIG`. Subsequent `get_seed_sources()` calls on any *different* store
instance (even with an empty file) returned the accumulated sources. Cross-test
contamination appeared in pytest because `tmp_path` is function-scoped — each test
had its own file, but the shared `_DEFAULT_CONFIG` produced the same inflated
result.

**Fix**: `import copy; merged = copy.deepcopy(_DEFAULT_CONFIG)`. And in
`add_seed_source()`, build a fresh list instead of mutating in place:

```python
# Before:
cfg.setdefault("seed_sources", []).append(record)

# After:
sources = list(cfg.get("seed_sources", []))
sources.append(record)
cfg["seed_sources"] = sources
```

### Bug 2 — Wizard route transforms write at wrong JSON nesting level

The wizard routes (`save_seed_sources`, `save_channels`, `save_rules`,
`launch_wizard`) all had inline `_update(cfg)` transforms that received `cfg`
(the current wizard_config dict) and returned a new dict. But the helpers
stored the result at `data["wizard_config"]`, not at top level.

The route handlers wrote directly into the JSON file root:

```python
def _update(cfg):
    merged = dict(cfg)
    merged["seed_sources"] = sources
    return merged

wizard_store.update(_update)  # writes merged dict at data root
```

But `WizardConfigStore.update()` expects the transform to return the full `data`
dict, not just the `cfg` sub-dict. The store's `mark_completed()` and
`mark_skipped()` methods implement this correctly:

```python
def _write(data: dict) -> dict:
    cfg = data.setdefault(_KEY, dict(_DEFAULT_CONFIG))
    # modify cfg
    data[_KEY] = cfg
    return data

self.update(_write)
```

**Fix**: Route helpers were rewritten to follow the `data.setdefault("wizard_config", {})`
pattern.

### Bug 3 — `_get_stores()` references non-existent `store.channel_status`

The wizard route's `_get_stores()` helper tried to return
`stores.wizard_config, stores.score, stores.seen_urls, stores.channel_status`.
But `channel_status` is a **module-level singleton** (`webui_store.channel_status`),
not a property on the `WebUIStores` Flask extension class in `registry.py`.

**Effect**: Every wizard route call raised `AttributeError` because
`WebUIStores` has no `channel_status` attribute.

**Fix**: Remove the 4th tuple element from `_get_stores()`. `channel_status`
isn't needed in the wizard routes — this was left over from an earlier draft.

### Bug 4 — `select_best_channel` tests omitted `bound: True`

`WatchService.select_best_channel()` filters channels by
`ch_cfg.get("bound", False)` — only bound channels qualify. Test fixtures
provided channel configs without the `"bound"` key, so every channel was
filtered out and `select_best_channel()` returned `None`.

**Fix**: Add `"bound": True` to all channel configs in tests.

The `test_unbound_channel_filtered_out` test was also wrong — it set
`bound: False` on one channel and omitted it on the other. With the default
`False`, both were filtered. Fixed by giving the expected-selectable channel
`bound: True`.

### Bug 5 — `enqueue_publish` test assertions expect flat keys on nested task

`enqueue_publish()` stores tasks with this shape:

```python
{
    "id": "abc12345",
    "status": "pending",
    "created_at": "...",
    "urls": ["https://target.com"],
    "config": {
        "platform": "medium",
        "source": "watch_service",
        ...
    },
}
```

But test assertions looked for flat keys:

```python
assert tasks[0]["target_url"] == "https://target.com"   # KeyError
assert tasks[0]["platform"] == "medium"                 # KeyError
assert task["source"] == "watch_service"                # KeyError
```

**Fix**: Update assertions to match actual nested structure:
```python
assert tasks[0]["urls"][0] == "https://target.com"
assert tasks[0]["config"]["platform"] == "medium"
assert task["config"]["source"] == "watch_service"
```

### Bug 6 — Wizard route tests used wrong filename

Tests created `WizardConfigStore(tmp_path / "wizard.json")` but the
`WebUIStores` registry creates `wizard-config.json` (with hyphen). The route
test `_wizard_client` fixture creates a real Flask app whose stores read from
`wizard-config.json`, so tests writing to `wizard.json` never persisted data
the routes could see.

**Fix**: Changed test file path to `wizard-config.json` to match registry.

## Root cause analysis

All six bugs share a common pattern: **assuming the store/route API structure matches
what you intuitively expect, without verifying the actual data shape.**

| Bug | Assumption | Reality |
|---|---|---|
| 1 | `dict(other_dict)` deep-copies nested objects | Python `dict()` copies one level only |
| 2 | `store.update(fn)` replaces the sub-value | It replaces the whole file content — you must nest at the right key |
| 3 | `WebUIStores` mirrors all `webui_store` module exports | It only explicitly declared stores |
| 4 | Channel config keys are intuitive | `bound` matters; default is `False` |
| 5 | Task shape is flat | Tasks are nested under `urls`/`config` |
| 6 | Filename matches by convention | Filename must match `WebUIStores` registry exactly |

## Prevention

1. **For `JsonStore` subclasses with nested defaults**: Always `copy.deepcopy()`.
   Never `setdefault(.., []).append()` — build a new list instead.

2. **For `store.update()` transforms**: Always operate on the full `data` dict and
   explicitly manage the sub-key. The `data.setdefault(KEY, default)` pattern
   (used by `mark_completed`/`mark_skipped`) is the canonical approach.

3. **For route helpers that aggregate stores**: Only expose what the routes actually
   use. Unused store references (like `channel_status`) signal confusion.

4. **For test fixtures**: Use the same file paths as production code.

5. **For nested task structures**: Test the exact serialization output, not your
   mental model of it.
