"""Static AST gate: ban raw subscript mutation of security-toggle config keys
in test files outside the sanctioned ``disable_csrf`` fixture (Plan
2026-05-27-003 Unit 3).

Motivation: ~25 test files set ``webui.app.config["WTF_CSRF_ENABLED"] = False``
(or ``CSRF_ENABLED``/``SESSION_COOKIE_SECURE``) on the shared module-level
``webui.app`` singleton without restoring it. The unrestored ones leak a
disabled CSRF guard into every later test, which is how a dead guard hid behind
a false-green (PR #261). The containment net in ``conftest.py`` now restores
these keys per-test, but this gate stops the *pattern* from spreading: new tests
must use the sanctioned ``disable_csrf`` fixture rather than raw mutation.

Scope and honesty:
- Config-key subscripts only. Env-toggle safety is the net's job (R2); the
  dominant env-mutation form is ``monkeypatch.setenv``, a self-restoring Call
  that a subscript ban neither sees nor should flag.
- This catches the canonical ``<x>.config["KEY"] = ...`` shape. It is a
  discoverability / speed-bump layer, NOT an absolute wall — ``config.update``,
  ``setattr``, or a helper indirection can evade it. The runtime net is the
  real safety layer.
- ``TESTING`` is deliberately NOT gated: ``config["TESTING"] = True`` is
  standard test-client setup in ~31 files, not a security downgrade.

The grandfather allowlist is a closed set of ``(file, key)`` PAIRS (not files):
per-file grandfathering would let an allowlisted file silently add a *second*
toggle mutation. The set may only shrink — adding or removing any mutation trips
the exact-match assertion and forces a same-PR allowlist edit.
"""
from __future__ import annotations

import ast
from pathlib import Path

from conftest import SECURITY_CONFIG_KEYS

TESTS_DIR = Path(__file__).resolve().parent

# ``conftest.py`` is the sanctioned mutation site (``disable_csrf``); it is also
# excluded naturally by the ``test_*.py`` glob, but we never scan it.
_EXEMPT_FILENAMES = {"conftest.py"}

# Closed grandfather allowlist of (filename, key) pairs, re-derived against the
# branch base (origin/main b14d989): 31 pairs across 25 files. May only shrink.
GRANDFATHERED: frozenset[tuple[str, str]] = frozenset(
    {
        ("test_channel_bind_save.py", "SESSION_COOKIE_SECURE"),
        ("test_drafts_bulk_routes.py", "WTF_CSRF_ENABLED"),
        ("test_e2e_history_batch_management.py", "WTF_CSRF_ENABLED"),
        ("test_history_bulk_routes.py", "WTF_CSRF_ENABLED"),
        ("test_history_recheck.py", "WTF_CSRF_ENABLED"),
        ("test_history_template_rendering.py", "WTF_CSRF_ENABLED"),
        ("test_manifest_webui_wiring.py", "WTF_CSRF_ENABLED"),
        ("test_medium_login_routes.py", "SESSION_COOKIE_SECURE"),
        ("test_copilot_panel_render.py", "CSRF_ENABLED"),
        ("test_copilot_qa_render.py", "CSRF_ENABLED"),
        ("test_copilot_qna_route.py", "CSRF_ENABLED"),
        ("test_webui_bind_routes.py", "SESSION_COOKIE_SECURE"),
        ("test_webui_checkpoint.py", "WTF_CSRF_ENABLED"),
        ("test_webui_equity_ledger_recheck.py", "WTF_CSRF_ENABLED"),
        ("test_webui_equity_ledger_route.py", "WTF_CSRF_ENABLED"),
        ("test_webui_history_invariant.py", "SESSION_COOKIE_SECURE"),
        ("test_webui_history_invariant.py", "WTF_CSRF_ENABLED"),
        ("test_webui_image_gen.py", "CSRF_ENABLED"),
        ("test_webui_index_js_bootstrap.py", "CSRF_ENABLED"),
        ("test_webui_index_template_structure.py", "CSRF_ENABLED"),
        ("test_webui_publish_route.py", "CSRF_ENABLED"),
        ("test_webui_request_cache.py", "CSRF_ENABLED"),
        ("test_webui_route_contract.py", "CSRF_ENABLED"),
        ("test_webui_route_contract.py", "SESSION_COOKIE_SECURE"),
        ("test_webui_route_contract.py", "WTF_CSRF_ENABLED"),
        ("test_webui_routes_oauth.py", "SESSION_COOKIE_SECURE"),
        ("test_webui_settings_template_split.py", "CSRF_ENABLED"),
        ("test_webui_static_css_served.py", "CSRF_ENABLED"),
        ("test_webui_three_url.py", "CSRF_ENABLED"),
        ("test_webui_three_url.py", "SESSION_COOKIE_SECURE"),
        ("test_webui_three_url.py", "WTF_CSRF_ENABLED"),
        ("test_webui_unit3_security.py", "CSRF_ENABLED"),
        ("test_webui_unit3_security.py", "WTF_CSRF_ENABLED"),
        ("test_webui_url_verify_routes.py", "SESSION_COOKIE_SECURE"),
    }
)


