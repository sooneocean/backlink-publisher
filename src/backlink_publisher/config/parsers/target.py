"""Target-level anchor keyword + V2 pool parsers."""
from __future__ import annotations

import logging
from typing import Any

from ..types import (
    ANCHOR_TYPES,
    _UNSAFE_IN_ANCHOR,
)

_log = logging.getLogger(__name__)


def _parse_target_anchor_keywords(targets_section: Any) -> dict[str, list[str]]:
    """Parse ``[targets."<main_domain>"].anchor_keywords`` entries.

    Tolerant of missing / malformed entries — invalid entries are skipped with a
    warning rather than aborting the whole config load. Keys are normalised by
    stripping trailing slashes so lookups work regardless of how the user wrote
    the domain.
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, list[str]] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            _log.warning(
                "[targets.%r] is not a table, skipping", raw_domain,
            )
            continue
        keywords = entry.get("anchor_keywords")
        if keywords is None:
            continue
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            _log.warning(
                "[targets.%r].anchor_keywords must be a list of strings, skipping",
                raw_domain,
            )
            continue
        # Strip characters that would break Markdown link syntax or inject HTML.
        # Brackets, parens, angle-brackets, newlines can corrupt [anchor](url) output.
        cleaned = [_UNSAFE_IN_ANCHOR.sub("", k).strip() for k in keywords]
        cleaned = [k for k in cleaned if k]  # drop any that became empty after cleaning
        key = raw_domain.rstrip("/")
        result[key] = cleaned
    return result


def _parse_target_string_list_field(
    targets_section: Any, field_name: str,
) -> dict[str, list[str]]:
    """Parse a per-target ``list[str]`` field out of ``[targets."<domain>"]``.

    Generic helper mirroring :func:`_parse_target_anchor_keywords`'s tolerance
    contract: missing / malformed entries are skipped with a warning rather
    than aborting the whole config load. Keys are normalised by stripping
    trailing slashes so lookups work regardless of how the user wrote the
    domain. Used for the GEO ``probe_queries`` and ``brand_aliases`` fields
    (Plan 2026-05-29-006 Unit 1).

    Unlike anchor keywords these values are not Markdown-link anchors, so no
    ``_UNSAFE_IN_ANCHOR`` scrubbing is applied — only whitespace is stripped
    and empties dropped.
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, list[str]] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            _log.warning(
                "[targets.%r] is not a table, skipping", raw_domain,
            )
            continue
        values = entry.get(field_name)
        if values is None:
            continue
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            _log.warning(
                "[targets.%r].%s must be a list of strings, skipping",
                raw_domain, field_name,
            )
            continue
        cleaned = [v.strip() for v in values]
        cleaned = [v for v in cleaned if v]  # drop empties after stripping
        key = raw_domain.rstrip("/")
        result[key] = cleaned
    return result


def _clean_pool(value: Any) -> list[str]:
    """Strip unsafe chars + drop empties from a list-of-string pool entry."""
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return []
    cleaned = [_UNSAFE_IN_ANCHOR.sub("", v).strip() for v in value]
    return [v for v in cleaned if v]


def _parse_target_anchor_pools_v2(
    sites_section: Any,
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Parse ``[sites."<main_domain>".anchor_pools.<url_cat>.<anchor_type>]``.

    Schema-strict: ``anchor_type`` must be one of ``ANCHOR_TYPES``; lists must
    be ``list[str]``. Pool entries are run through ``_UNSAFE_IN_ANCHOR`` to
    strip characters that would break Markdown/HTML link syntax — same hygiene
    contract as the legacy ``target_anchor_keywords`` parser.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        pools = entry.get("anchor_pools")
        if pools is None:
            continue
        if not isinstance(pools, dict):
            _log.warning(
                "[sites.%r].anchor_pools must be a table, skipping", raw_domain,
            )
            continue
        site_pools: dict[str, dict[str, list[str]]] = {}
        for url_cat, type_table in pools.items():
            if not isinstance(type_table, dict):
                continue
            cat_pools: dict[str, list[str]] = {}
            for anchor_type, words in type_table.items():
                if anchor_type not in ANCHOR_TYPES:
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s is not a known anchor "
                        "type (expected one of %s), skipping",
                        raw_domain, url_cat, anchor_type, ANCHOR_TYPES,
                    )
                    continue
                if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
                    _log.warning(
                        "[sites.%r].anchor_pools.%s.%s must be a list of strings, skipping",
                        raw_domain, url_cat, anchor_type,
                    )
                    continue
                cleaned = [_UNSAFE_IN_ANCHOR.sub("", w).strip() for w in words]
                cleaned = [w for w in cleaned if w]
                cat_pools[anchor_type] = cleaned
            if cat_pools:
                site_pools[url_cat] = cat_pools
        if site_pools:
            result[raw_domain.rstrip("/")] = site_pools
    return result
