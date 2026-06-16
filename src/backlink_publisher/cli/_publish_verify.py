"""Verification helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Holds the publish-time verification logic and row-reachability checks.
"""

from __future__ import annotations

from typing import Any


def _check_row_reachability(row: dict[str, Any]) -> tuple[bool, str | None]:
    from backlink_publisher.linkcheck.http import MAX_CONCURRENT as _LINKCHECK_MAX_CONCURRENT
    from backlink_publisher.cli._publish_helpers import check_url
    from concurrent.futures import ThreadPoolExecutor, as_completed

    urls = [row.get("target_url", "")]
    for link in row.get("links", []):
        if isinstance(link, dict):
            url = link.get("url")
            if url:
                urls.append(url)
    urls = [u for u in urls if u]
    if not urls:
        return True, None

    if len(urls) == 1:
        ok, _err = check_url(urls[0])
        return (True, None) if ok else (False, urls[0])

    workers = min(_LINKCHECK_MAX_CONCURRENT, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_url, u): u for u in urls}
        first_failure: str | None = None
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                ok, _err = fut.result()
            except Exception:
                ok = False
            if not ok and first_failure is None:
                first_failure = url
                for other in futures:
                    if not other.done():
                        other.cancel()
                break
    if first_failure is not None:
        return False, first_failure
    return True, None


def _do_verify(
    no_verify: bool,
    dry_run: bool,
    result: Any,
    row: dict[str, Any],
) -> tuple[bool, str]:
    # Lazy import so test patches at backlink_publisher.cli._publish_helpers.verify_published work
    from backlink_publisher.cli._publish_helpers import verify_published

    if no_verify or dry_run:
        return True, ""
    verify_url = result.published_url or result.draft_url
    if not verify_url:
        return True, ""
    needs_extended_wait = getattr(result, "post_publish_delay_seconds", 0) > 0
    max_wait = 30 if needs_extended_wait else 10
    required_links = [lnk["url"] for lnk in row.get("links", []) if lnk.get("required")]
    vr = verify_published(
        verify_url,
        title=row.get("title", ""),
        required_link_urls=required_links,
        max_wait=max_wait,
    )
    return vr.ok, vr.reason