def _subscript_config_key(target: ast.AST) -> str | None:
    """If *target* is ``<anything>.config["KEY"]``, return ``"KEY"`` else None.

    Matches the subscript key string regardless of receiver chain (robust;
    static receiver resolution is brittle).
    """
    if not isinstance(target, ast.Subscript):
        return None
    value = target.value
    if not (isinstance(value, ast.Attribute) and value.attr == "config"):
        return None
    sl = target.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _security_config_mutations(tree: ast.AST) -> list[tuple[str, int]]:
    """Return ``(key, lineno)`` for every ``<x>.config["<security key>"] = ...``
    (or ``+=`` / annotated) assignment in *tree*."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            key = _subscript_config_key(target)
            if key in SECURITY_CONFIG_KEYS:
                found.append((key, target.lineno))
    return found


def _test_source_files() -> list[Path]:
    files = sorted(TESTS_DIR.rglob("test_*.py"))
    assert files, f"no test source files discovered under {TESTS_DIR}"
    return files


def _discover_mutation_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for path in _test_source_files():
        if path.name in _EXEMPT_FILENAMES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for key, _lineno in _security_config_mutations(tree):
            pairs.add((path.name, key))
    return pairs


def test_no_new_security_config_mutations() -> None:
    """No test file may raw-mutate a security config key unless grandfathered."""
    found = _discover_mutation_pairs()
    new = sorted(found - GRANDFATHERED)
    assert not new, (
        "raw security-config mutation outside the sanctioned `disable_csrf` "
        "fixture. Use `disable_csrf` (conftest) instead, or — if this is a "
        "deliberate CSRF on/off test — add the (file, key) pair to "
        "GRANDFATHERED:\n  " + "\n  ".join(f"{f} -> {k}" for f, k in new)
    )


def test_grandfather_allowlist_only_shrinks() -> None:
    """The allowlist must match reality exactly — stale entries are removed in
    the same PR that removes the mutation (ratchets down, never up)."""
    found = _discover_mutation_pairs()
    stale = sorted(GRANDFATHERED - found)
    assert not stale, (
        "GRANDFATHERED lists (file, key) pairs no longer present in the tree; "
        "remove them so the allowlist only shrinks:\n  "
        + "\n  ".join(f"{f} -> {k}" for f, k in stale)
    )
    assert found == GRANDFATHERED  # exact ratchet (combined with the test above)


def test_scanner_discovers_known_offenders() -> None:
    """The glob must actually reach the webui test files (a broken glob would
    make the gate a silent no-op)."""
    names = {p.name for p in _test_source_files()}
    assert "test_webui_route_contract.py" in names
    assert "test_history_bulk_routes.py" in names


def test_scanner_flags_subscript_but_ignores_testing_update_and_env() -> None:
    """Anti-no-op guard: the collector must flag the canonical bad shape and
    must NOT flag TESTING, .update() calls, or os.environ writes — otherwise the
    gate is either a no-op or over-broad."""
    snippet = (
        'webui.app.config["CSRF_ENABLED"] = False\n'          # flagged
        'app.config["SESSION_COOKIE_SECURE"] = False\n'        # flagged
        'a.config["WTF_CSRF_ENABLED"] = False\n'               # flagged
        'x = app.config["SECRET_KEY"] = "k"\n'                 # flagged (multi-target Assign)
        'app.config["TESTING"] = True\n'                       # NOT gated
        'webui.app.config.update({"CSRF_ENABLED": False})\n'   # Call, not subscript
        'os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"\n'    # env, not .config
    )
    keys = {key for key, _lineno in _security_config_mutations(ast.parse(snippet))}
    assert keys == {
        "CSRF_ENABLED",
        "SESSION_COOKIE_SECURE",
        "WTF_CSRF_ENABLED",
        "SECRET_KEY",
    }, keys
