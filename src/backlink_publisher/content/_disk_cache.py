"""Disk-persistent cache for content/fetch.py results.

Supplements the in-memory LRU cache (fetch.py:_CACHE) with a file-backed L2
layer so repeated fetches of the same URL across separate CLI invocations skip
the network entirely.

Layout: ``~/.cache/backlink-publisher/content-cache/{aa}/{sha256(url)}.json``
        Two-char shard prefix keeps directory entry counts manageable.

TTL:    Controlled by ``BACKLINK_CONTENT_DISK_CACHE_TTL`` (default 3600s).
        Set to ``0`` or ``BACKLINK_NO_CONTENT_DISK_CACHE=1`` to disable reads.

Thread safety: each cache file is written atomically (tmp + rename). Concurrent
writers for the same URL race benignly — last write wins, result is identical.

Public surface (called from fetch.py only):
  disk_cache_get(url)                -> CheckResult | None
  disk_cache_set(url, result)        -> None
  disk_cache_clear()                 -> int  (files removed)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path  # used by callers of _cache_root, not for home expansion
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backlink_publisher.content.fetch import CheckResult

_DISABLED_ENV = "BACKLINK_NO_CONTENT_DISK_CACHE"
_TTL_ENV = "BACKLINK_CONTENT_DISK_CACHE_TTL"
_DEFAULT_TTL_S: float = 3600.0


def _ttl() -> float:
    try:
        return float(os.environ.get(_TTL_ENV, str(_DEFAULT_TTL_S)))
    except (ValueError, TypeError):
        return _DEFAULT_TTL_S


def _disabled() -> bool:
    return os.environ.get(_DISABLED_ENV, "0") == "1" or _ttl() <= 0


def _cache_root() -> Path:
    from backlink_publisher.config.loader import _cache_dir

    return _cache_dir() / "content-cache"


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return _cache_root() / digest[:2] / f"{digest}.json"


def disk_cache_get(url: str) -> "Optional[CheckResult]":
    """Return cached result for *url*, or ``None`` on miss / expired / error."""
    if _disabled():
        return None
    path = _cache_path(url)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - data.get("ts", 0)
        if age > _ttl():
            return None
        result = data["result"]
        return (result[0], result[1], result[2])
    except (OSError, KeyError, json.JSONDecodeError, IndexError):
        return None


def disk_cache_set(url: str, result: "CheckResult") -> None:
    """Write *result* for *url* to disk cache (atomic tmp + rename)."""
    if _disabled():
        return
    path = _cache_path(url)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(
        {"ts": time.time(), "url": url, "result": list(result)}, ensure_ascii=False
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def disk_cache_clear() -> int:
    """Remove all disk cache files. Returns count of files removed."""
    root = _cache_root()
    if not root.exists():
        return 0
    count = 0
    for p in root.rglob("*.json"):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count
