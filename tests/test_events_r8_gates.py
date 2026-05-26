"""R8a/R8b CI gates (test-time, never module-level assertions).

R8a — every kind a writer emits is a registry symbol, not a bare literal:
  (i) literal-ban AST scan of the writer modules (scoped to ``store.append(...)``
      and ``emit(...)`` call sites so ``list.append("x")`` is never flagged), and
  (ii) the 14 kind constants exported by events.kinds all live in KINDS.
R8b — bidirectional reader check: a reader queries only registered kinds AND is
  flagged if it omits a registered publish.* kind without an explicit allowlist
  entry (today ledger intentionally omits publish.unverified).

The R8c content gate (status->outcome pinning) lives in test_events_kind_contract_gate.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backlink_publisher.events import kinds
from backlink_publisher.ledger import sources as ledger_sources

_SRC = Path(__file__).resolve().parents[1] / "src" / "backlink_publisher"

# The modules that emit events.db kinds. The literal-ban is scoped to these —
# a new kind-emitting writer must be added here (and use a registry symbol).
WRITER_MODULES = [
    _SRC / "events" / "projector.py",
    _SRC / "publishing" / "banner_dispatcher.py",
    _SRC / "publishing" / "adapters" / "image_gen" / "caps.py",
]


def _bare_literal_kind_calls(source: str) -> list[str]:
    """Return string literals passed as the first arg to store.append(...) or
    emit(...). Scoped by receiver so list.append("x") is not matched."""
    tree = ast.parse(source)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_store_append = (
            isinstance(func, ast.Attribute)
            and func.attr == "append"
            and isinstance(func.value, ast.Name)
            and func.value.id == "store"
        )
        is_emit = isinstance(func, ast.Name) and func.id == "emit"
        if not (is_store_append or is_emit):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            hits.append(first.value)
    return hits


@pytest.mark.parametrize("module_path", WRITER_MODULES, ids=lambda p: p.name)
def test_no_bare_literal_kind_at_writer_call_sites(module_path):
    # R8a(i): writers must pass a registry symbol, never a bare string literal.
    hits = _bare_literal_kind_calls(module_path.read_text(encoding="utf-8"))
    assert hits == [], (
        f"{module_path.name} passes bare literal kind(s) {hits} to store.append/emit; "
        "use a constant from events.kinds instead (R1a)."
    )


def test_red_path_bare_literal_is_detected():
    # Proves the gate has teeth: an in-scope literal trips it; an out-of-scope
    # list.append("x") does not.
    bad = 'store.append("publish.bogus", {})\nmylist.append("not a kind")\nemit("banner.x", {})'
    assert sorted(_bare_literal_kind_calls(bad)) == ["banner.x", "publish.bogus"]


def test_exported_kind_constants_are_all_registered():
    # R8a(ii): every public kind constant resolves to a member of KINDS.
    for name in dir(kinds):
        if name.isupper() and isinstance(getattr(kinds, name), str) and "." in getattr(kinds, name):
            assert getattr(kinds, name) in kinds.KINDS
    # image_gen_* are underscored (no dot); assert by explicit membership.
    for c in (kinds.IMAGE_GEN_INVOKED, kinds.IMAGE_GEN_CAPPED, kinds.IMAGE_GEN_DISABLED_AUTO):
        assert c in kinds.KINDS


# R8b — reader gate -------------------------------------------------------

#: (reader, kind) pairs where a reader intentionally does NOT consume a
#: registered kind. Each entry is a deliberate, reviewed decision.
ALLOWED_OMISSIONS: set[tuple[str, str]] = {
    ("ledger", "publish.unverified"),  # unconfirmed-liveness link != attempted-confirmed
}

#: The publish.* family a target-attempt reader is expected to consume.
_PUBLISH_FAMILY = {
    kinds.PUBLISH_INTENT,
    kinds.PUBLISH_CONFIRMED,
    kinds.PUBLISH_UNVERIFIED,
    kinds.PUBLISH_FAILED,
}


def test_ledger_queries_only_registered_kinds():
    # R8b: no reader may query an unregistered kind.
    for k in ledger_sources.ATTEMPTED_KINDS:
        assert k in kinds.KINDS


def test_ledger_publish_family_omissions_are_allowlisted():
    # R8b (the directional bit the naive check misses): a registered publish.*
    # kind the ledger does NOT consume must be explicitly allowlisted, so a
    # future publish.* kind can't be silently dropped from the ledger.
    consumed = set(ledger_sources.ATTEMPTED_KINDS)
    for k in _PUBLISH_FAMILY:
        if k not in consumed:
            assert ("ledger", k) in ALLOWED_OMISSIONS, (
                f"ledger omits registered kind {k!r} without an ALLOWED_OMISSIONS entry"
            )
