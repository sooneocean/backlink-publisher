"""Unit tests for cli/_dedup_ops.py.

Covers the pure and near-pure functions:
  - _parse_older_than   (pure, no I/O)
  - _resolve_to_state   (pure, emit_error raises SystemExit on bad input)
  - _adjudicate_one     (store/audit_log via mocks)
  - load_force_manifest (file I/O + DedupStore token via mocks + tmp_path)
  - _do_list_uncertain  (capsys + DedupStore mock)
  - _do_forget          (store + audit_log mocks, args mock)

Each test class patches only what it needs; DedupStore is never opened for real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

import backlink_publisher.idempotency.audit_log  # ensure submodule is in sys.modules before patching

from backlink_publisher.cli._dedup_ops import (
    _adjudicate_one,
    _do_forget,
    _do_list_uncertain,
    _parse_older_than,
    _resolve_to_state,
    load_force_manifest,
)
from backlink_publisher.idempotency import DedupRecord


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_record(
    *,
    platform: str = "velog",
    target_url: str = "https://example.com/post",
    account: str = "default",
    state: str = "uncertain",
    run_id: str | None = "run-abc",
) -> DedupRecord:
    return DedupRecord(
        platform=platform,
        account=account,
        target_url=target_url,
        state=state,  # type: ignore[arg-type]
        verify_ok=None,
        live_url=None,
        run_id=run_id,
        owner_pid=None,
        owner_run_id=None,
        owner_started_at=None,
        updated_at=0.0,
    )


def _args(**kw) -> SimpleNamespace:
    """Build a minimal args namespace with sensible defaults."""
    defaults = dict(
        to="succeeded",
        reason="test reason",
        forget=None,
        platform=None,
        adjudicate_uncertain=None,
        confirm=None,
        list_affected=False,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── _parse_older_than ─────────────────────────────────────────────────────────


class TestParseOlderThan:
    def test_none_returns_none(self) -> None:
        assert _parse_older_than(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_older_than("") is None

    def test_seconds(self) -> None:
        assert _parse_older_than("3600s") == 3600.0

    def test_minutes(self) -> None:
        assert _parse_older_than("90m") == 90 * 60

    def test_hours(self) -> None:
        assert _parse_older_than("24h") == 24 * 3600

    def test_days(self) -> None:
        assert _parse_older_than("7d") == 7 * 86400

    def test_one_second(self) -> None:
        assert _parse_older_than("1s") == 1.0

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_older_than("7x")
        assert exc.value.code == 1

    def test_decimal_not_allowed(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_older_than("1.5d")
        assert exc.value.code == 1

    def test_bare_unit_no_digits(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_older_than("d")
        assert exc.value.code == 1

    def test_plain_number_no_unit(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _parse_older_than("100")
        assert exc.value.code == 1


# ── _resolve_to_state ─────────────────────────────────────────────────────────


class TestResolveToState:
    def test_succeeded_maps_to_done(self) -> None:
        assert _resolve_to_state(_args(to="succeeded", reason="ok")) == "done"

    def test_failed_maps_to_failed(self) -> None:
        assert _resolve_to_state(_args(to="failed", reason="ok")) == "failed"

    def test_unknown_to_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _resolve_to_state(_args(to="pending", reason="ok"))
        assert exc.value.code == 1

    def test_missing_reason_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _resolve_to_state(_args(to="succeeded", reason=None))
        assert exc.value.code == 1


# ── _adjudicate_one ───────────────────────────────────────────────────────────


class TestAdjudicateOne:
    def _run(self, store, key, to_state="done", reason="manual fix", run_id="run-1"):
        with patch("backlink_publisher.idempotency.audit_log") as mock_log:
            _adjudicate_one(store, key, to_state, reason, run_id=run_id)
            return mock_log

    def test_calls_store_transition(self) -> None:
        store = MagicMock()
        key = MagicMock(platform="velog", target_url="https://x.com", account="default")
        self._run(store, key)
        store.transition.assert_called_once_with(
            key, "done", run_id="run-1", expect_from=("uncertain",)
        )

    def test_calls_audit_log_append_entry(self) -> None:
        store = MagicMock()
        key = MagicMock(platform="velog", target_url="https://x.com", account="default")
        mock_log = self._run(store, key, to_state="failed", reason="confirmed gone", run_id="run-2")
        mock_log.append_entry.assert_called_once()
        _, kw = mock_log.append_entry.call_args
        assert kw["action"] == "adjudicate"
        assert kw["to_state"] == "failed"
        assert kw["reason"] == "confirmed gone"

    def test_value_error_from_transition_propagates(self) -> None:
        store = MagicMock()
        store.transition.side_effect = ValueError("row changed mid-flight")
        key = MagicMock(platform="velog", target_url="https://x.com", account="default")
        with patch("backlink_publisher.idempotency.audit_log"):
            with pytest.raises(ValueError, match="row changed"):
                _adjudicate_one(store, key, "done", "ok", run_id=None)


# ── load_force_manifest ───────────────────────────────────────────────────────


class TestLoadForceManifest:
    """load_force_manifest validates guards then returns forced key tuples."""

    _TARGET = "https://example.com/post"

    def _jsonl_line(self, *, force: bool, token: str = "tok123") -> str:
        return json.dumps(
            {
                "platform": "velog",
                "target_url": self._TARGET,
                "force": force,
                "store_token": token,
            }
        )

    def test_no_reason_raises(self, tmp_path) -> None:
        p = tmp_path / "manifest.jsonl"
        p.write_text("")
        with pytest.raises(SystemExit) as exc:
            load_force_manifest(str(p), confirm=0, reason=None)
        assert exc.value.code == 1

    def test_missing_file_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            load_force_manifest("/nonexistent/path/manifest.jsonl", confirm=0, reason="ok")
        assert exc.value.code == 1

    def test_empty_file_with_confirm_zero_ok(self, tmp_path) -> None:
        p = tmp_path / "manifest.jsonl"
        p.write_text("")
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.store_token.return_value = "tok"
            result = load_force_manifest(str(p), confirm=0, reason="ok")
        assert result == set()

    def test_token_mismatch_raises(self, tmp_path) -> None:
        p = tmp_path / "manifest.jsonl"
        p.write_text(self._jsonl_line(force=True, token="stale-token"))
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.store_token.return_value = "current-token"
            with pytest.raises(SystemExit) as exc:
                load_force_manifest(str(p), confirm=1, reason="ok")
        assert exc.value.code == 1

    def test_count_mismatch_raises(self, tmp_path) -> None:
        p = tmp_path / "manifest.jsonl"
        p.write_text(self._jsonl_line(force=True))
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.store_token.return_value = "tok123"
            with pytest.raises(SystemExit) as exc:
                load_force_manifest(str(p), confirm=99, reason="ok")
        assert exc.value.code == 1

    def test_happy_path_returns_key_set(self, tmp_path) -> None:
        from backlink_publisher.idempotency import DedupKey

        p = tmp_path / "manifest.jsonl"
        p.write_text(self._jsonl_line(force=True))
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.store_token.return_value = "tok123"
            result = load_force_manifest(str(p), confirm=1, reason="re-publish ok")
        expected_key = DedupKey(platform="velog", target_url=self._TARGET)
        assert expected_key.as_tuple() in result
        assert len(result) == 1

    def test_non_force_rows_skipped(self, tmp_path) -> None:
        p = tmp_path / "manifest.jsonl"
        lines = "\n".join([
            self._jsonl_line(force=False),
            self._jsonl_line(force=True),
        ])
        p.write_text(lines)
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.store_token.return_value = "tok123"
            result = load_force_manifest(str(p), confirm=1, reason="ok")
        assert len(result) == 1


# ── _do_list_uncertain ────────────────────────────────────────────────────────


class TestDoListUncertain:
    def test_no_rows_prints_empty_message(self, capsys) -> None:
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.list_by_state.return_value = []
            _do_list_uncertain(_args(platform=None))
        out = capsys.readouterr().out
        assert "No uncertain" in out

    def test_rows_printed_as_table(self, capsys) -> None:
        rec = _make_record(platform="medium", target_url="https://medium.com/@u/slug", run_id="run-xyz")
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.list_by_state.return_value = [rec]
            _do_list_uncertain(_args(platform="medium"))
        out = capsys.readouterr().out
        assert "medium" in out
        assert "uncertain" in out

    def test_platform_filter_passed_to_store(self) -> None:
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore:
            MockStore.return_value.list_by_state.return_value = []
            _do_list_uncertain(_args(platform="telegraph"))
        MockStore.return_value.list_by_state.assert_called_once_with(
            "uncertain", platform="telegraph"
        )


# ── _do_forget ────────────────────────────────────────────────────────────────


class TestDoForget:
    def _forget_args(self, platform="velog", target_url="https://example.com/post", **kw):
        return _args(forget=(platform, target_url), **kw)

    def test_no_reason_raises(self) -> None:
        with patch("backlink_publisher.idempotency.DedupStore"):
            with pytest.raises(SystemExit) as exc:
                _do_forget(self._forget_args(reason=None))
        assert exc.value.code == 1

    def test_glob_in_platform_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _do_forget(self._forget_args(platform="*", reason="test"))
        assert exc.value.code == 1

    def test_glob_in_target_url_raises(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _do_forget(self._forget_args(target_url="https://example.com/*", reason="test"))
        assert exc.value.code == 1

    def test_forget_absent_key_prints_audit_message(self, capsys) -> None:
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore, \
             patch("backlink_publisher.idempotency.audit_log") as mock_log:
            MockStore.return_value.get.return_value = None
            _do_forget(self._forget_args(reason="cleanup"))
        err = capsys.readouterr().err
        assert "already absent" in err
        mock_log.append_entry.assert_called_once()

    def test_forget_existing_key_prints_cleared_message(self, capsys) -> None:
        rec = _make_record(state="done")
        with patch("backlink_publisher.idempotency.DedupStore") as MockStore, \
             patch("backlink_publisher.idempotency.audit_log") as mock_log:
            MockStore.return_value.get.return_value = rec
            _do_forget(self._forget_args(reason="cleanup"))
        err = capsys.readouterr().err
        assert "cleared" in err
        mock_log.append_entry.assert_called_once()
        _, kw = mock_log.append_entry.call_args
        assert kw["from_state"] == "done"
        assert kw["to_state"] == "absent"
