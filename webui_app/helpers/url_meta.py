"""URL metadata, content-fetch gate, anchor-pool derivation.

Extracted from webui_app/helpers.py in Plan 2026-05-21-007 Unit 1.
Import direction: url_meta → backlink_publisher.config._domain_label only.

_TRUTHY_BYPASS is temporarily duplicated here from the future
helpers/security.py (Unit 3). Remove the duplicate when Unit 3 lands.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from backlink_publisher.config import _domain_label
from backlink_publisher.content import fetch as content_fetch


# Temporarily duplicated from helpers/security.py (Unit 3).
# Do NOT import from helpers.py — that creates a circular dep.
_TRUTHY_BYPASS = {"1", "true", "yes"}


def _is_fetch_verify_disabled() -> bool:
    return os.environ.get("BACKLINK_NO_FETCH_VERIFY", "").strip().lower() in _TRUTHY_BYPASS


def _content_gate_enabled() -> bool:
    return not _is_fetch_verify_disabled()


def _verify_urls_or_error(
    urls: list[str], field_label: str
) -> tuple[list[str], str | None]:
    if not urls:
        return [], None
    if not _content_gate_enabled():
        return list(urls), None
    results = content_fetch.verify_urls_batch(urls)
    survivors: list[str] = []
    failures: list[str] = []
    for u in urls:
        ok, reason, _title = results.get(u, (False, "missing_result", None))
        if ok:
            survivors.append(u)
        else:
            failures.append(f"{u} ({reason})")
    if failures:
        joined = ", ".join(failures)
        return survivors, f"{field_label} 无可访问内容: {joined}"
    return survivors, None


def _fetch_page(url, timeout=10):
    headers = {'User-Agent':
               'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    resp = requests.get(url, headers=headers, timeout=timeout, verify=False)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')


def _extract_title(soup):
    og = soup.find('meta', property='og:title')
    if og:
        return (og.get('content', '') or '').strip()
    tag = soup.find('title')
    return tag.text.strip() if tag else ''


def _extract_description(soup):
    og = soup.find('meta', property='og:description')
    if og:
        return (og.get('content', '') or '').strip()
    meta = soup.find('meta', attrs={'name': 'description'})
    return (meta.get('content', '') or '').strip() if meta else ''


def fetch_url_metadata(url):
    try:
        soup = _fetch_page(url, timeout=10)
        title = _extract_title(soup)
        desc = _extract_description(soup)
        return {'url': url, 'title': title, 'description': desc, 'status': 'success'}
    except Exception as e:
        return {'url': url, 'title': '', 'description': '',
                'status': 'error', 'error': str(e)}


def fetch_full_tdk(url):
    try:
        soup = _fetch_page(url, timeout=15)
        title = _extract_title(soup)
        description = _extract_description(soup)
        keywords = ''
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = (meta_keywords.get('content', '') or '').strip()

        suggested_anchors = []
        if keywords:
            suggested_anchors = [k.strip() for k in keywords.split(',') if k.strip()]
        if not suggested_anchors and title:
            suggested_anchors = [t for t in title.replace('|', '-').replace('_', '-').split('-') if len(t.strip()) > 3][:3]

        return {
            'title': title, 'description': description,
            'keywords': keywords, 'suggested_anchors': suggested_anchors,
            'status': 'success'
        }
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def detect_platform(url):
    # Unknown-domain fallback flipped from 'medium' to 'blogger' per operator
    # preset (2026-05-20); medium/blogger explicit matches preserved.
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if 'medium.com' in domain:
        return 'medium'
    if 'blogspot.com' in domain or 'blogger.com' in domain:
        return 'blogger'
    return 'blogger'


def detect_language(url):
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    if '.cn' in domain or 'cn' in path:
        return 'zh-CN'
    if '.tw' in domain or 'tw' in path or 'hk' in path:
        return 'zh-TW'
    if '.jp' in domain or 'jp' in path or 'ja' in path:
        return 'ja'
    if '.ru' in domain or 'ru' in path:
        return 'ru'
    if '.es' in domain or 'es' in path:
        return 'es'
    if '.de' in domain or 'de' in path:
        return 'de'
    if '.fr' in domain or 'fr' in path:
        return 'fr'
    return 'zh-CN'


def get_main_domain(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_url(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


# ─────────────────────────────────────────────────────────────────────────────
# Derived anchor pools (plan 006)
# ─────────────────────────────────────────────────────────────────────────────

_DERIVED_BRANDED_MAX: int = 30
_DERIVED_PARTIAL_MAX: int = 60
_DERIVED_PARTIAL_KEEP: int = 3
_DERIVED_PARTIAL_SPLIT_RE = re.compile(r"[。.；;，,、]+")


def _derive_branded_pool(main_url: str, tdk: dict | None) -> list[str]:
    if tdk and tdk.get("title"):
        title = str(tdk["title"]).strip()
        if title:
            return [title[:_DERIVED_BRANDED_MAX]]
    return [_domain_label(main_url)]


def _derive_partial_pool(main_url: str, tdk: dict | None) -> list[str]:
    if tdk and tdk.get("description"):
        desc = str(tdk["description"]).strip()
        if desc:
            phrases = [
                p.strip()[:_DERIVED_PARTIAL_MAX]
                for p in _DERIVED_PARTIAL_SPLIT_RE.split(desc)
                if p and p.strip()
            ]
            if phrases:
                return phrases[:_DERIVED_PARTIAL_KEEP]
    return [_domain_label(main_url)]


def _derive_exact_pool(main_url: str) -> list[str]:
    return [_domain_label(main_url)]
