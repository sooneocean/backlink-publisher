"""Tests for Wave 1 Watch Service engine."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from webui_store import SeenUrlsStore, QueueStore, HistoryStore
from webui_app.services.watch_service import (
    WatchService,
    _url_hash,
    _parse_manual_urls,
    _check_coverage_via_history,
    _get_dofollow_priority,
)


# ── Mock helpers ──────────────────────────────────────────────────────────────


class MockChannelStatus:
    """Mock channel_status_store with .load() for select_best_channel tests."""

    def __init__(self, data: dict):
        self._data = data

    def load(self):
        return self._data


# ── Helpers ───────────────────────────────────────────────────────────────


class TestUrlHash:
    def test_produces_16_hex_chars(self):
        h = _url_hash("https://example.com/page")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_url_same_hash(self):
        assert _url_hash("https://example.com/a") == _url_hash("https://example.com/a")

    def test_different_url_different_hash(self):
        assert _url_hash("https://a.com") != _url_hash("https://b.com")

    def test_normalizes_trailing_slash(self):
        assert _url_hash("https://a.com/") == _url_hash("https://a.com")


class TestParseManualUrls:
    def test_parses_newline_separated(self):
        text = "https://a.com\nhttps://b.com\n"
        urls = _parse_manual_urls(text)
        assert urls == ["https://a.com", "https://b.com"]

    def test_ignores_blank_lines_and_comments(self):
        text = "\nhttps://a.com\n\n# comment\nhttps://b.com\n"
        urls = _parse_manual_urls(text)
        assert urls == ["https://a.com", "https://b.com"]

    def test_strips_whitespace(self):
        urls = _parse_manual_urls("  https://a.com  \n  https://b.com  ")
        assert urls == ["https://a.com", "https://b.com"]

    def test_returns_empty_for_empty_input(self):
        assert _parse_manual_urls("") == []
        assert _parse_manual_urls("\n\n") == []


class TestCheckCoverageViaHistory:
    def test_covered_when_history_matches(self, tmp_path):
        history = HistoryStore(tmp_path / "history.json")
        history.save([
            {"target_url": "https://a.com", "channel": "medium", "status": "published"},
        ])
        assert _check_coverage_via_history("https://a.com", "medium", history) is True

    def test_not_covered_when_no_match(self, tmp_path):
        history = HistoryStore(tmp_path / "history.json")
        history.save([
            {"target_url": "https://a.com", "channel": "medium", "status": "published"},
        ])
        assert _check_coverage_via_history("https://b.com", "medium", history) is False

    def test_not_covered_wrong_channel(self, tmp_path):
        history = HistoryStore(tmp_path / "history.json")
        history.save([
            {"target_url": "https://a.com", "channel": "medium", "status": "published"},
        ])
        assert _check_coverage_via_history("https://a.com", "blogger", history) is False

    def test_not_covered_when_failed(self, tmp_path):
        history = HistoryStore(tmp_path / "history.json")
        history.save([
            {"target_url": "https://a.com", "channel": "medium", "status": "failed"},
        ])
        assert _check_coverage_via_history("https://a.com", "medium", history) is False

    def test_not_covered_empty_history(self, tmp_path):
        history = HistoryStore(tmp_path / "history.json")
        history.save([])
        assert _check_coverage_via_history("https://a.com", "medium", history) is False


# ── SeenUrlsStore integration ─────────────────────────────────────────────


class TestSeenUrlsStoreIntegration:
    def test_is_new_initially(self, tmp_path):
        seen = SeenUrlsStore(tmp_path / "seen.json")
        assert seen.is_new("https://example.com") is True

    def test_not_new_after_mark_seen(self, tmp_path):
        seen = SeenUrlsStore(tmp_path / "seen.json")
        seen.mark_seen("https://example.com", "manual", "")
        assert seen.is_new("https://example.com") is False

    def test_get_uncovered(self, tmp_path):
        seen = SeenUrlsStore(tmp_path / "seen.json")
        r1 = seen.mark_seen("https://a.com", "manual", "")
        r2 = seen.mark_seen("https://b.com", "sitemap", "https://sitemap.xml")
        seen.update_coverage(r1["url_hash"], "medium", "published")
        uncovered = seen.get_uncovered()
        hashes = [u["url_hash"] for u in uncovered]
        assert r2["url_hash"] in hashes
        assert r1["url_hash"] not in hashes


# ── WatchService detect_new_urls ──────────────────────────────────────────


class MockSeenUrlsStore:
    """Minimal mock that accepts mark_seen/is_new calls."""

    def __init__(self):
        self._seen: set[str] = set()
        self._records: list[dict] = []

    def is_new(self, url):
        h = _url_hash(url)
        return h not in self._seen

    def mark_seen(self, url, source_type, source_origin):
        h = _url_hash(url)
        self._seen.add(h)
        rec = {"url": url, "url_hash": h, "source_type": source_type}
        self._records.append(rec)
        return rec


class TestDetectNewUrls:
    def test_returns_all_when_empty_seen(self):
        service = WatchService(seen_urls_store=MockSeenUrlsStore())
        urls = [{"url": "https://a.com", "source_type": "manual", "source_origin": ""}]
        new = service.detect_new_urls(urls)
        assert len(new) == 1

    def test_detects_duplicate(self):
        seen = MockSeenUrlsStore()
        seen.mark_seen("https://a.com", "manual", "")
        service = WatchService(seen_urls_store=seen)
        urls = [{"url": "https://a.com", "source_type": "manual", "source_origin": ""}]
        assert service.detect_new_urls(urls) == []

    def test_mixed_new_and_duplicate(self):
        seen = MockSeenUrlsStore()
        seen.mark_seen("https://old.com", "manual", "")
        service = WatchService(seen_urls_store=seen)
        urls = [
            {"url": "https://old.com", "source_type": "manual", "source_origin": ""},
            {"url": "https://new.com", "source_type": "sitemap", "source_origin": "sitemap.xml"},
        ]
        new = service.detect_new_urls(urls)
        assert len(new) == 1
        assert new[0]["url"] == "https://new.com"

    def test_deduplicates_same_url_within_one_cycle(self):
        """A URL appearing twice in one candidate batch (e.g. two seed
        sources, or duplicate <loc> entries in a sitemap) must be returned
        only once.

        Regression: ``is_new`` is checked against the *persisted* store, but
        ``mark_seen`` only runs later in ``run_once``. So two identical
        not-yet-seen URLs both passed the filter and ``run_once`` enqueued the
        same target twice in a single cycle (double publish).
        """
        service = WatchService(seen_urls_store=MockSeenUrlsStore())
        urls = [
            {"url": "https://dup.com/page", "source_type": "manual", "source_origin": "list-a"},
            {"url": "https://dup.com/page", "source_type": "sitemap", "source_origin": "sitemap.xml"},
        ]
        new = service.detect_new_urls(urls)
        assert len(new) == 1, f"duplicate URL not collapsed within cycle: {new}"

    def test_dedup_treats_trailing_slash_as_same_url(self):
        """Normalisation (trailing slash / case) is part of the hash, so
        ``https://x.com`` and ``https://x.com/`` are the same target and must
        collapse to one within a cycle."""
        service = WatchService(seen_urls_store=MockSeenUrlsStore())
        urls = [
            {"url": "https://x.com/page", "source_type": "manual", "source_origin": "a"},
            {"url": "https://x.com/page/", "source_type": "manual", "source_origin": "b"},
        ]
        new = service.detect_new_urls(urls)
        assert len(new) == 1


# ── WatchService check_coverage ────────────────────────────────────────────


class MockHistoryStore:
    def __init__(self, data):
        self._data = data

    def load(self):
        return self._data


class TestCheckCoverage:
    def test_covered_via_history(self):
        history = MockHistoryStore([
            {"target_url": "https://a.com", "channel": "medium", "status": "published"},
        ])
        service = WatchService(history_store=history)
        result = service.check_coverage("https://a.com", ["medium", "blogger"])
        assert result["medium"] is True
        assert result["blogger"] is False

    def test_all_uncovered(self):
        history = MockHistoryStore([])
        service = WatchService(history_store=history)
        result = service.check_coverage("https://a.com", ["medium", "blogger"])
        assert result == {"medium": False, "blogger": False}


# ── WatchService select_best_channel ──────────────────────────────────────


class TestSelectBestChannel:
    def test_selects_dofollow_over_nofollow(self):
        service = WatchService()
        channels = [
            {"channel": "blogger", "bound": True, "dofollow_preference": True, "daily_cap": 10,
             "language_whitelist": []},
            {"channel": "medium", "bound": True, "dofollow_preference": False, "daily_cap": 10,
             "language_whitelist": []},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] == "blogger"

    def test_selects_uncertain_over_nofollow(self):
        service = WatchService()
        channels = [
            {"channel": "medium", "bound": True, "dofollow_preference": False, "daily_cap": 10,
             "language_whitelist": []},
            {"channel": "velog", "bound": True, "dofollow_preference": "uncertain", "daily_cap": 10,
             "language_whitelist": []},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] in ("medium", "velog")

    def test_respects_language_whitelist(self):
        service = WatchService()
        channels = [
            {"channel": "medium", "bound": True, "dofollow_preference": True, "daily_cap": 10,
             "language_whitelist": ["ko"]},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] == "medium"

    def test_returns_none_when_no_channels(self):
        service = WatchService()
        assert service.select_best_channel("https://a.com", []) is None

    def test_rejects_expired_channels(self):
        service = WatchService(
            channel_status_store=MockChannelStatus(
                {"medium": {"status": "expired"}, "blogger": {"status": "bound"}}
            )
        )
        channels = [
            {"channel": "medium", "bound": True, "dofollow_preference": True, "daily_cap": 10,
             "language_whitelist": []},
            {"channel": "blogger", "bound": True, "dofollow_preference": True, "daily_cap": 10,
             "language_whitelist": []},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] == "blogger"

    def test_unbound_channel_filtered_out(self):
        service = WatchService()
        channels = [
            {"channel": "medium", "bound": True, "dofollow_preference": True, "daily_cap": 10,
             "language_whitelist": []},
            {"channel": "blogger", "bound": False, "dofollow_preference": True,
             "daily_cap": 10, "language_whitelist": []},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] == "medium"

    def test_equal_priority_load_balances_to_fewer_publishes_today(self):
        """Among equal dofollow-priority channels, the one with FEWER publishes
        today must win (load-balancing), per the documented tie-break.

        Regression: the secondary sort key was ``-today_count`` ascending,
        which selected the BUSIEST channel — the opposite of load-balancing,
        concentrating publishes on one channel and exhausting its cap first.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # blogger published 5x today, medium published 1x today; both dofollow.
        history = MagicMock()
        history.load.return_value = [
            *({"platform": "blogger", "created_at": f"{today} 0{i}:00",
               "status": "published"} for i in range(5)),
            {"platform": "medium", "created_at": f"{today} 09:00",
             "status": "published"},
        ]
        service = WatchService(history_store=history)
        channels = [
            {"channel": "blogger", "bound": True, "dofollow_preference": True,
             "daily_cap": 10, "language_whitelist": []},
            {"channel": "medium", "bound": True, "dofollow_preference": True,
             "daily_cap": 10, "language_whitelist": []},
        ]
        best = service.select_best_channel("https://a.com", channels)
        assert best is not None
        assert best["channel"] == "medium", (
            "expected load-balancing to pick the channel with fewer publishes "
            f"today, got {best['channel']!r}"
        )


