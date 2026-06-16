"""Three-URL target schema parser + site_url_categories."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..._util.logger import plan_logger
from ..._util.url import validate_https_url, validate_main_domain_url
from ..types import (
    Config,
    DEFAULT_WORK_TEMPLATES,
    ThreeUrlConfig,
)

from .target import _clean_pool

_log = logging.getLogger(__name__)


def _parse_work_urls(entry: dict, raw_domain: str) -> list[str]:
    """Validate and normalise the ``work_urls`` list for one target entry."""
    raw: Any = entry.get("work_urls", []) or []
    if not isinstance(raw, list):
        raw = []

    urls: list[str] = []
    dropped = 0
    for u in raw:
        if not isinstance(u, str):
            dropped += 1
            continue
        normalized = validate_https_url(u)
        if not normalized:
            dropped += 1
            continue
        urls.append(normalized)
    if dropped:
        _log.warning(
            "[targets.%r].work_urls: dropped %d non-https or invalid URL(s)",
            raw_domain, dropped,
        )
    return urls


def _parse_work_templates(entry: dict, raw_domain: str) -> list[str]:
    """Return the resolved ``work_anchor_templates`` list for one target entry."""
    raw = entry.get("work_anchor_templates")
    if raw is None:
        return list(DEFAULT_WORK_TEMPLATES)
    if isinstance(raw, list) and all(isinstance(t, str) for t in raw):
        stripped = [t.strip() for t in raw if t.strip()]
        return stripped or list(DEFAULT_WORK_TEMPLATES)
    _log.warning(
        "[targets.%r].work_anchor_templates must be a list of strings, "
        "using defaults",
        raw_domain,
    )
    return list(DEFAULT_WORK_TEMPLATES)


def _parse_blocklist(entry: dict, raw_domain: str) -> list[str] | None:
    """Return the resolved ``list_path_blocklist`` for one target entry."""
    raw = entry.get("list_path_blocklist")
    if raw is None:
        return None
    if isinstance(raw, list) and all(isinstance(p, str) for p in raw):
        return [p for p in raw if p]
    _log.warning(
        "[targets.%r].list_path_blocklist must be a list of strings, "
        "ignoring (default blocklist applies)",
        raw_domain,
    )
    return None


def _parse_target_three_url(targets_section: Any) -> dict[str, ThreeUrlConfig]:
    """Parse ``[targets."<main_domain>"]`` entries that carry the three-URL
    work-themed schema. Plan 2026-05-13-004 Unit 3.

    Tolerant of malformed entries — each error is logged at WARN level and the
    offending entry is skipped rather than aborting the load. Entries that
    only carry ``anchor_keywords`` (legacy schema) are silently ignored here;
    they belong to ``_parse_target_anchor_keywords``.

    Returns a dict keyed by ``main_url`` with trailing slash stripped (the
    canonical key used by ``get_three_url_config``).
    """
    if not isinstance(targets_section, dict):
        return {}
    result: dict[str, ThreeUrlConfig] = {}
    for raw_domain, entry in targets_section.items():
        if not isinstance(entry, dict):
            continue  # already logged by _parse_target_anchor_keywords
        # Detection: any of main_url/list_url/*_pool present → caller intends
        # the three-URL schema. anchor_keywords-only entries are silently ignored.
        if not any(k in entry for k in (
            "main_url", "list_url", "branded_pool", "partial_pool", "exact_pool",
        )):
            continue

        main_url = validate_main_domain_url(entry.get("main_url"))
        if not main_url:
            _log.warning(
                "[targets.%r].main_url must be https://<host>/ (host-root + "
                "trailing slash), skipping",
                raw_domain,
            )
            continue

        list_url = validate_https_url(entry.get("list_url"))
        if not list_url:
            _log.warning(
                "[targets.%r].list_url missing or not https://, skipping",
                raw_domain,
            )
            continue

        branded_pool = _clean_pool(entry.get("branded_pool"))
        partial_pool = _clean_pool(entry.get("partial_pool"))
        exact_pool = _clean_pool(entry.get("exact_pool"))
        if not branded_pool:
            _log.warning(
                "[targets.%r].branded_pool is empty or invalid; target is "
                "unusable without a branded pool, skipping",
                raw_domain,
            )
            continue
        if not partial_pool:
            _log.warning(
                "[targets.%r].partial_pool is empty or invalid, skipping",
                raw_domain,
            )
            continue
        if not exact_pool:
            _log.warning(
                "[targets.%r].exact_pool is empty or invalid, skipping",
                raw_domain,
            )
            continue

        work_urls = _parse_work_urls(entry, raw_domain)
        templates = _parse_work_templates(entry, raw_domain)
        blocklist = _parse_blocklist(entry, raw_domain)

        insecure_tls = bool(entry.get("insecure_tls", False))

        key = main_url.rstrip("/")
        result[key] = ThreeUrlConfig(
            main_url=main_url,
            list_url=list_url,
            branded_pool=branded_pool,
            partial_pool=partial_pool,
            exact_pool=exact_pool,
            work_urls=work_urls,
            work_anchor_templates=templates,
            list_path_blocklist=blocklist,
            insecure_tls=insecure_tls,
        )
    return result


def _parse_site_url_categories(sites_section: Any) -> dict[str, dict[str, str]]:
    """Parse ``[sites."<main_domain>".url_categories]`` entries.

    Each URL must be a string starting with ``http://`` or ``https://``;
    malformed entries are skipped with a warning rather than raising.
    """
    if not isinstance(sites_section, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for raw_domain, entry in sites_section.items():
        if not isinstance(entry, dict):
            continue
        categories = entry.get("url_categories")
        if categories is None:
            continue
        if not isinstance(categories, dict):
            _log.warning(
                "[sites.%r].url_categories must be a table, skipping", raw_domain,
            )
            continue
        cleaned: dict[str, str] = {}
        for cat_name, cat_url in categories.items():
            if not isinstance(cat_name, str) or not isinstance(cat_url, str):
                continue
            if not re.match(r"^https?://", cat_url):
                _log.warning(
                    "[sites.%r].url_categories.%r is not a valid URL, skipping",
                    raw_domain, cat_name,
                )
                continue
            cleaned[cat_name] = cat_url
        if cleaned:
            result[raw_domain.rstrip("/")] = cleaned
    return result


def _normalize_domain_key(domain: str) -> str:
    """Strip scheme and trailing slashes for config key comparison."""
    return domain.rstrip("/").removeprefix("https://").removeprefix("http://")


def _domain_label(url: str) -> str:
    """Extract the leading host label from a URL, stripping ``www.``.

    ``https://www.51acgs.com/`` → ``"51acgs"``;
    ``https://a.b.c.com/`` → ``"a"``.

    Used by :func:`upgrade_target_to_threeurl` as the bootstrap fallback
    when an unknown main_url has no existing anchor_keywords to migrate
    from. Mirrors the same heuristic the homepage form / brainstorm doc
    use for "brand label".
    """
    from urllib.parse import urlparse as _urlparse
    netloc = _urlparse(url).netloc
    if netloc.startswith("www."):
        netloc = netloc[4:]
    first_segment = netloc.split(".", 1)[0]
    return first_segment or netloc or "site"


def upgrade_target_to_threeurl(
    config: Config,
    main_url: str,
    category_url: str | None = None,
    work_url: str | None = None,
) -> ThreeUrlConfig:
    """Return a ThreeUrlConfig for ``main_url`` derived from current state.

    Decision tree (Plan 2026-05-14-009 Unit 3):

    1. **Existing ThreeUrlConfig.** Overwrite ``list_url`` (if ``category_url``
       provided) and ``work_urls=[work_url]`` (if ``work_url`` provided).
       Other fields kept as-is — operator already tuned them via ``/sites``.

    2. **Legacy anchor_keywords.** Migrate keywords → ``branded_pool``. Fill
       ``partial_pool`` and ``exact_pool`` with the domain label as a
       non-empty fallback (ThreeUrlConfig schema requires all three pools
       non-empty per ``_parse_target_three_url``). ``list_url`` = category_url
       when provided, else main_url; ``work_urls`` = [work_url] when provided.

    3. **Bootstrap.** No prior state — every pool defaults to the domain
       label, ``list_url`` = category_url or main_url, ``work_urls`` =
       [work_url] when provided. All ThreeUrlConfig defaults for the
       remaining fields (work_anchor_templates, list_path_blocklist,
       insecure_tls).

    Returns a fresh ``ThreeUrlConfig`` instance; does not mutate
    ``config``. Caller is responsible for calling ``save_config`` with
    the upgraded entry merged into ``target_three_url``.

    Always emits a ``plan_logger.recon('target_upgraded_to_threeurl', ...)``
    event so the operator sees which migration path was taken.
    """
    domain_key = main_url.rstrip("/")
    label = _domain_label(main_url)
    new_list_url = category_url or main_url
    new_work_urls = [work_url] if work_url else []

    from ..loader import get_three_url_config
    existing = get_three_url_config(config, main_url)
    if existing is not None:
        plan_logger.recon(
            "target_upgraded_to_threeurl",
            main=domain_key,
            source="merge_existing",
            category_set=bool(category_url),
            work_set=bool(work_url),
        )
        return ThreeUrlConfig(
            main_url=existing.main_url,
            list_url=category_url or existing.list_url,
            branded_pool=list(existing.branded_pool),
            partial_pool=list(existing.partial_pool),
            exact_pool=list(existing.exact_pool),
            work_urls=new_work_urls if work_url else list(existing.work_urls),
            work_anchor_templates=list(existing.work_anchor_templates),
            list_path_blocklist=(
                list(existing.list_path_blocklist)
                if existing.list_path_blocklist is not None
                else None
            ),
            insecure_tls=existing.insecure_tls,
        )

    # Use the canonical accessor so a legacy pool keyed by the bare domain or a
    # scheme variant is found before declaring bootstrap. The previous manual
    # lookup tried only the scheme-exact key plus a trailing-slash variant —
    # but stored keys are always rstrip('/')-normalised by
    # _parse_target_anchor_keywords, so the trailing-slash branch was dead code
    # and a bare-domain anchor_keywords pool was silently lost on upgrade.
    from .anchor import get_anchor_keywords

    keywords = get_anchor_keywords(config, main_url)

    if keywords:
        plan_logger.recon(
            "target_upgraded_to_threeurl",
            main=domain_key,
            source="anchor_keywords",
            n_keywords=len(keywords),
        )
        return ThreeUrlConfig(
            main_url=main_url,
            list_url=new_list_url,
            branded_pool=list(keywords),
            partial_pool=[label],
            exact_pool=[label],
            work_urls=new_work_urls,
        )

    plan_logger.recon(
        "target_upgraded_to_threeurl",
        main=domain_key,
        source="bootstrap",
    )
    return ThreeUrlConfig(
        main_url=main_url,
        list_url=new_list_url,
        branded_pool=[label],
        partial_pool=[label],
        exact_pool=[label],
        work_urls=new_work_urls,
    )
