"""Watch service — automated seed-source polling and publish enqueueing.

Polls configured seed sources (sitemaps, manual lists, bookmark files) on a
configurable interval, detects newly discovered URLs, checks coverage across
bound channels, selects the best channel for each uncovered target, and pushes
publish tasks to the existing queue_store for execution by the queue worker.

Usage (called by APScheduler job)::

    service = WatchService(seen_urls_store, history_store, queue_store, ...)
    report = service.run_once(wizard_config)
"""

from __future__ import annotations

import hashlib
import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from backlink_publisher._util.logger import plan_logger

# ── Helpers ──────────────────────────────────────────────────────────────


def _url_hash(url: str) -> str:
    """Return first 16 hex chars of SHA-256 of the normalized URL."""
    normalized = url.strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sitemap(xml_content: bytes) -> list[str]:
    """Extract all ``<loc>`` URLs from a sitemap XML document."""
    root = ET.fromstring(xml_content)
    # Sitemap namespace
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.iterfind(".//sm:loc", ns):
        text = (loc.text or "").strip()
        if text:
            urls.append(text)
    # Also handle sitemap index (nested sitemaps -> return as discovered URLs)
    # For simplicity, only extract leaf URLs in v1.
    return urls


def _parse_bookmark_file(html_content: str) -> list[str]:
    """Extract all ``href`` URLs from an HTML bookmark file.

    Handles the Netscape bookmark format exported by all major browsers
    (``<DT><A HREF="...">``).
    """
    import re

    urls = re.findall(r'HREF="([^"]+)"', html_content, re.IGNORECASE)
    return [u.strip() for u in urls if u.strip()]


# ── Coverage helpers ─────────────────────────────────────────────────────


def _check_coverage_via_history(
    target_url: str,
    channel: str,
    history_store,
) -> bool:
    """Check history_store for any successful publish of target_url on channel."""
    h = _url_hash(target_url)
    try:
        entries = history_store.load()
    except Exception:
        return False
    for entry in entries:
        if isinstance(entry, dict) and entry.get("target_url"):
            if _url_hash(entry.get("target_url", "")) == h:
                entry_channel = entry.get("channel") or entry.get("platform") or ""
                if entry_channel == channel and entry.get("status") in (
                    "published",
                    "drafted",
                    "published_unverified",
                ):
                    return True
    return False


def _get_dofollow_priority(channel: str) -> int:
    """Return a numeric priority tier for dofollow status (lower = better).

    Tier 0: dofollow=True
    Tier 1: dofollow="uncertain"
    Tier 2: dofollow=False
    Tier 9: unknown
    """
    try:
        from backlink_publisher.publishing.registry import dofollow_status

        ds = dofollow_status(channel)
        if ds is True:
            return 0
        if isinstance(ds, str) and ds == "uncertain":
            return 1
        if ds is False:
            return 2
    except Exception:
        pass
    return 9


