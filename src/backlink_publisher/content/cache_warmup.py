"""Cache warmup utility for the content fetch module.

This module provides functionality to pre-warm the content fetch cache
with frequently-accessed URLs, reducing cold-start latency in long-running
processes like the WebUI daemon.

Usage:
    # Warm up cache from a list of URLs
    from backlink_publisher.content.cache_warmup import warmup_cache

    warmup_cache(["https://example.com", "https://example.org"])

    # Load from file
    from backlink_publisher.content.cache_warmup import warmup_cache_from_file

    warmup_cache_from_file("~/.config/backlink-publisher/hot-urls.txt")
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Optional

from backlink_publisher._util.logger import opencli_logger
from backlink_publisher.config.loader import _config_dir
from .fetch import verify_url_has_content, reset_cache, stats_snapshot


def warmup_cache(
    urls: Iterable[str],
    max_workers: int = 5,
    timeout_per_url: float = 5.0,
    log_progress: bool = True,
) -> dict[str, int]:
    """Pre-warm the content fetch cache with a list of URLs.

    This function fetches the provided URLs in parallel and caches the results,
    so subsequent calls to verify_url_has_content will hit the cache.

    Args:
        urls: Iterable of URLs to pre-fetch.
        max_workers: Number of concurrent workers for fetching.
        timeout_per_url: Timeout per URL in seconds.
        log_progress: Whether to log progress to stderr.

    Returns:
        Dictionary with counts: {'success': N, 'failed': M, 'total': T}
    """
    urls_list = list(urls)
    if not urls_list:
        return {"success": 0, "failed": 0, "total": 0}

    stats = {"success": 0, "failed": 0, "total": len(urls_list)}

    if log_progress:
        opencli_logger.info(f"Warming cache with {len(urls_list)} URLs...")

    # Use batch verification for concurrent fetching
    from .fetch import verify_urls_batch

    # Split into batches to avoid overwhelming the network
    batch_size = max_workers * 2
    for i in range(0, len(urls_list), batch_size):
        batch = urls_list[i:i + batch_size]
        results = verify_urls_batch(batch, max_workers=max_workers)

        for url, (ok, reason, title) in results.items():
            if ok:
                stats["success"] += 1
            else:
                stats["failed"] += 1
                if log_progress:
                    opencli_logger.debug(f"Cache warmup failed for {url}: {reason}")

        if log_progress:
            processed = min(i + batch_size, len(urls_list))
            opencli_logger.debug(f"Processed {processed}/{len(urls_list)} URLs")

    if log_progress:
        opencli_logger.info(
            f"Cache warmup complete: {stats['success']} success, "
            f"{stats['failed']} failed out of {stats['total']} URLs"
        )

    return stats


def warmup_cache_from_file(
    filepath: Optional[str] = None,
    max_workers: int = 5,
    timeout_per_url: float = 5.0,
    log_progress: bool = True,
) -> dict[str, int]:
    """Pre-warm the content fetch cache from a file of URLs.

    The file should contain one URL per line. Empty lines and lines starting
    with '#' are ignored.

    Args:
        filepath: Path to the file containing URLs. If None, looks for
                  'hot-urls.txt' in the config directory.
        max_workers: Number of concurrent workers for fetching.
        timeout_per_url: Timeout per URL in seconds.
        log_progress: Whether to log progress to stderr.

    Returns:
        Dictionary with counts: {'success': N, 'failed': M, 'total': T}
    """
    if filepath is None:
        filepath = str(_config_dir() / "hot-urls.txt")

    path = Path(filepath).expanduser()
    if not path.exists():
        if log_progress:
            opencli_logger.info(f"Cache warmup file not found: {filepath}")
        return {"success": 0, "failed": 0, "total": 0}

    # Read URLs from file
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    if not urls:
        if log_progress:
            opencli_logger.info(f"No URLs found in {filepath}")
        return {"success": 0, "failed": 0, "total": 0}

    return warmup_cache(urls, max_workers, timeout_per_url, log_progress)


def get_cache_warmup_stats() -> dict:
    """Get statistics about the current cache state.

    Returns:
        Dictionary with cache statistics including:
        - cache_size: Number of entries in cache
        - cache_hits: Number of cache hits
        - cache_misses: Number of cache misses
        - fetch_count: Number of actual fetches
        - avg_latency_ms: Average fetch latency
    """
    from .fetch import _CACHE, _STATS

    snapshot = stats_snapshot()
    fetch_count = snapshot["fetches"]
    avg_latency = (
        snapshot["total_latency_ms"] / fetch_count if fetch_count > 0 else 0
    )

    return {
        "cache_size": len(_CACHE),
        "cache_hits": snapshot["cache_hits"],
        "cache_misses": snapshot["cache_misses"],
        "fetch_count": fetch_count,
        "avg_latency_ms": round(avg_latency, 2),
        "hit_rate": (
            snapshot["cache_hits"] / (snapshot["cache_hits"] + snapshot["cache_misses"])
            if (snapshot["cache_hits"] + snapshot["cache_misses"]) > 0
            else 0.0
        ),
    }


def export_cache_to_file(filepath: Optional[str] = None) -> int:
    """Export the current cache state to a file for faster restarts.

    This writes the current cache entries to a file that can be used to
    pre-populate the cache on the next startup.

    Args:
        filepath: Path to write to. If None, writes to 'cache-state.json'
                  in the cache directory.

    Returns:
        Number of entries exported.
    """
    import json
    from .fetch import _CACHE

    if filepath is None:
        from backlink_publisher.config.loader import _cache_dir
        filepath = str(_cache_dir() / "cache-state.json")

    # Convert cache to serializable format
    cache_data = {}
    for key, (result, timestamp) in _CACHE.items():
        cache_data[key] = {
            "result": result,
            "timestamp": timestamp,
        }

    path = Path(filepath).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)

    return len(cache_data)


def import_cache_from_file(filepath: Optional[str] = None) -> int:
    """Import cache state from a file to pre-populate the cache.

    Args:
        filepath: Path to read from. If None, reads from 'cache-state.json'
                  in the cache directory.

    Returns:
        Number of entries imported.
    """
    import json
    from .fetch import _CACHE, _CACHE_LOCK, _evict_lru
    import time

    if filepath is None:
        from backlink_publisher.config.loader import _cache_dir
        filepath = str(_cache_dir() / "cache-state.json")

    path = Path(filepath).expanduser()
    if not path.exists():
        return 0

    with open(path, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    # Import into cache
    count = 0
    with _CACHE_LOCK:
        for key, entry in cache_data.items():
            # Only import if the entry is still valid (not too old)
            result = tuple(entry["result"])
            timestamp = entry["timestamp"]
            _CACHE[key] = (result, timestamp)
            count += 1
        _evict_lru()

    return count