"""Tests for _group_history — run_id-based grouping of publish-history rows."""

from __future__ import annotations

from webui_app.helpers.contexts import _group_history


def _item(run_id=None, status="published", platform="medium"):
    return {
        "id": "abc",
        "run_id": run_id,
        "status": status,
        "platform": platform,
        "language": "zh-CN",
        "created_at": "2026-05-22 15:47",
    }


def test_empty_returns_empty():
    assert _group_history([]) == []


def test_single_item_no_run_id_forms_own_group():
    groups = _group_history([_item(run_id=None)])
    assert len(groups) == 1
    assert groups[0]["is_multi"] is False
    assert len(groups[0]["rows"]) == 1


def test_items_same_run_id_grouped_together():
    items = [_item(run_id="r1"), _item(run_id="r1", status="failed")]
    groups = _group_history(items)
    assert len(groups) == 1
    assert groups[0]["is_multi"] is True
    assert groups[0]["n_total"] == 2
    assert groups[0]["n_published"] == 1
    assert groups[0]["n_failed"] == 1


def test_different_run_ids_form_separate_groups():
    items = [_item(run_id="r1"), _item(run_id="r2")]
    groups = _group_history(items)
    assert len(groups) == 2
    assert all(not g["is_multi"] for g in groups)


def test_non_consecutive_same_run_id_not_merged():
    items = [_item(run_id="r1"), _item(run_id="r2"), _item(run_id="r1")]
    groups = _group_history(items)
    assert len(groups) == 3  # r1 / r2 / r1 — not merged across r2


def test_counts_computed_correctly():
    items = [
        _item(run_id="r1", status="published"),
        _item(run_id="r1", status="drafted"),
        _item(run_id="r1", status="failed"),
        _item(run_id="r1", status="published_unverified"),
    ]
    g = _group_history(items)[0]
    assert g["n_published"] == 1
    assert g["n_drafted"] == 1
    assert g["n_failed"] == 1
    assert g["n_unverified"] == 1
    assert g["n_total"] == 4
    assert g["is_multi"] is True
