# AGENTS.md — tests

~160 test files, ~96K total lines. Network is mocked by default — 4 autouse conftest fixtures isolate config dir, URL checks, content fetches, and socket access.

## Running tests

```bash
pytest tests/                                          # all tests (PYTHONHASHSEED=0 via pyproject.toml)
pytest tests/ -n auto                                  # parallel full-suite run (~2.5x faster; CI uses this)
pytest tests/ -m "not real_ssrf_check"                 # skip live-network tests
pytest tests/test_no_monolith_regrowth.py -k "R4"      # single budget gate
pytest tests/test_webui_route_contract.py              # slowest single test (~1100+ lines)
pytest tests/scripts/                                  # worktree script tests
```

`-n auto` (pytest-xdist) is determinism-verified for the full suite and wired into
CI, but is **not** forced via `addopts`: a bare `pytest tests/single_file.py` stays
serial so a focused inner-loop run does not pay 10-worker startup, and parallel
machines (worktree swarms) do not multiply process count unexpectedly. Add `-n auto`
yourself for full-suite runs. The suite is xdist-safe because every store/lock path
resolves to a per-worker sandbox (`conftest.py` per-process `mkdtemp`) and
PYTHONHASHSEED=0 is set per worker.

## Test markers (opt-in live tests)

| Marker | What it does |
|---|---|
| `real_ssrf_check` | Exercise real `_check_url_for_ssrf` path |
| `real_content_fetch` | Exercise real `verify_urls_batch` (module-wide in `test_content_fetch.py`) |
| `real_image_gen` | Exercise real FRW image-gen endpoint (operator-only, never in CI) |
| `real_browser_publish_smoke` | Open live channel compose URL in attached Chrome (operator-only) |

## Test isolation

- **Session-scoped** `_isolate_user_dirs` fixture (`conftest.py`): redirects `BACKLINK_PUBLISHER_CONFIG_DIR` and `BACKLINK_PUBLISHER_CACHE_DIR` to `tmp_path` — operator's real config never leaks into tests.
- **4 autouse fixtures** (declared in conftest at various levels): config sandboxed, URL checks pass, content fetches pass, sockets blocked.
- PYTHONHASHSEED=0 is mandatory (set via `pyproject.toml` `[tool.pytest.ini_options].env`). Without it, footprint regression tests produce non-deterministic output.

## Test fixtures

| Path | Content |
|---|---|
| `fixtures/seed.jsonl` | E2E pipeline test data |
| `tests/fixtures/sloc_canary.py` | Expected radon SLOC values |
| `tests/fixtures/footprint_attack/` | HTML samples for footprint tests |
| `tests/fixtures/` | Additional test data files |

## Budget gates (hard-fail on regrowth)

| Test | Enforces |
|---|---|
| `test_no_monolith_regrowth.py` | radon SLOC ceilings from `monolith_budget.toml` (R4 hard + R7 warning) |
| `test_adapter_dofollow_gate.py` | `dofollow=` required keyword on `register()` |
| `test_save_config_section_taxonomy_canary.py` | `save_config` section taxonomy |
| `test_r9_extension_readiness.py` | Cross-layer wiring for adapter extensions |
| `test_bind_error_messages.py` | Chinese error-code→message mapping for channel binding |
| `test_security_toggle_mutation_gate.py` | No raw `*.config["CSRF_ENABLED"/...]=` in tests outside `disable_csrf` (grandfather `(file,key)` pairs, ratchets down) |

## Known quirks

- **YAML SHA quoting**: PyYAML int-coerces unquoted all-digit scalars. Always quote SHA values in YAML test fixtures: `f"    - '{sha[:7]}'\n"` (PR #98).
- **Slowest test**: `test_webui_route_contract.py` (~1423 lines, most expensive).
- **CSRF in tests**: Use the sanctioned `disable_csrf` fixture (conftest) to turn the global CSRF guard off for a test — it restores on teardown. Do **not** raw-mutate `app.config['CSRF_ENABLED'] = False` / `WTF_CSRF_ENABLED` / `SESSION_COOKIE_SECURE` / `SECRET_KEY`; `test_security_toggle_mutation_gate.py` fails CI on new occurrences. An autouse containment net (conftest `_restore_global_state_net`) restores these keys + enumerated security env vars to a clean baseline around every test, so leaks can't cross tests.
- **Test collection order**: Python 3.11 and 3.12 may differ on dict ordering in fixtures — use `sorted()` when asserting lists.
