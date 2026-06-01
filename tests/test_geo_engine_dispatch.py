"""Tests for the GEO probe-engine dispatch-by-name seam (Plan 2026-05-29-006 Unit 3, D1).

Covers: the ``perplexity`` name routes to the adapter; an unknown engine raises
a clear ``UsageError`` (exit 1) — NOT a registry KeyError and NOT a
``DependencyError``; the ``ProbeResult`` dataclass validates its ``outcome``.

Credential-shaped fixture values are assembled at runtime via concatenation so
the literal ``api_key = "<value>"`` shape never lands in source (leak-check hook).
"""

from __future__ import annotations

import pytest

from backlink_publisher._util.errors import UsageError
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.geo import ProbeResult, dispatch_probe
from backlink_publisher.geo import engines as engines_mod

# Fake key assembled at runtime (never a source literal).
_GEO_KEY = "pk-" + "dispatchfixture"


def _cfg() -> GeoProbeConfig:
    return GeoProbeConfig(
        base_url="https://api.perplexity.ai",
        api_key=_GEO_KEY,
        model="sonar",
    )


# ── dispatch routing ─────────────────────────────────────────────────────────


def test_dispatch_routes_to_named_engine(monkeypatch):
    """``perplexity`` dispatches to the registered adapter callable."""
    captured: dict[str, object] = {}

    def fake_perplexity(query, cfg):
        captured["query"] = query
        captured["cfg"] = cfg
        return ProbeResult(
            answer_text="hi", source_urls=[], raw_response={}, outcome="absent"
        )

    # Patch the dict entry so dispatch routes to our stub without network.
    monkeypatch.setitem(engines_mod._ENGINES, "perplexity", fake_perplexity)

    cfg = _cfg()
    result = dispatch_probe("perplexity", "is example.com good?", cfg)

    assert isinstance(result, ProbeResult)
    assert captured["query"] == "is example.com good?"
    assert captured["cfg"] is cfg


def test_unknown_engine_raises_usage_error():
    """An unsupported engine name → ``UsageError`` (exit 1), with a clear message.

    Closed-set operator argument → ``UsageError`` (the
    ``argparse-choices-vs-usage-error`` learning), NOT a ``DependencyError``
    (no external precondition is missing) and NOT a bare ``KeyError``.
    """
    with pytest.raises(UsageError) as exc_info:
        dispatch_probe("gemini", "q", _cfg())

    assert exc_info.value.exit_code == 1
    msg = str(exc_info.value)
    assert "gemini" in msg
    # The error lists the supported engines so the operator can self-correct.
    assert "perplexity" in msg


def test_known_engines_lists_perplexity():
    assert "perplexity" in engines_mod.known_engines()


# ── ProbeResult contract ─────────────────────────────────────────────────────


def test_probe_result_rejects_unknown_outcome():
    """A typo'd outcome is a programmer error — surfaces loudly (not silently)."""
    with pytest.raises(ValueError):
        ProbeResult(
            answer_text="", source_urls=[], raw_response={}, outcome="bogus"
        )


@pytest.mark.parametrize("outcome", ["ok", "refused", "absent", "parse_error"])
def test_probe_result_accepts_each_valid_outcome(outcome):
    r = ProbeResult(
        answer_text="a", source_urls=["https://x"], raw_response={}, outcome=outcome
    )
    assert r.outcome == outcome
    # raw_response is carried for in-memory debugging (D8) — present on the obj.
    assert r.raw_response == {}
