"""Tests for ``scripts/geo_refusal_spike.py`` — the GEO citation refusal-spike
feature-level value gate (Plan 2026-05-29-006 Unit 4).

The spike measures **citation** refusal, not generation refusal (review A2): a
target is excluded when EITHER its refused-rate is high OR its cited-rate among
answered probes is ~0 (answered-about-but-never-cited is equally worthless). The
aggregate D12 verdict recommends Phase-C-only when no target is GEO-eligible.

Network is blocked by the autouse fixtures in ``tests/conftest.py``; every probe
here is injected by monkeypatching ``dispatch_probe`` at the script's consumer
reference, so no real adapter / socket is ever touched.

Credential-shaped fixture values are assembled at runtime via concatenation so
the literal ``api_key = "<value>"`` shape never lands in source (leak-check hook).
"""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from backlink_publisher._util.errors import DependencyError, InputValidationError
from backlink_publisher.config import Config
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.geo import ProbeResult

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "geo_refusal_spike.py"

# Fake GEO key assembled at runtime — never a source literal (leak-check hook).
_GEO_KEY = "pk-" + "geospikefixture"


def _load_script():
    """Import geo_refusal_spike as a fresh module registered in sys.modules.

    Registration is required because the module defines dataclasses that
    reference their own module namespace (``field(default_factory=...)`` +
    forward annotations); the dataclass machinery looks the module up in
    ``sys.modules`` during ``exec_module``.
    """
    spec = importlib.util.spec_from_file_location("geo_refusal_spike_mod", SCRIPT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        # Leave it registered for the duration of the test process; harmless and
        # lets monkeypatch.setattr resolve the consumer reference.
        pass
    return mod


@pytest.fixture
def script():
    return _load_script()


def _cfg(probe_queries: dict[str, list[str]], *, with_provider: bool = True) -> Config:
    provider = (
        GeoProbeConfig(
            base_url="https://api.perplexity.ai",
            api_key=_GEO_KEY,
            model="sonar",
        )
        if with_provider
        else None
    )
    return Config(
        geo_probe_provider=provider,
        target_probe_queries=probe_queries,
    )


def _ok(source_urls: list[str]) -> ProbeResult:
    return ProbeResult(
        answer_text="an answer", source_urls=source_urls, raw_response={}, outcome="ok"
    )


def _refused() -> ProbeResult:
    return ProbeResult(
        answer_text="", source_urls=[], raw_response={}, outcome="refused"
    )


def _install(monkeypatch, script, config: Config, responses) -> None:
    """Wire ``load_config`` and ``dispatch_probe`` at the script's consumer refs.

    ``responses`` is either a single ProbeResult, a callable
    ``(engine, query, cfg) -> ProbeResult`` (may raise), or a list consumed in
    order across all probes.
    """
    monkeypatch.setattr(script, "load_config", lambda _path: config, raising=True)

    if callable(responses):
        fake = responses
    elif isinstance(responses, list):
        seq = iter(responses)

        def fake(engine, query, cfg):  # noqa: ANN001
            return next(seq)
    else:

        def fake(engine, query, cfg):  # noqa: ANN001
            return responses

    monkeypatch.setattr(script, "dispatch_probe", fake, raising=True)


def _run(script, argv=None) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = script.main(argv or [])
    return code, buf.getvalue()


# ── Happy path: mixed responses → correct per-bucket counts + table ──────────


def test_mixed_responses_correct_bucket_counts(monkeypatch, script):
    """A target with one cited, one absent, one refused probe → 1/1/1 counts."""
    config = _cfg({"https://example.com": ["q1", "q2", "q3"]})
    responses = [
        _ok(["https://www.example.com/article"]),  # cited (www-folded host match)
        _ok(["https://other.com/page"]),  # absent (different host)
        _refused(),  # refused
    ]
    _install(monkeypatch, script, config, responses)

    code, out = _run(script)

    # One cited among two answered → 50% cited-rate-among-answered > floor 0% →
    # the single target is eligible → exit 0.
    assert code == 0
    assert "GEO citation refusal-spike" in out
    # Per-target row reflects refused=1 absent=1 cited=1 answered=2.
    assert "| https://example.com | 1 | 1 | 1 | 2 |" in out
    assert "✅ included" in out
    # Aggregate verdict names the eligible target.
    assert "BUILD/ENABLE Phase B" in out


# ── all-refused target → excluded, exit 1 ────────────────────────────────────


def test_all_refused_target_excluded_exit_1(monkeypatch, script):
    config = _cfg({"https://refuser.com": ["q1", "q2"]})
    _install(monkeypatch, script, config, _refused())

    code, out = _run(script)

    assert code == 1
    assert "❌ excluded" in out
    assert "high refusal" in out
    # Sole target excluded → empty eligible set → Phase-C-only verdict (D12).
    assert "SHIP PHASE C ONLY" in out


# ── answered-but-zero-cited target → excluded (A2) ───────────────────────────


def test_answered_but_zero_cited_excluded(monkeypatch, script):
    """Engine answers every probe but never cites the target → worthless → excluded."""
    config = _cfg({"https://ignored.com": ["q1", "q2", "q3"]})
    _install(monkeypatch, script, config, _ok(["https://elsewhere.com/x"]))

    code, out = _run(script)

    assert code == 1
    assert "❌ excluded" in out
    assert "never cited" in out
    # absent=3 cited=0 answered=3, refused=0.
    assert "| https://ignored.com | 0 | 3 | 0 | 3 |" in out
    assert "SHIP PHASE C ONLY" in out


# ── empty target query set → clear message, no crash ─────────────────────────


def test_empty_query_set_for_target_no_crash(monkeypatch, script):
    """A target configured with an empty query list does not crash and is flagged."""
    config = _cfg({"https://noqueries.com": []})
    # dispatch_probe must never be called; a raising stub proves that.
    def _boom(engine, query, cfg):  # noqa: ANN001
        raise AssertionError("dispatch_probe must not be called for empty query set")

    _install(monkeypatch, script, config, _boom)

    code, out = _run(script)

    # No probes ⇒ target cannot be shown eligible ⇒ excluded "no probes" ⇒ exit 1.
    assert code == 1
    assert "no probe queries configured" in out
    assert "_No probe queries configured for this target._" in out


# ── adapter raises → counted as refused, never propagates ────────────────────


def test_adapter_exception_counted_as_refused_never_propagates(monkeypatch, script):
    """An adapter exception for one (target, query) is counted as refused and the
    spike completes all probes (plan never-raises requirement)."""
    config = _cfg({"https://flaky.com": ["q1", "q2"]})

    calls = {"n": 0}

    def _flaky(engine, query, cfg):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transport boom")
        return _ok(["https://flaky.com/p"])  # second probe answers + cites

    _install(monkeypatch, script, config, _flaky)

    # Must not raise out of main().
    code, out = _run(script)

    # Both probes ran (exception did not abort the batch).
    assert calls["n"] == 2
    # First probe → refused (the exception), second → cited.
    assert "| https://flaky.com | 1 | 0 | 1 | 1 |" in out
    # refused-rate = 50% (< default 50% threshold? it's == 0.5 → meets/exceeds →
    # excluded by the high-refusal arm). Verify the refused bucket recorded the
    # exception type tag in the per-probe detail.
    assert "error:RuntimeError" in out


# ── aggregate eligible set empty → Phase-C-only verdict + nonzero exit ───────


def test_aggregate_empty_eligible_set_phase_c_only(monkeypatch, script):
    """Every target refused or never-cited → empty eligible set → Phase-C-only / exit 1."""
    config = _cfg(
        {
            "https://a.com": ["q1", "q2"],
            "https://b.com": ["q1"],
        }
    )
    # a.com all refused; b.com answered but never cites.
    def _resp(engine, query, cfg):  # noqa: ANN001
        return _refused()  # both targets get refusals → both excluded

    _install(monkeypatch, script, config, _resp)

    code, out = _run(script)

    assert code == 1
    assert "SHIP PHASE C ONLY" in out
    assert "DEFER Phase B" in out
    assert "GEO-eligible (included)**: 0" in out


# ── a viable eligible set → include verdict + exit 0 ─────────────────────────


def test_viable_eligible_set_include_exit_0(monkeypatch, script):
    """At least one target gets cited enough → eligible set non-empty → exit 0."""
    config = _cfg(
        {
            "https://good.com": ["q1", "q2"],
            "https://bad.com": ["q1", "q2"],
        }
    )

    def _resp(engine, query, cfg):  # noqa: ANN001
        # good.com is always cited; bad.com is always refused.
        # The fake can't see the target directly, so key off whether the query
        # number is odd/even via a simple round-robin: good first, bad second.
        return _resp.queue.pop(0)

    # good.com probes (2) then bad.com probes (2) — targets iterated sorted.
    # sorted order: bad.com, good.com → so bad first.
    _resp.queue = [
        _refused(),  # bad.com q1
        _refused(),  # bad.com q2
        _ok(["https://good.com/a"]),  # good.com q1 cited
        _ok(["https://good.com/b"]),  # good.com q2 cited
    ]
    _install(monkeypatch, script, config, _resp)

    code, out = _run(script)

    assert code == 0
    assert "BUILD/ENABLE Phase B" in out
    # good.com included, bad.com excluded.
    assert "https://good.com" in out
    # good.com row: refused=0 absent=0 cited=2 answered=2.
    assert "| https://good.com | 0 | 0 | 2 | 2 |" in out
    # bad.com row: refused=2.
    assert "| https://bad.com | 2 | 0 | 0 | 0 |" in out


# ── config preconditions ─────────────────────────────────────────────────────


def test_missing_geo_provider_exits_2(monkeypatch, script):
    config = _cfg({"https://x.com": ["q1"]}, with_provider=False)
    monkeypatch.setattr(script, "load_config", lambda _path: config, raising=True)

    code, out = _run(script)
    assert code == 2  # not a viable/empty verdict — a precondition error.


def test_no_target_probe_queries_exits_2(monkeypatch, script):
    config = _cfg({})
    monkeypatch.setattr(script, "load_config", lambda _path: config, raising=True)

    code, out = _run(script)
    assert code == 2


@pytest.mark.parametrize(
    "exc, expected_code",
    [
        (InputValidationError("bad geo section"), 2),
        (DependencyError("bad toml"), 3),
    ],
)
def test_load_config_error_honors_exit_contract(monkeypatch, script, exc, expected_code):
    """A malformed/invalid config must exit with the documented 0-6 code, not
    crash with an uncaught traceback (ce:review reliability fix)."""
    def _raise(_path):
        raise exc

    monkeypatch.setattr(script, "load_config", _raise, raising=True)

    code, out = _run(script)
    assert code == expected_code
