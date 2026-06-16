"""Unit 2 — every in-scope CLI fatal exit emits a typed-error envelope.

Two layers:

1. A **static guard** over the in-scope CLI sources (the "missed one dispatch
   path" tripwire from the plan's risk table). It asserts no process-fatal exit
   bypasses the Unit 1 chokepoint — i.e. there is no bare ``raise SystemExit(N)``
   for a non-zero literal ``N``. Fatal exits must go through ``emit_error`` /
   ``emit_envelope_and_exit`` / ``handle_error`` (all of which emit the envelope).
   ``SystemExit(0)`` (success) and ``SystemExit(exc.code)`` (re-raise propagation
   of an already-emitted envelope) are allowed.

2. **Behavioral** tests that drive the cheap-to-trigger CLIs to a real fatal exit
   and assert stderr carries a parseable envelope with the expected
   ``error_class``/``exit_code`` while stdout stays clean — plus the two
   negative cases (argparse usage error → exit 2 with NO envelope; exit 0 → no
   envelope) that the WebUI bridge must distinguish.
"""

from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path

import pytest

from backlink_publisher._util.error_envelope import parse

# In-scope CLIs whose fatal exits must carry the envelope (plan 2026-05-27-004
# Unit 2). Paths are relative to the package root.
_SRC = Path(__file__).resolve().parents[1] / "src" / "backlink_publisher"
_IN_SCOPE_CLI_FILES = [
    _SRC / "cli" / "validate_backlinks.py",
    _SRC / "cli" / "publish_backlinks" / "__init__.py",  # package after decomposition
    _SRC / "cli" / "_publish_helpers.py",
    _SRC / "cli" / "_resume.py",
    _SRC / "cli" / "plan_backlinks" / "core.py",
    _SRC / "cli" / "report_anchors.py",
    _SRC / "cli" / "equity_ledger.py",
    _SRC / "cli" / "plan_check.py",
    _SRC / "cli" / "plan_gap.py",
]


# --- Layer 1: static guard ------------------------------------------------


def _bare_nonzero_systemexits(source: str) -> list[int]:
    """Return line numbers of ``raise SystemExit(<nonzero literal>)`` nodes.

    Allowed (not returned): ``SystemExit(0)``, ``SystemExit(exc.code)`` and any
    non-literal argument (those route through, or propagate, the chokepoint).
    A string literal counts as a violation too — ``raise SystemExit("msg")``
    exits 1 without an envelope.
    """
    offenders: list[int] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        if not (isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)):
            continue
        if exc.func.id != "SystemExit" or not exc.args:
            continue
        arg = exc.args[0]
        if not isinstance(arg, ast.Constant):
            continue  # SystemExit(exc.code) / computed code → propagation, fine
        if arg.value == 0:
            continue  # success path
        offenders.append(node.lineno)
    return offenders


@pytest.mark.parametrize("path", _IN_SCOPE_CLI_FILES, ids=lambda p: p.name)
def test_no_fatal_exit_bypasses_chokepoint(path):
    offenders = _bare_nonzero_systemexits(path.read_text())
    assert offenders == [], (
        f"{path.name} has bare `raise SystemExit(<nonzero>)` at lines {offenders}; "
        "route fatal exits through emit_error / emit_envelope_and_exit / handle_error "
        "so each emits a typed-error envelope (plan 2026-05-27-004 Unit 2)."
    )


# --- Layer 2: behavioral --------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_dirs(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))


def _run(main, argv, stdin_data: str | None = None):
    """Invoke a CLI ``main`` in-process; return (stdout, stderr, exit_code)."""
    out, err = io.StringIO(), io.StringIO()
    saved = sys.stdout, sys.stderr, sys.stdin
    sys.stdout, sys.stderr = out, err
    if stdin_data is not None:
        sys.stdin = io.StringIO(stdin_data)
    code = 0
    try:
        main(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        elif exc.code is None:
            code = 0
        else:
            err.write(str(exc.code))
            code = 1
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved
    return out.getvalue(), err.getvalue(), code


def test_equity_ledger_bad_stale_days_emits_usage_envelope():
    from backlink_publisher.cli.equity_ledger import main

    out, err, code = _run(main, ["--stale-days", "0"])
    assert code == 1
    env = parse(err)
    assert env is not None
    assert env.error_class == "UsageError"
    assert env.exit_code == 1
    assert "stale-days" in env.message
    assert out == ""  # no JSONL on the failure path


def test_equity_ledger_argparse_usage_error_has_no_envelope():
    # argparse exits 2 *outside* the chokepoint. It collides with
    # InputValidationError's exit 2, so the WebUI must distinguish them: the
    # discriminator is that the argparse path carries NO envelope.
    from backlink_publisher.cli.equity_ledger import main

    _, err, code = _run(main, ["--bogus-flag"])
    assert code == 2
    assert parse(err) is None


def test_validate_aggregated_errors_single_envelope():
    from backlink_publisher.cli.validate_backlinks import main

    # Two rows on an unsupported platform → both land in all_errors → one
    # aggregated fatal exit (code 2), not one per row.
    rows = "\n".join(
        json.dumps({"id": f"r{i}", "platform": "linkedin"}) for i in range(2)
    )
    out, err, code = _run(main, ["--no-validate-url-check"], stdin_data=rows + "\n")
    assert code == 2
    env = parse(err)
    assert env is not None
    assert env.error_class == "InputValidationError"
    assert env.exit_code == 2
    assert "validation failed" in env.message
    # Passing rows would stream to stdout; with zero passing, stdout is empty JSONL.
    assert [l for l in out.splitlines() if l.strip()] == []


def test_validate_success_emits_no_envelope():
    from backlink_publisher.cli.validate_backlinks import main

    payload = {
        "id": "abc123",
        "platform": "medium",
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": (
            "This is a test article about https://example.com and some content here."
        ),
        "links": [
            {"url": "https://example.com", "anchor": "Example",
             "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article",
             "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki",
             "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN",
             "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO",
             "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub",
             "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }
    out, err, code = _run(
        main, ["--no-validate-url-check"], stdin_data=json.dumps(payload) + "\n"
    )
    assert code == 0
    assert parse(err) is None  # success path → no false-positive error envelope
    assert [l for l in out.splitlines() if l.strip()]  # JSONL streamed
