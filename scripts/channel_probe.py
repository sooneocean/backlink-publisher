"""Channel reachability probe — GO/NO-GO triage before building an adapter.

Experimental developer tool — NOT part of the publishing pipeline.
Run via: python scripts/channel_probe.py <url> [<content_url> ...]

The deterministic, HTTP-only tier of channel analysis. For a candidate
backlink channel it answers the cheap questions before any code is written:

  - Does the site serve our verifier's *bot* user-agent, or 403 it?
    (If bots are 403'd, the real `link_attr_verifier` preflight fetch can
    never reach a verdict, and search engines likely can't index it either.)
  - Is content behind a login wall (redirect to /login, password form)?
  - Is it a Cloudflare/WAF JS-challenge that only a real browser passes?

It probes each URL with three user-agents — the project's REAL preflight UA
(imported live so it never drifts), a Googlebot UA, and a desktop-browser UA —
and emits a triage verdict. The HTTP tier CANNOT see JS-rendered content or
extract outbound <a rel> link attributes; when the site is reachable-but-gated
it emits ``needs-browser-tier`` and the exact checks the browser step must run
(the `channel-probe` skill drives that step + the final dofollow verdict).

This mirrors the spike-script convention (`*_spike.py`, `*_diagnose.py`):
read-only, no config writes, JSON-or-human output, advisory.

SSRF safety (R14, funnel-brainstorm 2026-06-01): every URL — including each
redirect hop — is validated via net_safety._check_url_for_ssrf before fetch.
This guard MUST remain in place before the script is ever driven on
machine-sourced candidate lists (SERP / LLM / family-enum). The guard is
fail-open (falls back to an unguarded warning) when the package is absent so
the script remains usable outside an installed venv for hand-curated URLs.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests

# Real verifier UA, imported live so the probe matches what the pipeline's
# link_attr_verifier preflight fetch actually sends. Fallback keeps the probe
# runnable outside the installed venv.
try:
    from backlink_publisher.content._preflight_fetch import USER_AGENT as _PREFLIGHT_UA
except Exception:  # noqa: BLE001 — diagnostic must run even if package is absent
    _PREFLIGHT_UA = "backlink-publisher/0.1 preflight-targets"

# SSRF guard — same function used by the production pipeline's preflight fetch.
# Fail-open: if the package is absent, the guard is skipped (manual hand-curated
# URLs only, never machine-sourced). Warn loudly so the operator knows the risk.
try:
    from backlink_publisher._util.net_safety import _check_url_for_ssrf as _ssrf_check
    _SSRF_GUARD_ACTIVE = True
except Exception:  # noqa: BLE001
    _ssrf_check = None  # type: ignore[assignment]
    _SSRF_GUARD_ACTIVE = False

GOOGLEBOT_UA = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

USER_AGENTS = {
    "preflight-bot": _PREFLIGHT_UA,  # what our real verifier sends
    "googlebot": GOOGLEBOT_UA,
    "browser": BROWSER_UA,
}

_CF_MARKERS = ("just a moment", "cf-chl", "attention required", "cloudflare")
_LOGIN_MARKERS = ('type="password"', "forgot password", "sign in to", "log in to")
_TIMEOUT = 20
_MAX_REDIRECTS = 10


@dataclass
class Hit:
    ua: str
    status: Optional[int]
    final_url: str = ""
    redirected: bool = False
    server: str = ""
    body_len: int = 0
    looks_cloudflare: bool = False
    looks_login_wall: bool = False
    error: str = ""


@dataclass
class UrlResult:
    url: str
    hits: list[Hit] = field(default_factory=list)


def _validate_url_ssrf(url: str) -> Optional[str]:
    """Return blocked reason if URL is SSRF-dangerous, else None.

    Returns None (safe) when the guard is not active (package absent).
    """
    if _ssrf_check is None:
        return None
    return _ssrf_check(url)


def _probe(url: str, ua_key: str, ua: str) -> Hit:
    # SSRF guard: validate initial URL before any network contact.
    blocked = _validate_url_ssrf(url)
    if blocked:
        return Hit(ua=ua_key, status=None, error=f"ssrf-blocked:{blocked}")

    try:
        # Follow redirects manually so every hop passes the SSRF gate.
        resp = requests.get(
            url,
            headers={"User-Agent": ua, "Accept": "text/html,*/*"},
            timeout=_TIMEOUT,
            allow_redirects=False,
        )
        redirect_count = 0
        while resp.is_redirect and redirect_count < _MAX_REDIRECTS:
            next_url = resp.headers.get("Location", "").strip()
            if not next_url:
                break
            # Resolve relative redirects against the current response URL.
            if not next_url.startswith(("http://", "https://")):
                next_url = urljoin(resp.url, next_url)
            blocked = _validate_url_ssrf(next_url)
            if blocked:
                return Hit(
                    ua=ua_key,
                    status=None,
                    error=f"ssrf-redirect-blocked:{blocked}",
                )
            resp = requests.get(
                next_url,
                headers={"User-Agent": ua, "Accept": "text/html,*/*"},
                timeout=_TIMEOUT,
                allow_redirects=False,
            )
            redirect_count += 1
    except requests.RequestException as exc:
        return Hit(ua=ua_key, status=None, error=f"{type(exc).__name__}: {exc}")

    body = resp.text[:20000].lower()
    final = resp.url
    login = (
        "/login" in final
        or "/signin" in final
        or any(m in body for m in _LOGIN_MARKERS)
    )
    cf = resp.status_code == 403 and (
        "cloudflare" in resp.headers.get("Server", "").lower()
        or any(m in body for m in _CF_MARKERS)
    )
    return Hit(
        ua=ua_key,
        status=resp.status_code,
        final_url=final,
        redirected=final.rstrip("/") != url.rstrip("/"),
        server=resp.headers.get("Server", ""),
        body_len=len(resp.content),
        looks_cloudflare=cf,
        looks_login_wall=login,
    )


def _triage(results: list[UrlResult]) -> tuple[str, list[str], list[str]]:
    """Return (verdict, signals, next_checks).

    Key correctness rule (learned dogfooding bloglovin): an HTTP 200 from a
    JS/SPA site proves nothing about content availability — `requests` does not
    run the client-side redirect, so a login-gated shell returns 200 with a
    full HTML bundle. Therefore a login-wall signal anywhere CAPS the verdict
    at ``needs-browser-tier``; only the browser tier can confirm a real,
    public, linkable surface.
    """
    all_hits = [h for r in results for h in r.hits]
    coded = [h for h in all_hits if h.status is not None]

    def _ua_2xx(key: str) -> bool:
        return any(h.ua == key and h.status and 200 <= h.status < 300 for h in all_hits)

    def _ua_only_403(key: str) -> bool:
        ks = [h for h in all_hits if h.ua == key and h.status is not None]
        return bool(ks) and all(h.status == 403 for h in ks)

    signals: list[str] = []
    preflight_2xx = _ua_2xx("preflight-bot")  # can OUR verifier even fetch it?
    preflight_403 = _ua_only_403("preflight-bot")
    googlebot_403 = _ua_only_403("googlebot")
    browser_2xx = _ua_2xx("browser")
    any_2xx = any(h.status and 200 <= h.status < 300 for h in coded)
    any_login = any(h.looks_login_wall for h in all_hits)
    any_cf = any(h.looks_cloudflare for h in all_hits)

    if not coded:
        signals.append("No HTTP response from any UA (DNS/connection failure).")
    if preflight_2xx:
        signals.append("Our preflight verifier UA receives 2xx (HTTP-fetchable).")
    if preflight_403:
        signals.append(
            "Our preflight verifier UA is 403'd — link_attr_verifier cannot fetch this channel."
        )
    if googlebot_403 and not preflight_403:
        signals.append(
            "Googlebot UA hard-403'd while a generic UA passes — likely Cloudflare "
            "anti-spoofing (it verifies Googlebot by IP, not UA). The REAL Googlebot "
            "may or may not be blocked; confirm via the `site:` index check, do not assume."
        )
    if any_cf:
        signals.append("Cloudflare/WAF challenge detected (403 + CF markers).")
    if any_login:
        signals.append(
            "Login wall detected — an HTTP 200 here is a gated SPA shell, NOT proof of "
            "a public content surface. Browser tier required to see real content/links."
        )

    next_checks = [
        "Render a content/post page in a REAL browser (JS-capable). Does it "
        "pass the challenge, or hit a login wall?",
        "On the rendered page, extract ALL outbound <a href> + rel. Is there a "
        "real link to the source blog / target — and is it dofollow or nofollow?",
        "Google index: `site:<domain>` — are fresh dated content pages indexed, "
        "or only stale structural pages?",
    ]

    if not coded:
        return "no-go-unreachable", signals, next_checks
    if preflight_403 and not browser_2xx:
        # Nothing can fetch it the way the pipeline would.
        return "no-go-unreachable", signals, next_checks
    if any_login:
        # 200s are gated shells — inconclusive over HTTP regardless of status.
        return "needs-browser-tier", signals, next_checks
    if preflight_403 and browser_2xx:
        # JS-only: bots blocked, only a real browser renders it.
        return "needs-browser-tier", signals, next_checks
    if any_2xx:
        return "needs-canary", signals, next_checks
    return "needs-browser-tier", signals, next_checks


_VERDICT_NOTE = {
    "no-go-unreachable": (
        "NO-GO candidate: the channel does not serve our verifier and is not "
        "reachable in a way the pipeline can use. Record in "
        "docs/notes/retired-platforms/."
    ),
    "needs-browser-tier": (
        "INCONCLUSIVE over HTTP: reachable only by a JS browser and/or "
        "login-gated. Run the browser tier (see next_checks) before deciding. "
        "If bots are 403'd, search-engine indexation — and thus SEO value — is "
        "in doubt."
    ),
    "needs-canary": (
        "PLAUSIBLE: HTTP-reachable. Confirm a real dofollow backlink surface "
        "via the browser tier + a live pipeline canary before register()."
    ),
}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "urls", nargs="+", help="Homepage + optional content/post URL(s) to probe."
    )
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = ap.parse_args(argv)

    if not _SSRF_GUARD_ACTIVE:
        print(
            "WARNING: SSRF guard inactive (backlink_publisher package not installed). "
            "Use only with hand-curated, trusted URLs.",
            file=sys.stderr,
        )

    results: list[UrlResult] = []
    for url in args.urls:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        r = UrlResult(url=url)
        for ua_key, ua in USER_AGENTS.items():
            r.hits.append(_probe(url, ua_key, ua))
        results.append(r)

    verdict, signals, next_checks = _triage(results)
    payload = {
        "preflight_ua": _PREFLIGHT_UA,
        "ssrf_guard_active": _SSRF_GUARD_ACTIVE,
        "results": [asdict(r) for r in results],
        "signals": signals,
        "verdict": verdict,
        "verdict_note": _VERDICT_NOTE[verdict],
        "next_checks": next_checks,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    # Human-readable
    print(f"Channel probe — preflight UA: {_PREFLIGHT_UA}\n")
    if not _SSRF_GUARD_ACTIVE:
        print("⚠  SSRF guard inactive — use only with trusted, hand-curated URLs.\n")
    for r in results:
        print(f"  {r.url}")
        for h in r.hits:
            if h.error:
                print(f"    [{h.ua:<13}] ERROR {h.error}")
                continue
            tags = []
            if h.looks_cloudflare:
                tags.append("CF-challenge")
            if h.looks_login_wall:
                tags.append("login-wall")
            if h.redirected:
                tags.append(f"→ {h.final_url}")
            suffix = ("  " + " ".join(tags)) if tags else ""
            print(f"    [{h.ua:<13}] HTTP {h.status}  ({h.body_len}B){suffix}")
        print()
    if signals:
        print("Signals:")
        for s in signals:
            print(f"  • {s}")
        print()
    print(f"VERDICT: {verdict}")
    print(f"  {_VERDICT_NOTE[verdict]}\n")
    print("Browser-tier checks still required:")
    for c in next_checks:
        print(f"  → {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
