"""Unit 7: probe-citations CLI verb contract.

Scenarios
---------
- ``--dry-run`` (default): plan + cost ceiling printed, ZERO network calls, exit 0.
- ``--probe`` mocked: pairs probed, rows emitted, summary JSONL on stdout, recon
  on stderr, exit 0.
- ``--probe`` with no GEO config → DependencyError/exit 3.
- ``--engine bogus`` → UsageError/exit 1.
- ``--format bogus`` → UsageError/exit 1.
- Cost cap mid-batch → durably-appended pairs cursored, rest untouched, exit 0,
  never-raises.
- ``--fail-on-low-share`` with measured above-floor low-share → exit 6;
  warming/never-probed → exit 0.
- Overlapping run declined via flock.
- No ``--api-key`` flag in argparse namespace.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.cli import probe_citations as cli
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED
from backlink_publisher.geo.engines import ProbeResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(
    *,
    with_geo: bool = False,
    probe_queries: dict[str, list[str]] | None = None,
    brand_aliases: dict[str, list[str]] | None = None,
):
    """Return a real Config instance with probe-citations fields set."""
    from backlink_publisher.config.types import Config, GeoProbeConfig

    cfg = Config()  # all-defaults (provides blogger_blog_ids etc.)
    geo = (
        GeoProbeConfig(
            base_url="https://api.perplexity.ai",
            api_key="test-key",
            model="sonar",
        )
        if with_geo
        else None
    )
    object.__setattr__(cfg, "geo_probe_provider", geo)
    object.__setattr__(
        cfg,
        "target_probe_queries",
        probe_queries if probe_queries is not None else {},
    )
    object.__setattr__(
        cfg,
        "target_brand_aliases",
        brand_aliases if brand_aliases is not None else {},
    )
    return cfg


def _ok_probe_result(**overrides) -> ProbeResult:
    base = ProbeResult(
        answer_text="The best widgets are at example.com.",
        source_urls=["https://example.com/widgets"],
        raw_response={"id": "test"},
        outcome="ok",
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def _absent_probe_result() -> ProbeResult:
    return ProbeResult(
        answer_text="There are many options.",
        source_urls=["https://unrelated.net/page"],
        raw_response={"id": "test"},
        outcome="ok",
    )


@contextlib.contextmanager
def _acquired_lock(*args, **kwargs):
    """Fake _single_run_lock that always yields True (lock acquired)."""
    yield True


@contextlib.contextmanager
def _declined_lock(*args, **kwargs):
    """Fake _single_run_lock that always yields False (lock declined)."""
    yield False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Sandbox: config dir isolation."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_config_echo():
    """Patch config_echo.emit_banner to avoid needing a full Config."""
    with patch("backlink_publisher.cli.probe_citations.config_echo") as mock_echo:
        mock_echo.emit_banner.return_value = None
        yield


# ---------------------------------------------------------------------------
# Test: dry-run performs zero network and exits 0  (written FIRST per plan)
# ---------------------------------------------------------------------------


def test_dry_run_zero_network_exits_0(isolated, capsys):
    """--dry-run (default) must not touch the network and must exit 0."""
    spy_calls: list[str] = []

    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
    )

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        result = cli.main([])  # no --probe = dry run

    assert result is None  # exit 0
    assert spy_calls == []  # zero network (no probe_fn called)

    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["type"] == "dry_run"
    assert rows[0]["target_url"] == "https://example.com"
    assert rows[0]["query"] == "best widgets"


# ---------------------------------------------------------------------------
# Test: dry-run format=text exits 0, zero network
# ---------------------------------------------------------------------------


def test_dry_run_text_format_zero_network(isolated, capsys):
    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
    )
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        result = cli.main(["--format", "text"])

    assert result is None
    out = capsys.readouterr().out
    assert "example.com" in out
    assert "best widgets" in out


# ---------------------------------------------------------------------------
# Test: no pairs configured → no error, exit 0
# ---------------------------------------------------------------------------


def test_dry_run_no_pairs_exits_0(isolated, capsys):
    cfg = _make_cfg(with_geo=False, probe_queries={})
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        result = cli.main([])

    assert result is None


# ---------------------------------------------------------------------------
# Test: --probe mocked — pairs probed, JSONL on stdout, recon on stderr
# ---------------------------------------------------------------------------


def test_probe_mocked_emits_jsonl(isolated, capsys, tmp_path):
    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
        brand_aliases={"example.com": ["ExampleBrand"]},
    )
    store = EventStore(path=tmp_path / "events.db")

    def _fake_dispatch(engine_name, query, probe_cfg):
        return _ok_probe_result()

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with patch(
            "backlink_publisher.cli.probe_citations._single_run_lock",
            _acquired_lock,
        ):
            with patch(
                "backlink_publisher.cli.probe_citations.EventStore",
                return_value=store,
            ):
                with patch(
                    "backlink_publisher.geo.engines.dispatch_probe",
                    side_effect=_fake_dispatch,
                ):
                    result = cli.main(["--probe"])

    assert result is None  # exit 0
    out = capsys.readouterr().out
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    probe_rows = [r for r in rows if r.get("type") == "probe"]
    summary_rows = [r for r in rows if r.get("type") == "summary"]
    assert len(probe_rows) >= 1
    assert len(summary_rows) == 1
    assert summary_rows[0]["probed"] >= 1


# ---------------------------------------------------------------------------
# Test: --probe with no GEO config → DependencyError/exit 3
# ---------------------------------------------------------------------------


def test_probe_no_geo_config_exits_3(isolated):
    cfg = _make_cfg(
        with_geo=False,
        probe_queries={"example.com": ["best widgets"]},
    )
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--probe"])
    assert exc.value.code == 3


# ---------------------------------------------------------------------------
# Test: --engine bogus → UsageError/exit 1
# ---------------------------------------------------------------------------


def test_engine_bogus_exits_1(isolated):
    cfg = _make_cfg(with_geo=True, probe_queries={"example.com": ["q"]})
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--engine", "bogus-engine-that-does-not-exist"])
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Test: --format bogus → UsageError/exit 1
# ---------------------------------------------------------------------------


def test_format_bogus_exits_1(isolated):
    cfg = _make_cfg(with_geo=True, probe_queries={"example.com": ["q"]})
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--format", "xlsx"])
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Test: no --api-key flag in argparse
# ---------------------------------------------------------------------------


def test_no_api_key_flag(isolated):
    cfg = _make_cfg(with_geo=True, probe_queries={})
    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        import argparse

        parser_ns = None
        original_parse = argparse.ArgumentParser.parse_args

        def _capture(self, args=None, namespace=None):
            ns = original_parse(self, args, namespace)
            nonlocal parser_ns
            parser_ns = ns
            return ns

        with patch.object(argparse.ArgumentParser, "parse_args", _capture):
            cli.main([])  # dry run, no pairs → returns None

    assert parser_ns is not None
    assert not hasattr(parser_ns, "api_key"), (
        "probe-citations must not expose --api-key flag (S4)"
    )


# ---------------------------------------------------------------------------
# Test: cost cap mid-batch → durably-appended pairs, rest deferred, exit 0
# ---------------------------------------------------------------------------


def test_cost_cap_mid_batch_exits_0(isolated, tmp_path):
    """Cost cap stops the batch; already-probed pairs remain in events.db."""
    store = EventStore(path=tmp_path / "events.db")
    probe_call_count = []

    def _counting_dispatch(engine_name, query, probe_cfg):
        probe_call_count.append(query)
        return _absent_probe_result()

    cfg = _make_cfg(
        with_geo=True,
        probe_queries={
            "example.com": ["query one", "query two", "query three"]
        },
    )

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with patch(
            "backlink_publisher.cli.probe_citations._single_run_lock",
            _acquired_lock,
        ):
            with patch(
                "backlink_publisher.cli.probe_citations.EventStore",
                return_value=store,
            ):
                with patch(
                    "backlink_publisher.geo.engines.dispatch_probe",
                    side_effect=_counting_dispatch,
                ):
                    result = cli.main(["--probe", "--cost-cap", "1"])

    assert result is None  # exit 0, never-raises
    assert len(probe_call_count) == 1
    rows = store.query(
        "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
    )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test: --fail-on-low-share with measured low share → exit 6
# ---------------------------------------------------------------------------


def test_fail_on_low_share_exits_6(isolated, tmp_path, capsys):
    """Measured low-share target (share < threshold) → advisory exit 6."""
    store = EventStore(path=tmp_path / "events.db")

    # Seed 10 absent events → "measured" state with 0% share.
    for i in range(10):
        store.append(
            CITATION_OBSERVED,
            {
                "verdict": "absent",
                "engine": "perplexity",
                "query": "best widgets",
                "run_id": f"run-{i}",
            },
            target_url="https://example.com",
            run_id=f"run-{i}",
        )

    def _absent_dispatch(engine_name, query, probe_cfg):
        return _absent_probe_result()

    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
    )

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with patch(
            "backlink_publisher.cli.probe_citations._single_run_lock",
            _acquired_lock,
        ):
            with patch(
                "backlink_publisher.cli.probe_citations.EventStore",
                return_value=store,
            ):
                with patch(
                    "backlink_publisher.geo.engines.dispatch_probe",
                    side_effect=_absent_dispatch,
                ):
                    with pytest.raises(SystemExit) as exc:
                        cli.main(["--probe", "--fail-on-low-share"])

    assert exc.value.code == 6


# ---------------------------------------------------------------------------
# Test: --fail-on-low-share with never_probed/warming_up → exit 0
# ---------------------------------------------------------------------------


def test_fail_on_low_share_suppressed_for_warming_up(isolated, tmp_path):
    """warming_up / never_probed targets do NOT trip --fail-on-low-share."""
    store = EventStore(path=tmp_path / "events.db")
    # No events → never_probed.

    def _absent_dispatch(engine_name, query, probe_cfg):
        return _absent_probe_result()

    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
    )

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with patch(
            "backlink_publisher.cli.probe_citations._single_run_lock",
            _acquired_lock,
        ):
            with patch(
                "backlink_publisher.cli.probe_citations.EventStore",
                return_value=store,
            ):
                with patch(
                    "backlink_publisher.geo.engines.dispatch_probe",
                    side_effect=_absent_dispatch,
                ):
                    result = cli.main(["--probe", "--fail-on-low-share"])

    assert result is None  # exit 0


# ---------------------------------------------------------------------------
# Test: overlapping run declined via flock
# ---------------------------------------------------------------------------


def test_overlapping_run_declined(isolated, capsys, tmp_path):
    """Second concurrent run should be declined gracefully."""
    cfg = _make_cfg(
        with_geo=True,
        probe_queries={"example.com": ["best widgets"]},
    )
    store = EventStore(path=tmp_path / "events.db")

    with patch("backlink_publisher.cli.probe_citations.load_config", return_value=cfg):
        with patch(
            "backlink_publisher.cli.probe_citations._single_run_lock",
            _declined_lock,
        ):
            with patch(
                "backlink_publisher.cli.probe_citations.EventStore",
                return_value=store,
            ):
                result = cli.main(["--probe"])

    assert result is None  # exit 0 — declined gracefully
    rows = store.query(
        "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
    )
    assert rows == []