def _today_publish_count(channel: str, history_store) -> int:
    """Return count of publishes to *channel* today (for daily cap check)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        entries = history_store.load()
    except Exception:
        return 0
    count = 0
    for entry in entries:
        if isinstance(entry, dict) and entry.get("platform") == channel:
            created = entry.get("created_at", "")
            if created.startswith(today):
                count += 1
    return count


# ── Polling helpers ──────────────────────────────────────────────────────


def _fetch_sitemap(url: str, timeout: int = 30) -> list[str]:
    """Fetch a sitemap URL and extract all leaf URLs."""
    req = Request(url, headers={"User-Agent": "backlink-publisher-watch/1.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
    except Exception as exc:
        plan_logger.warn("watch_sitemap_fetch_failed", url=url, error=str(exc))
        return []
    try:
        return _parse_sitemap(content)
    except ET.ParseError as exc:
        plan_logger.warn("watch_sitemap_parse_failed", url=url, error=str(exc))
        return []


def _parse_manual_urls(text: str) -> list[str]:
    """Parse newline-separated manual target URLs, skip empties and comments."""
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _fetch_bookmark_urls(file_path: str) -> list[str]:
    """Read a bookmark HTML file and extract all URLs."""
    from pathlib import Path

    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        plan_logger.warn("watch_bookmark_read_failed", path=file_path, error=str(exc))
        return []
    return _parse_bookmark_file(content)


# ── Report type ──────────────────────────────────────────────────────────


class RunReport(dict):
    """Structured report from a single watch cycle, dict-serialisable."""

    def __init__(self, **kwargs) -> None:
        super().__init__(
            polled_sources=0,
            urls_found=0,
            new_urls=0,
            already_covered=0,
            enqueued=0,
            uncovered=[],
            errors=[],
        )
        self.update(kwargs)


# ── Main service ─────────────────────────────────────────────────────────


class WatchService:
    """Poll seed sources, detect new URLs, check coverage, enqueue publishes.

    All dependencies are injected (stores, engines) so the class is pure
    business logic and fully testable with mocks.
    """

    def __init__(
        self,
        seen_urls_store=None,
        history_store=None,
        queue_store=None,
        channel_status_store=None,
    ) -> None:
        self._seen = seen_urls_store
        self._history = history_store
        self._queue = queue_store
        self._channel_status = channel_status_store

    # ── Polling ──────────────────────────────────────────────────────────

    def poll_source(self, source: dict) -> list[dict]:
        """Poll a single seed source. Returns list of ``{"url", "source_type", "source_origin"}``."""
        stype = source.get("type", "")
        value = source.get("value", "")
        origin = source.get("label") or value

        if stype == "sitemap":
            urls = _fetch_sitemap(value)
        elif stype == "manual":
            urls = _parse_manual_urls(value)
        elif stype == "bookmark":
            urls = _fetch_bookmark_urls(value)
        else:
            plan_logger.warn("watch_unknown_source_type", type=stype)
            return []

        return [
            {"url": u, "source_type": stype, "source_origin": origin}
            for u in urls
        ]

    def poll_all_sources(self, seed_sources: list[dict]) -> list[dict]:
        """Poll every configured seed source.

        Returns a flat list of ``{"url", "source_type", "source_origin"}``.
        Errors per source are logged and collected in a separate list.
        """
        all_candidates: list[dict] = []
        for source in seed_sources:
            if not source.get("enabled", True):
                continue
            try:
                candidates = self.poll_source(source)
                all_candidates.extend(candidates)
            except Exception as exc:
                plan_logger.error(
                    "watch_source_poll_error",
                    source=source.get("value"),
                    error=str(exc),
                )
        return all_candidates

    # ── Detection ────────────────────────────────────────────────────────

    def detect_new_urls(self, candidates: list[dict]) -> list[dict]:
        """Return only candidates whose URL has never been seen before."""
        new = []
        for c in candidates:
            url = c["url"]
            if self._seen.is_new(url):
                new.append(c)
        return new

    # ── Coverage ─────────────────────────────────────────────────────────

    def check_coverage(self, target_url: str, channels: list[str]) -> dict[str, bool]:
        """Return ``{channel: is_covered}`` for each channel.

        Checks history_store for each channel — has this target URL been
        successfully published to this channel before?
        (Equity-ledger integration deferred — the full ledger build is
        expensive for per-URL checks; history_store is sufficient for v1.)
        """
        result: dict[str, bool] = {}

        # history_store check
        for ch in channels:
            result[ch] = _check_coverage_via_history(target_url, ch, self._history)
        return result

    # ── Channel selection ────────────────────────────────────────────────

    def select_best_channel(
        self,
        target_url: str,
        channels_config: list[dict],
    ) -> dict | None:
        """Select the best channel for *target_url* using priority rules.

        Algorithm (R7):
        1. Filter: only bound channels
        2. Filter: channel status is not expired
        3. Filter: language whitelist match
        4. Sort by: dofollow priority → daily cap headroom
        5. Return best candidate or None
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        qualified: list[tuple[int, int, dict]] = []

        for ch_cfg in channels_config:
            ch_name = ch_cfg.get("channel", "")
            if not ch_cfg.get("bound", False):
                continue

            # Check channel status (expired?)
            if self._channel_status is not None:
                try:
                    status_data = self._channel_status.load()
                    ch_status = status_data.get(ch_name, {})
                    if ch_status.get("status") == "expired":
                        plan_logger.debug(
                            "watch_channel_expired_skipped", channel=ch_name
                        )
                        continue
                except Exception:
                    pass

            # Language filter
            whitelist = ch_cfg.get("language_whitelist", [])
            if whitelist:
                # In v1 we skip language filtering for simplicity; the
                # target language is unknown at this stage. Future versions
                # can infer language from the target URL content.
                pass

            # Daily cap
            daily_cap = ch_cfg.get("daily_cap", 10)
            today_count = _today_publish_count(ch_name, self._history)
            headroom = daily_cap - today_count
            if headroom <= 0:
                continue  # cap exhausted

            # Dofollow priority (lower = better)
            priority = _get_dofollow_priority(ch_name)
            qualified.append((priority, today_count, ch_cfg))

        if not qualified:
            return None

        # Sort by priority asc, then by today_count asc (load-balancing
        # across equal-priority channels: pick the one with fewer publishes
        # today)
        qualified.sort(key=lambda x: (x[0], x[1]))
        return qualified[0][2]

    # ── Enqueue ──────────────────────────────────────────────────────────

    def enqueue_publish(
        self,
        target_url: str,
        channel: str,
        seed_source_info: dict,
    ) -> str:
        """Push a publish task to queue_store. Returns task ID."""
        task_id = str(uuid.uuid4())[:8]
        now = _now_iso()

        task = {
            "id": task_id,
            "status": "pending",
            "created_at": now,
            "urls": [target_url],
            "config": {
                "platform": channel,
                "target_language": "zh-CN",
                "url_mode": "A",
                "publish_mode": "draft",
                "custom_title": "",
                "custom_tags": "",
                "source": "watch_service",
                "source_type": seed_source_info.get("source_type", "unknown"),
            },
        }

        def _enqueue(tasks: list) -> list:
            tasks.append(task)
            return tasks

        self._queue.update(_enqueue)
        plan_logger.recon(
            "watch_enqueued",
            task_id=task_id,
            channel=channel,
            target_url=target_url,
        )
        return task_id

    # ── Full cycle ───────────────────────────────────────────────────────

    def run_once(self, wizard_config: dict) -> RunReport:
        """Execute one full watch cycle.

        1. Poll all seed sources
        2. Detect new URLs
        3. Check coverage for each new URL
        4. Select best channel for uncovered targets
        5. Enqueue publish tasks
        """
        report = RunReport()
        seed_sources = wizard_config.get("seed_sources", [])
        channels_cfg = wizard_config.get("channels", [])
        channel_names = [c["channel"] for c in channels_cfg if c.get("bound")]

        report["polled_sources"] = len(seed_sources)

        # Step 1: Poll all sources
        candidates = self.poll_all_sources(seed_sources)
        report["urls_found"] = len(candidates)

        if not candidates:
            return report

        # Step 2: Find new URLs
        new_urls = self.detect_new_urls(candidates)
        report["new_urls"] = len(new_urls)

        # Step 3-5: For each new URL, check coverage and enqueue
        for candidate in new_urls:
            url = candidate["url"]

            # Mark as seen immediately (prevents re-enqueue on next cycle)
            self._seen.mark_seen(
                url=url,
                source_type=candidate["source_type"],
                source_origin=candidate["source_origin"],
            )

            # Step 3: Check coverage
            coverage = self.check_coverage(url, channel_names)
            covered_any = any(coverage.values())

            if covered_any:
                report["already_covered"] += 1
                h = _url_hash(url)
                for ch, cov in coverage.items():
                    if cov:
                        self._seen.update(
                            lambda d: {
                                **d,
                                h: {
                                    **d.get(h, {}),
                                    "coverage": {
                                        **d.get(h, {}).get("coverage", {}),
                                        ch: "published",
                                    },
                                },
                            }
                        )
                continue

            # Step 4: Select best channel
            best = self.select_best_channel(url, channels_cfg)
            if best is None:
                report["uncovered"].append(
                    {"url": url, "reason": "no_suitable_channel"}
                )
                continue

            # Step 5: Enqueue
            self.enqueue_publish(url, best["channel"], candidate)
            report["enqueued"] += 1

        return report
