"""Tests for Wave 1 scoring engine — ScoreStore + formulas."""

from __future__ import annotations

import json

import pytest

from webui_store.score_store import (
    ScoreStore,
    compute_score,
    platform_weight_from_dofollow,
)


# ── Formula unit tests ────────────────────────────────────────────────────


class TestComputeScore:
    def test_base_score_equals_weight_times_dofollow(self):
        s = compute_score(platform_weight=1.0, dofollow_multiplier=1.0)
        assert s == 1.0

    def test_nofollow_platform_is_discounted(self):
        s = compute_score(platform_weight=0.3, dofollow_multiplier=1.0)
        assert s == 0.3

    def test_uncertain_platform(self):
        s = compute_score(platform_weight=0.5, dofollow_multiplier=1.0)
        assert s == 0.5

    def test_survival_bonus_applied(self):
        s = compute_score(
            platform_weight=1.0, dofollow_multiplier=1.0, survival_bonus=1.2
        )
        assert s == 1.2

    def test_all_factors_compound(self):
        s = compute_score(
            platform_weight=0.3, dofollow_multiplier=0.8, survival_bonus=1.2
        )
        assert s == pytest.approx(0.288)


class TestPlatformWeightFromDofollow:
    def test_dofollow_true(self):
        assert platform_weight_from_dofollow(True) == 1.0

    def test_dofollow_false(self):
        assert platform_weight_from_dofollow(False) == 0.3

    def test_dofollow_uncertain(self):
        assert platform_weight_from_dofollow("uncertain") == 0.5


# ── ScoreStore CRUD ───────────────────────────────────────────────────────


class TestScoreStore:
    def test_record_publish_returns_score_id(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        sid = store.record_publish(
            target_url="https://example.com/page",
            channel="medium",
            platform_weight=1.0,
            dofollow_multiplier=1.0,
        )
        assert sid is not None
        data = store.load()
        assert len(data) == 1
        entry = list(data.values())[0]
        assert entry["score"] == 1.0
        assert entry["channel"] == "medium"

    def test_get_total_score_aggregates_all(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        store.record_publish("https://a.com", "medium", 1.0, 1.0)
        store.record_publish("https://b.com", "blogger", 1.0, 1.0)
        store.record_publish("https://c.com", "velog", 0.5, 1.0)
        assert store.get_total_score() == pytest.approx(2.5)

    def test_get_total_score_empty(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        assert store.get_total_score() == 0.0

    def test_channel_breakdown(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        store.record_publish("https://a.com", "medium", 1.0, 1.0)
        store.record_publish("https://b.com", "medium", 1.0, 1.0)
        store.record_publish("https://c.com", "blogger", 0.3, 1.0)
        breakdown = store.get_channel_breakdown()
        assert breakdown["medium"]["count"] == 2
        assert breakdown["medium"]["total"] == pytest.approx(2.0)
        assert breakdown["blogger"]["count"] == 1
        assert breakdown["blogger"]["total"] == pytest.approx(0.3)

    def test_channel_breakdown_empty(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        assert store.get_channel_breakdown() == {}

    def test_get_recent_returns_newest_first(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        id_a = store.record_publish("https://a.com", "medium", 1.0, 1.0)
        id_b = store.record_publish("https://b.com", "blogger", 1.0, 1.0)
        recent = store.get_recent(limit=2)
        assert len(recent) == 2
        assert recent[0]["score_id"] == id_b

    def test_get_recent_respects_limit(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        for i in range(5):
            store.record_publish(f"https://{i}.com", "medium", 1.0, 1.0)
        assert len(store.get_recent(limit=3)) == 3

    def test_update_survival_confirmed(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        sid = store.record_publish("https://a.com", "medium", 1.0, 1.0)
        updated = store.update_survival(sid, alive=True)
        assert updated == pytest.approx(1.2)
        entry = store.load()[sid]
        assert entry["status"] == "survival_confirmed"
        assert entry["survival_bonus"] == 1.2

    def test_update_survival_lost(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        sid = store.record_publish("https://a.com", "medium", 1.0, 1.0)
        updated = store.update_survival(sid, alive=False)
        assert updated == 0.0
        entry = store.load()[sid]
        assert entry["status"] == "survival_lost"
        assert entry["survival_bonus"] == 0.0

    def test_update_survival_unknown_id_returns_none(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        assert store.update_survival("nonexistent", alive=True) is None


# ── Backfill ──────────────────────────────────────────────────────────────


class TestBackfill:
    def test_backfill_from_history_creates_scores(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        history_data = [
            {
                "target_url": "https://a.com",
                "channel": "medium",
                "status": "published",
                "published_at": "2026-06-01T00:00:00",
            },
            {
                "target_url": "https://b.com",
                "channel": "blogger",
                "status": "published",
                "published_at": "2026-06-02T00:00:00",
            },
            {
                "target_url": "https://c.com",
                "channel": "medium",
                "status": "failed",
                "published_at": "2026-06-03T00:00:00",
            },
        ]

        class FakeHistoryStore:
            def load(self):
                return history_data

        count = store.backfill_from_history(
            FakeHistoryStore(),
            platform_weight_fn=lambda ch: 1.0 if ch == "medium" else 0.3,
            dofollow_mult_fn=lambda ch: 1.0,
        )
        assert count == 2  # only "published" status
        assert store.get_total_score() == pytest.approx(1.3)

    def test_backfill_is_idempotent(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")
        history_data = [
            {
                "target_url": "https://a.com",
                "channel": "medium",
                "status": "published",
                "published_at": "2026-06-01T00:00:00",
            },
        ]

        class FakeHistoryStore:
            def load(self):
                return history_data

        count1 = store.backfill_from_history(
            FakeHistoryStore(),
            platform_weight_fn=lambda ch: 1.0,
            dofollow_mult_fn=lambda ch: 1.0,
        )
        count2 = store.backfill_from_history(
            FakeHistoryStore(),
            platform_weight_fn=lambda ch: 1.0,
            dofollow_mult_fn=lambda ch: 1.0,
        )
        assert count1 == 1
        assert count2 == 0  # no new records
        assert store.get_total_score() == 1.0

    def test_backfill_empty_history(self, tmp_path):
        store = ScoreStore(tmp_path / "scores.json")

        class FakeEmptyHistory:
            def load(self):
                return []

        assert store.backfill_from_history(FakeEmptyHistory()) == 0
