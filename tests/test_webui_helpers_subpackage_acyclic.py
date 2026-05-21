"""Acyclic-import invariant for helpers/ subpackage — Plan 2026-05-21-007.

Asserts that no module in helpers/ imports from a sibling module except
the one documented edge: url_meta → security (for _TRUTHY_BYPASS only).

This test grows as more sub-modules land. Each Unit adds its module to
KNOWN_MODULES and updates ALLOWED_EDGES if it introduces a new documented edge.
"""
from __future__ import annotations

import ast
from pathlib import Path

HELPERS_DIR = Path(__file__).resolve().parents[1] / "webui_app" / "helpers"

# Sub-modules present after Unit 1. Extend as Units 2-5 land.
KNOWN_MODULES = {"url_meta"}

# Documented inter-sibling edges (from → to). Unit 1 has none yet:
# url_meta currently duplicates _TRUTHY_BYPASS rather than importing security.
# Update this set when the duplication is cleaned up in Unit 3.
ALLOWED_EDGES: set[tuple[str, str]] = set()


def _sibling_imports(module_name: str) -> list[str]:
    """Return sibling module names imported by the given module."""
    src = (HELPERS_DIR / f"{module_name}.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    siblings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            # Match "webui_app.helpers.X" or relative ".X"
            if mod.startswith("webui_app.helpers."):
                sibling = mod.split(".")[-1]
                if sibling in KNOWN_MODULES:
                    siblings.append(sibling)
            elif node.level == 1 and mod in KNOWN_MODULES:
                siblings.append(mod)
    return siblings


def test_no_undocumented_sibling_imports():
    violations = []
    for module in KNOWN_MODULES:
        for sibling in _sibling_imports(module):
            edge = (module, sibling)
            if edge not in ALLOWED_EDGES:
                violations.append(
                    f"helpers/{module}.py → helpers/{sibling}.py "
                    f"(undocumented edge; add to ALLOWED_EDGES if intentional)"
                )
    assert not violations, "\n".join(violations)
