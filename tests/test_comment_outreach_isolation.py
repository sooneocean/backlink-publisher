"""Structural isolation guarantees for the ``comment_outreach`` module.

Two promises this module makes, proven mechanically so they cannot silently
regress (plan 2026-05-27-005, Unit 1):

1. **Registry isolation** — the module imports nothing from the publishing
   adapter registry / adapter package. The sole allowed exception is a *lazy,
   function-local* import of the LLM provider inside the ``brief`` handler
   (added in Unit 7).
2. **Not a spam engine** — the module imports no browser-automation primitive
   and makes no comment-posting call (``*.post(...)`` or
   ``urllib.request.Request(..., data=...)``).

Plus a runtime check: running a non-``brief`` verb loads no registry module and
creates no ``events.db``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"
_PKG = _SRC_DIR / "backlink_publisher"

# Every file that must obey the isolation contract.
_GUARDED_FILES = sorted((_PKG / "comment_outreach").glob("*.py")) + [
    _PKG / "cli" / "comment.py"
]


def _collect_imports(tree: ast.AST) -> list[tuple[str, bool]]:
    """Return ``(module_path, is_function_local)`` for every import, including
    nested/function-body imports (``ast.walk`` alone loses the function scope)."""
    found: list[tuple[str, bool]] = []

    def visit(node: ast.AST, in_func: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, True)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    found.append((alias.name, in_func))
            elif isinstance(child, ast.ImportFrom):
                found.append((child.module or "", in_func))
            else:
                visit(child, in_func)

    visit(tree, False)
    return found


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", _GUARDED_FILES, ids=lambda p: p.name)
def test_no_publishing_registry_import(path: Path) -> None:
    """No file imports the registry/adapter package, except a lazy, function-local
    import of the standalone ``llm_anchor_provider`` module inside a handler."""
    violations = []
    for module, in_func in _collect_imports(_parse(path)):
        if "publishing.registry" in module or "publishing.adapters" in module:
            if module.endswith("llm_anchor_provider") and in_func:
                continue  # the single allowed brief-handler carve-out
            violations.append(f"{module!r} (function_local={in_func})")
    assert not violations, (
        f"{path.name} imports the publishing registry/adapters: {violations}. "
        f"comment_outreach must stay isolated; only a lazy function-local import "
        f"of llm_anchor_provider inside the brief handler is allowed."
    )


@pytest.mark.parametrize("path", _GUARDED_FILES, ids=lambda p: p.name)
def test_no_posting_or_browser_primitives(path: Path) -> None:
    """No browser-automation imports and no comment-posting call — the structural
    proof that this module is not a spam engine."""
    tree = _parse(path)

    banned_import_substrings = ("selenium", "playwright", "webui", "browser_publish")
    chrome_modules = {"chrome", "cdp"}
    import_violations = []
    for module, _ in _collect_imports(tree):
        low = module.lower()
        if any(s in low for s in banned_import_substrings):
            import_violations.append(module)
        if any(part in chrome_modules for part in low.split(".")):
            import_violations.append(module)
    assert not import_violations, (
        f"{path.name} imports a browser-automation primitive: {import_violations}"
    )

    post_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # *.post(...) — requests.post / session.post / http.post
        if isinstance(func, ast.Attribute) and func.attr == "post":
            post_calls.append("*.post()")
        # urllib.request.Request(..., data=...) — a POST in disguise
        is_request = (isinstance(func, ast.Attribute) and func.attr == "Request") or (
            isinstance(func, ast.Name) and func.id == "Request"
        )
        if is_request:
            has_data = any(kw.arg == "data" for kw in node.keywords) or len(node.args) >= 2
            if has_data:
                post_calls.append("urllib Request(data=...)")
    assert not post_calls, (
        f"{path.name} makes a posting call ({post_calls}); the module must never "
        f"submit comments."
    )


@pytest.mark.parametrize("verb", ["import", "discover", "qualify", "status"])
def test_non_brief_verb_loads_no_registry_and_no_events_db(
    verb: str, tmp_path: Path
) -> None:
    """Running any non-``brief`` verb in a clean subprocess must not pull the
    publishing registry into ``sys.modules`` and must not create an ``events.db``."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)

    code = textwrap.dedent(
        f"""
        import sys
        from backlink_publisher.cli import comment
        try:
            comment.main([{verb!r}])
        except SystemExit:
            pass
        leaked = "backlink_publisher.publishing.registry" in sys.modules
        print("LEAK" if leaked else "CLEAN")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=30,
    )
    assert "CLEAN" in result.stdout, (
        f"`comment {verb}` pulled publishing.registry into sys.modules "
        f"(stdout={result.stdout!r}, stderr={result.stderr[:300]!r})"
    )
    leaked_dbs = list(tmp_path.rglob("events.db"))
    assert not leaked_dbs, f"`comment {verb}` created an events.db: {leaked_dbs}"


def test_brief_verb_creates_no_events_db(tmp_path: Path) -> None:
    """The ``brief`` verb MAY load the publishing registry into ``sys.modules`` (for the
    LLM provider) — that is accepted. What it must NOT do is touch the events pipeline:
    no ``events.db`` is created. (No-posting is proven structurally by the AST test.)"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(config_dir)

    code = textwrap.dedent(
        """
        from backlink_publisher.cli import comment
        try:
            comment.main(["brief"])  # empty stdin -> no accept rows
        except SystemExit:
            pass
        print("DONE")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT), timeout=30, input="",
    )
    assert "DONE" in result.stdout, f"brief crashed: stderr={result.stderr[:300]!r}"
    leaked_dbs = list(tmp_path.rglob("events.db"))
    assert not leaked_dbs, f"`comment brief` created an events.db: {leaked_dbs}"