# ── WatchService enqueue_publish ──────────────────────────────────────────


class TestEnqueuePublish:
    def test_pushes_to_queue_store(self, tmp_path):
        queue = QueueStore(tmp_path / "queue.json", default_factory=list)
        service = WatchService(queue_store=queue)
        task_id = service.enqueue_publish(
            target_url="https://target.com",
            channel="medium",
            seed_source_info={"type": "manual", "value": ""},
        )
        assert task_id is not None
        tasks = queue.load()
        assert len(tasks) == 1
        assert tasks[0]["urls"] == ["https://target.com"]
        assert tasks[0]["config"]["platform"] == "medium"

    def test_queue_item_has_required_fields(self, tmp_path):
        queue = QueueStore(tmp_path / "queue.json", default_factory=list)
        service = WatchService(queue_store=queue)
        service.enqueue_publish("https://target.com", "medium", {"type": "manual", "value": ""})
        task = queue.load()[0]
        assert "id" in task
        assert task["status"] == "pending"
        assert task["config"]["source"] == "watch_service"


# ── WatchService run_once (smoke test) ────────────────────────────────────


class TestRunOnce:
    def test_run_once_with_no_sources(self, tmp_path):
        seen = SeenUrlsStore(tmp_path / "seen.json")
        history = HistoryStore(tmp_path / "history.json")
        history.save([])
        queue = QueueStore(tmp_path / "queue.json", default_factory=list)
        service = WatchService(
            seen_urls_store=seen,
            history_store=history,
            queue_store=queue,
        )
        report = service.run_once({"seed_sources": [], "channels": []})
        assert report["polled_sources"] == 0
        assert report["new_urls"] == 0

    def test_run_once_with_manual_source(self, tmp_path):
        seen = SeenUrlsStore(tmp_path / "seen.json")
        history = HistoryStore(tmp_path / "history.json")
        history.save([])
        queue = QueueStore(tmp_path / "queue.json", default_factory=list)
        service = WatchService(
            seen_urls_store=seen,
            history_store=history,
            queue_store=queue,
        )
        report = service.run_once({
            "seed_sources": [
                {"type": "manual", "value": "https://target-1.com\nhttps://target-2.com",
                 "enabled": True},
            ],
            "channels": [],
        })
        assert report["polled_sources"] == 1
        assert report["new_urls"] == 2

    def test_run_once_load_balances_across_channels_within_one_cycle(self, tmp_path):
        """Two new URLs discovered in ONE cycle, two equal-priority channels,
        empty history → the URLs must spread one-per-channel.

        Regression: ``select_best_channel`` only read persisted history, never
        the enqueues made earlier in the same ``run_once`` cycle. So every URL
        in a batch saw identical per-channel counts and all routed to the same
        least-busy channel — defeating load-balancing for the common case of a
        sitemap poll yielding many URLs at once.
        """
        seen = SeenUrlsStore(tmp_path / "seen.json")
        history = HistoryStore(tmp_path / "history.json")
        history.save([])
        queue = QueueStore(tmp_path / "queue.json", default_factory=list)
        service = WatchService(
            seen_urls_store=seen,
            history_store=history,
            queue_store=queue,
        )
        report = service.run_once({
            "seed_sources": [
                {"type": "manual", "value": "https://t-1.com\nhttps://t-2.com",
                 "enabled": True},
            ],
            "channels": [
                {"channel": "medium", "bound": True, "dofollow_preference": True,
                 "daily_cap": 10, "language_whitelist": []},
                {"channel": "blogger", "bound": True, "dofollow_preference": True,
                 "daily_cap": 10, "language_whitelist": []},
            ],
        })
        assert report["enqueued"] == 2
        platforms = sorted(t["config"]["platform"] for t in queue.load())
        assert platforms == ["blogger", "medium"], (
            f"both URLs in one cycle hammered a single channel: {platforms}"
        )
