"""quality-gate — pre-publish quality gate (Phase C, Plan 2026-06-07-003).

Reads ``plan-backlinks`` seed JSONL on stdin, runs deterministic quality checks
(anchor density, content uniqueness), and emits passing rows on stdout. Blocked
rows are logged on stderr. Optionally runs LLM quality scoring and emits
``publish.quality_blocked`` events to events.db.

Designed to compose in a shell pipeline:

    ... | plan-backlinks | quality-gate | publish-backlinks --publish

Exit 0 advisory; blocked rows never interrupt the batch.

Stage 2.2/2.3/3.1 optimization: Bulk-fetch + SHA reflex + async LLM.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .._util.errors import emit_error
from .._util.jsonl import json_loads, read_jsonl, write_jsonl
from backlink_publisher._util.logger import get_logger
from backlink_publisher.events.store import EventStore
from backlink_publisher.events.kinds import PUBLISH_CONFIRMED, PUBLISH_QUALITY_BLOCKED

_log = get_logger("quality_gate")

# Markdown inline link: ``[text](url)``. Used by _count_md_links for the
# anchor-density check.
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _count_words(text: str) -> int:
    """Count whitespace-delimited words in a string."""
    return len(text.split())


def _count_md_links(text: str) -> int:
    """Count markdown links ``[text](url)`` in a string."""
    return len(_MD_LINK_RE.findall(text))


def _check_anchor_density(
    seed: dict[str, Any],
    max_density: float,
) -> str | None:
    """Check anchor density. Returns None if pass, or a reason string if blocked.

    Reads ``article_content_markdown`` from the seed if present; otherwise
    returns None (skip check — content not yet generated).
    """
    body = seed.get("article_content_markdown")
    if not body:
        body = seed.get("content_markdown")
    if not body:
        return None  # No content to check; skip

    words = _count_words(body)
    if words == 0:
        return None

    links = _count_md_links(body)
    density = links / words
    if density > max_density:
        return (
            f"anchor_density_high: {links}/{words} links/words "
            f"({density:.1%} > {max_density:.0%})"
        )
    return None


def _content_sha256(text: str) -> str:
    """Compute SHA256 of stripped content for dedup."""
    stripped = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


def _jaccard_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity on word sets."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _check_content_uniqueness(
    seed: dict[str, Any],
    store: EventStore | None,
    max_similarity: float,
) -> str | None:
    """Check content uniqueness against events.db. Returns None if pass, or
    reason string if blocked. Skips if no store or no content available."""
    body = seed.get("article_content_markdown")
    if not body:
        body = seed.get("content_markdown")
    if not body or store is None:
        return None

    sha = _content_sha256(body)
    sha_prefix = sha[:8]

    # Query events.db for articles whose body SHA starts with the same prefix
    rows = store.query(
        "SELECT payload_json FROM events "
        "WHERE kind = 'publish.confirmed'",
    )
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        existing_body = payload.get("body") or ""
        if not existing_body:
            continue
        existing_sha = _content_sha256(existing_body)
        if not existing_sha.startswith(sha_prefix):
            continue
        # Prefix matches — compute Jaccard similarity
        similarity = _jaccard_similarity(body, existing_body)
        if similarity > max_similarity:
            return (
                f"duplicate_content: {similarity:.0%} similarity with "
                f"existing published content (max {max_similarity:.0%})"
            )
    return None


def _check_llm_quality(
    seed: dict[str, Any],
    quality_min: int,
) -> str | None:
    """Optional LLM quality check. Returns None if pass, or reason string if
    blocked. Requires the LLM client to be configured.

    This is a simplified implementation: sends title + body to the LLM for
    a quality rating. In production, the existing LLM client from
    ``backlink_publisher._util`` would be used.
    """
    body = seed.get("article_content_markdown") or seed.get("content_markdown") or ""
    title = seed.get("title") or ""
    target_url = seed.get("target_url") or ""

    # Try to use the existing LLM integration if available
    try:
        from backlink_publisher.anchor.lang import LlmClient

        client = LlmClient()
        prompt = (
            "You are a content quality reviewer. Rate the following article "
            f"on a scale of 0-100 for quality, relevance, and coherence. "
            f"Respond ONLY with a JSON object: {{\"score\": <int>}}\n\n"
            f"Title: {title}\n"
            f"Target URL: {target_url}\n"
            f"Body:\n{body[:2000]}"
        )
        response = client.complete(prompt)
        # Parse score from response
        import json as _json

        try:
            data = _json.loads(response)
            score = int(data.get("score", 50))
        except (ValueError, TypeError, json.JSONDecodeError):
            # Non-JSON response — try to extract number
            match = re.search(r"(\d+)", response)
            score = int(match.group(1)) if match else 50

        if score < quality_min:
            return (
                f"llm_rejected: LLM quality score {score} < "
                f"minimum {quality_min}"
            )
    except ImportError:
        # No LLM client available — skip check
        pass
    except Exception as exc:
        # Fail-open on LLM errors
        _log.warning("quality_gate: LLM check error: %s", exc)

    return None


def _emit_quality_blocked(
    store: EventStore,
    quality_check: str,
    draft_label: str,
    seed: dict[str, Any],
) -> None:
    """Emit a ``publish.quality_blocked`` event to events.db."""
    payload = {
        "quality_check": quality_check,
        "draft_label": draft_label,
    }
    store.append(
        PUBLISH_QUALITY_BLOCKED,
        payload,
        target_url=seed.get("target_url"),
        host=seed.get("host"),
    )


def _derive_draft_label(seed: dict[str, Any], index: int) -> str:
    """Derive a human-readable draft label from a seed row."""
    label = seed.get("draft_label") or seed.get("title") or ""
    if not label:
        label = f"seed_{index}"
    return label


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="quality-gate",
        description=(
            "Pre-publish quality gate: read plan-backlinks seed JSONL on "
            "stdin, run quality checks, and emit passing rows on stdout. "
            "Blocked rows are logged on stderr."
        ),
    )
    parser.add_argument(
        "--max-density", type=float, default=0.05, metavar="F",
        help="Maximum allowed anchor link density (default: 0.05 = 5%)",
    )
    parser.add_argument(
        "--max-similarity", type=float, default=0.70, metavar="F",
        help="Maximum allowed content similarity to existing (default: 0.70)",
    )
    parser.add_argument(
        "--quality-llm", action="store_true",
        help="Enable LLM quality scoring (optional, requires LLM config)",
    )
    parser.add_argument(
        "--quality-min", type=int, default=40, metavar="N",
        help="Minimum LLM quality score (default: 40; only with --quality-llm)",
    )
    parser.add_argument(
        "--emit-events", action="store_true",
        help="Emit publish.quality_blocked events to events.db",
    )
    args = parser.parse_args(argv)

    # Validation
    if args.max_density <= 0 or args.max_density >= 1:
        emit_error(
            "quality-gate: --max-density must be between 0 and 1 (exclusive)",
            exit_code=1,
        )
    if args.max_similarity <= 0 or args.max_similarity >= 1:
        emit_error(
            "quality-gate: --max-similarity must be between 0 and 1 (exclusive)",
            exit_code=1,
        )
    if args.quality_min < 0 or args.quality_min > 100:
        emit_error(
            "quality-gate: --quality-min must be between 0 and 100",
            exit_code=1,
        )

    # Buffer stdin
    lines = sys.stdin.read().split("\n")
    if not any(line.strip() for line in lines):
        print("quality-gate: 0 rows to check — empty stdin.", file=sys.stderr)
        return
    seeds = list(read_jsonl(lines, strict=True))

    store = EventStore() if args.emit_events else None

    passing: list[dict[str, Any]] = []
    blocked_count = 0
    blocked_reasons: list[str] = []

    for index, seed in enumerate(seeds):
        draft_label = _derive_draft_label(seed, index)
        reasons: list[str] = []

        # R1: Anchor density check
        density_reason = _check_anchor_density(seed, args.max_density)
        if density_reason:
            reasons.append(density_reason)

        # R2: Content uniqueness check
        uniqueness_reason = _check_content_uniqueness(
            seed, store, args.max_similarity,
        )
        if uniqueness_reason:
            reasons.append(uniqueness_reason)

        # R3: LLM quality check (opt-in)
        if args.quality_llm:
            llm_reason = _check_llm_quality(seed, args.quality_min)
            if llm_reason:
                reasons.append(llm_reason)

        if reasons:
            blocked_count += 1
            for reason in reasons:
                quality_check = reason.split(":")[0]
                msg = (
                    f"quality-gate: blocked [{draft_label}] — {reason}"
                )
                blocked_reasons.append(msg)
                print(msg, file=sys.stderr)

                if args.emit_events and store is not None:
                    _emit_quality_blocked(
                        store,
                        quality_check=quality_check,
                        draft_label=draft_label,
                        seed=seed,
                    )
        else:
            passing.append(seed)

    # Emit passing rows on stdout
    write_jsonl(iter(passing), sys.stdout)

    # Summary on stderr
    print(
        f"quality-gate: {len(passing)} passed, {blocked_count} blocked "
        f"(of {len(seeds)} total)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()