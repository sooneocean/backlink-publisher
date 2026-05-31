"""Unit tests for helpers extracted from plan_check.main.

Covers _extract_plan_date, _emit_error_and_exit, and _resolve_claims.
All tests run without subprocess or filesystem I/O.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from backlink_publisher.cli.plan_check import (
    _emit_error_and_exit,
    _extract_plan_date,
    _resolve_claims,
)


# ── _extract_plan_date ────────────────────────────────────────────────────────


class TestExtractPlanDate:
    def test_date_object_returned_directly(self):
        d = datetime.date(2026, 5, 1)
        assert _extract_plan_date({"date": d}) == d

    def test_datetime_object_coerced_to_date(self):
        dt = datetime.datetime(2026, 5, 1, 12, 0)
        result = _extract_plan_date({"date": dt})
        assert result == datetime.date(2026, 5, 1)

    def test_string_returns_none(self):
        assert _extract_plan_date({"date": "2026-05-01"}) is None

    def test_missing_key_returns_none(self):
        assert _extract_plan_date({}) is None

    def test_none_value_returns_none(self):
        assert _extract_plan_date({"date": None}) is None

    def test_integer_value_returns_none(self):
        assert _extract_plan_date({"date": 20260501}) is None


# ── _emit_error_and_exit ─────────────────────────────────────────────────────


class _FakeExc(Exception):
    exit_code = 2


class TestEmitErrorAndExit:
    def _call(self, *, json_flag=False, json_status="schema_violation"):
        exc = _FakeExc("bad frontmatter")
        with patch("backlink_publisher._util.errors.emit_envelope_and_exit") as mock_exit, \
             patch("backlink_publisher.cli.plan_check._emit_json") as mock_emit_json, \
             patch("backlink_publisher.cli.plan_check._build_json_payload", return_value={"k": "v"}) as mock_build:
            try:
                _emit_error_and_exit(
                    "/tmp/plan.md",
                    datetime.date(2026, 5, 1),
                    exc,
                    stderr_msg="plan-check: schema violation — bad frontmatter",
                    json_status=json_status,
                    json_flag=json_flag,
                )
            except SystemExit:
                pass
            return mock_exit, mock_emit_json, mock_build, exc

    def test_always_calls_emit_envelope(self):
        mock_exit, _, _, exc = self._call()
        mock_exit.assert_called_once_with("_FakeExc", 2, "bad frontmatter")

    def test_json_false_does_not_emit_json(self):
        _, mock_emit_json, _, _ = self._call(json_flag=False)
        mock_emit_json.assert_not_called()

    def test_json_true_emits_json(self):
        _, mock_emit_json, mock_build, _ = self._call(json_flag=True)
        mock_emit_json.assert_called_once()

    def test_json_build_called_with_correct_status(self):
        _, _, mock_build, _ = self._call(json_flag=True, json_status="missing_claims")
        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["status"] == "missing_claims"

    def test_exit_code_from_exc_attribute(self):
        mock_exit, *_ = self._call()
        assert mock_exit.call_args[0][1] == 2


# ── _resolve_claims ───────────────────────────────────────────────────────────


def _make_claims(paths=(), shas=()):
    c = MagicMock()
    c.paths = list(paths)
    c.shas = list(shas)
    return c


class TestResolveClaims:
    def test_all_present_returns_empty_lists(self):
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main", return_value=(True, "exists")), \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main", return_value=(True, "reachable")):
            pm, su = _resolve_claims(_make_claims(["src/a.py"], ["abc1234"]))
        assert pm == []
        assert su == []

    def test_missing_path_collected(self):
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main", return_value=(False, "missing")), \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main", return_value=(True, "reachable")):
            pm, su = _resolve_claims(_make_claims(["src/gone.py"], []))
        assert pm == ["src/gone.py"]
        assert su == []

    def test_unreachable_sha_collected(self):
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main", return_value=(True, "exists")), \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main", return_value=(False, "unreachable")):
            pm, su = _resolve_claims(_make_claims([], ["dead1234"]))
        assert pm == []
        assert su == ["dead1234"]

    def test_git_error_counts_as_drift(self):
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main", return_value=(False, "git_error")), \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main", return_value=(False, "git_error")):
            pm, su = _resolve_claims(_make_claims(["src/x.py"], ["abc1234"]))
        assert pm == ["src/x.py"]
        assert su == ["abc1234"]

    def test_empty_claims_returns_empty_lists(self):
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main") as mock_p, \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main") as mock_s:
            pm, su = _resolve_claims(_make_claims([], []))
        mock_p.assert_not_called()
        mock_s.assert_not_called()
        assert pm == [] and su == []

    def test_multiple_paths_mixed(self):
        side_effects = [(True, "exists"), (False, "missing"), (True, "exists")]
        with patch("backlink_publisher.cli.plan_check._path_exists_on_main", side_effect=side_effects), \
             patch("backlink_publisher.cli.plan_check._sha_reachable_from_main", return_value=(True, "reachable")):
            pm, su = _resolve_claims(_make_claims(["a.py", "b.py", "c.py"], []))
        assert pm == ["b.py"]
