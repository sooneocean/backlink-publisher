"""``comment discover`` — fetch operator-supplied exact public URLs, detect comment
regions, and emit ``CommentTarget`` rows.

Only the **exact** seed URL is fetched: there is no link extraction and no link
following, so one seed yields exactly one target (a crawler that followed links would
turn into an unbounded fetch amplifier and could wander into private space). Fetch
failures degrade to ``comment_open=null`` with a note rather than crashing — ``discover``
is an exit-0 "verdicts are data" verb like ``preflight-targets``.

``page_title`` / ``thread_summary`` are produced here (the named source of those fields
for the LLM brief in a later unit) and are **length-bounded**: the page is untrusted, so
an arbitrarily large blob must never flow downstream toward a prompt. The brief unit
sanitizes again — this is the first, structural bound.

A per-run **seed cap** (:data:`MAX_SEEDS`) bounds a huge or hostile seed file from turning
``discover`` into a self-DoS or a port-scan amplifier against the operator's own network.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from bs4 import BeautifulSoup

from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.comment_outreach import schema
from backlink_publisher.comment_outreach.detect import detect_comment_region
from backlink_publisher.comment_outreach.fetch import fetch_comment_page
from backlink_publisher.content._html_utils import extract_title

discover_logger = PipelineLogger("comment-discover")

#: Max seeds processed per run; seeds beyond this are dropped with a RECON. Bounds a
#: hostile/huge seed file from becoming a self-DoS or port-scan amplifier.
MAX_SEEDS: int = 500
#: Length bounds on the untrusted text fields produced from fetched HTML.
PAGE_TITLE_MAX: int = 200
THREAD_SUMMARY_MAX: int = 500
DISCOVERED_BY: str = "discover"

#: Optional seed fields carried through onto the emitted target when present.
_CARRIED_OPTIONAL = ("anchor_text", "notes", "domain_rank_signal", "indexed", "link_allowed")


def _derive_id(source_url: str) -> str:
    """Stable per-URL id so re-discovering the same page yields the same target id."""
    return "d_" + hashlib.sha1(source_url.encode("utf-8", "ignore")).hexdigest()[:12]


def _bounded_summary(html: bytes, limit: int) -> str:
    """Visible-text summary of *html*, script/style stripped, whitespace-collapsed,
    truncated to *limit*. Untrusted input — kept bounded by construction."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 — never break the exit-0 contract on parse failure
        return ""
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    return text[:limit]


def _build_target(seed: dict[str, Any], now_iso: str) -> dict[str, Any]:
    """Fetch the seed's exact URL, detect the comment region, and assemble a
    ``CommentTarget`` dict. Fetch failure → ``comment_open=null`` + a note."""
    source_url = seed.get("source_url")
    result = fetch_comment_page(source_url) if isinstance(source_url, str) else None
    reason = result.reason if result is not None else "invalid_url"
    html = result.html if result is not None else None

    comment_open = detect_comment_region(html)  # None when not fetchable

    target: dict[str, Any] = {
        "id": seed.get("id") or (_derive_id(source_url) if isinstance(source_url, str) else ""),
        "source_url": source_url,
        "platform": seed.get("platform") or "blog",  # discover is for public web pages
        "topic": seed.get("topic", ""),
        "target_url": seed.get("target_url", ""),
        "comment_open": comment_open,
        "discovered_by": DISCOVERED_BY,
        "discovered_at": now_iso,
    }
    for field in _CARRIED_OPTIONAL:
        if field in seed:
            target[field] = seed[field]

    if html is not None:
        title = extract_title(html)
        if title:
            target["page_title"] = title[:PAGE_TITLE_MAX]
        summary = _bounded_summary(html, THREAD_SUMMARY_MAX)
        if summary:
            target["thread_summary"] = summary
    else:
        # Preserve any operator note, then append the fetch reason.
        existing = target.get("notes")
        note = f"fetch failed: {reason}"
        target["notes"] = f"{existing}; {note}" if existing else note

    return target


def discover_targets(source: Optional[TextIO] = None, dest: Optional[TextIO] = None) -> dict[str, int]:
    """Read seed JSONL, fetch+detect each exact URL, emit ``CommentTarget`` JSONL.

    Returns counts ``{"discovered", "rejected", "fetched"}``. Always exit-0 semantics:
    fetch failures become ``comment_open=null`` targets; only seeds that cannot form a
    valid target (e.g. missing/invalid required fields) are rejected with a RECON reason.
    """
    rows = read_jsonl(source, strict=False)
    now_iso = datetime.now(timezone.utc).isoformat()
    targets: list[dict[str, Any]] = []
    rejected = 0
    fetched = 0
    capped = False

    idx = 0
    for seed in rows:
        idx += 1
        if idx > MAX_SEEDS:
            capped = True
            break
        target = _build_target(seed, now_iso)
        fetched += 1
        errors = schema.validate_comment_target(target)
        if errors:
            rejected += 1
            discover_logger.recon(
                "comment_discover_skip", row=idx, source_url=seed.get("source_url"), reasons=errors
            )
            continue
        targets.append(target)

    write_jsonl(targets, dest)
    discover_logger.recon(
        "comment_discover_summary",
        discovered=len(targets),
        rejected=rejected,
        fetched=fetched,
        seed_cap=MAX_SEEDS,
        capped=capped,
    )
    return {"discovered": len(targets), "rejected": rejected, "fetched": fetched}
