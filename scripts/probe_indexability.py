#!/usr/bin/env python3
"""Premise-validation probe for the source-page indexability feature.

Prototype of R3a-R3d from
docs/brainstorms/2026-06-01-seo-outcome-indexability-loop-requirements.md.

Answers: do real published backlink HOST pages carry indexability barriers
(noindex meta / X-Robots-Tag / canonical-away / robots-disallow) that would
make an `alive` dofollow link pass zero equity? Per-URL verdict + per-channel
aggregation (barrier rate + unknown rate).

Stdlib only. Read-only network. Never raises.

Usage:
  python scripts/probe_indexability.py URL [URL ...]
  python scripts/probe_indexability.py --from-events   # sample live_urls from events.db
  python scripts/probe_indexability.py --robots-only HOST [HOST ...]
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from html.parser import HTMLParser
from urllib.parse import urlparse, urlunparse

UA = "Mozilla/5.0 (compatible; backlink-indexability-probe/0.1; +diagnostic)"
TIMEOUT = 12
BODY_CAP = 768 * 1024  # mirror PREFLIGHT_BODY_BYTES discipline

_NOINDEX_RE = re.compile(r"\b(noindex|none)\b", re.I)


class _HeadParser(HTMLParser):
    """Extract <meta name=robots|googlebot content> and <link rel=canonical href>."""

    def __init__(self) -> None:
        super().__init__()
        self.meta_robots: list[str] = []
        self.canonical: str | None = None
        self._in_head = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = a.get("name", "").lower()
            if name in ("robots", "googlebot"):
                self.meta_robots.append(a.get("content", ""))
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            if not self.canonical:
                self.canonical = a.get("href", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False


def _norm_url(u: str) -> str:
    """Lexical canonicalize for same/different comparison (NO network)."""
    try:
        p = urlparse(u.strip())
    except Exception:
        return u.strip().lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    # drop fragment + query (incl. tracking) for the equivalence test
    return urlunparse(("https", host, path, "", "", ""))


def _fetch(url: str) -> dict:
    """Return {status, headers(lower), body, error}. Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(BODY_CAP)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            return {"status": resp.status, "headers": headers, "body": body, "error": None,
                    "final_url": resp.geturl()}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "headers": {k.lower(): v for k, v in (e.headers or {}).items()},
                "body": "", "error": f"http_{e.code}", "final_url": url}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "headers": {}, "body": "", "error": type(e).__name__, "final_url": url}


def _robots_disallows(host: str, path: str) -> tuple[bool, str]:
    """True if robots.txt disallows `path` for * or Googlebot. (advisory: crawl != index)."""
    robots_url = f"https://{host}/robots.txt"
    r = _fetch(robots_url)
    if r["error"] or r["status"] != 200 or not r["body"]:
        return False, "robots_unreadable"  # fail-open
    groups: dict[str, list[str]] = defaultdict(list)
    agent = None
    for line in r["body"].splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        kl = k.lower()
        if kl == "user-agent":
            agent = v.lower()
        elif kl == "disallow" and agent in ("*", "googlebot"):
            groups[agent].append(v)
    for ua in ("googlebot", "*"):
        for dis in groups.get(ua, []):
            if dis and path.startswith(dis):
                return True, f"robots_disallow[{ua}]:{dis}"
    return False, "robots_allows"


def probe(url: str) -> dict:
    """R3a-R3d verdict for one URL. ok / blocked / unknown."""
    out = {"url": url, "verdict": "unknown", "barriers": [], "canonical": None,
           "status": None, "note": ""}
    r = _fetch(url)
    out["status"] = r["status"]
    if r["error"] or r["status"] != 200 or not r["body"]:
        out["note"] = r["error"] or f"status_{r['status']}"
        return out  # unknown — fail-open

    barriers: list[str] = []
    # R3b: X-Robots-Tag header
    xrt = r["headers"].get("x-robots-tag", "")
    if xrt and _NOINDEX_RE.search(xrt):
        barriers.append(f"x-robots-tag:{xrt[:80]}")
    # R3a / R3c: parse head
    hp = _HeadParser()
    try:
        hp.feed(r["body"][:200_000])
    except Exception:
        pass
    for content in hp.meta_robots:
        if _NOINDEX_RE.search(content):
            barriers.append(f"meta-robots:{content[:60]}")
            break
    # R3c: canonical-away (ADVISORY per R3c open decision — reported, not auto-blocking)
    out["canonical"] = hp.canonical
    canonical_away = False
    if hp.canonical:
        cu = hp.canonical
        if cu.startswith("/"):
            pu = urlparse(url)
            cu = f"{pu.scheme}://{pu.netloc}{cu}"
        if _norm_url(cu) != _norm_url(r["final_url"]):
            canonical_away = True
            out["note"] = f"canonical->{cu} (ADVISORY: may be benign syndication)"
    # R3d: robots
    pu = urlparse(r["final_url"])
    dis, why = _robots_disallows(pu.netloc, pu.path or "/")
    if dis:
        barriers.append(why)

    out["barriers"] = barriers
    out["canonical_away_advisory"] = canonical_away
    # Hard barriers => blocked. Canonical-away alone stays advisory (not blocked).
    out["verdict"] = "blocked" if barriers else "ok"
    return out


def _sample_from_events() -> list[tuple[str, str]]:
    import sqlite3
    db = os.path.expanduser(
        os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR", "~/.config/backlink-publisher")
    )
    db = os.path.join(os.path.expanduser(db), "events.db")
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT live_url, host FROM articles WHERE live_url LIKE 'http%' AND live_url<>''"
    ).fetchall()
    con.close()
    # filter obvious placeholders
    bad = ("example.com", "blog.org", "x.com/a", "blogger.example.com", "/p/already-shipped")
    return [(u, h) for u, h in rows if not any(b in u for b in bad)]


def main(argv: list[str]) -> int:
    if "--from-events" in argv:
        pairs = _sample_from_events()
        urls = [u for u, _ in pairs]
        print(f"# sampled {len(urls)} real live_url(s) from events.db", file=sys.stderr)
    elif "--robots-only" in argv:
        hosts = [a for a in argv[1:] if not a.startswith("--")]
        for h in hosts:
            dis, why = _robots_disallows(h, "/")
            print(f"{h:30s} robots root: {why}")
        return 0
    else:
        urls = [a for a in argv[1:] if not a.startswith("--")]
    if not urls:
        print(__doc__)
        return 1

    results = [probe(u) for u in urls]
    by_chan: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        chan = urlparse(r["url"]).netloc
        by_chan[chan][r["verdict"]] += 1
        flag = {"ok": "✓", "blocked": "✗ BLOCKED", "unknown": "? unknown"}[r["verdict"]]
        extra = (" | " + ", ".join(r["barriers"])) if r["barriers"] else ""
        if r.get("canonical_away_advisory"):
            extra += f" | canonical-away(advisory)"
        print(f"{flag:12s} HTTP {str(r['status']):4s} {r['url']}{extra}")
        if r["note"]:
            print(f"             ↳ {r['note']}")

    print("\n=== per-channel ===")
    for chan, c in sorted(by_chan.items()):
        tot = sum(c.values())
        print(f"  {chan:32s} ok={c['ok']} blocked={c['blocked']} unknown={c['unknown']} "
              f"(unknown_rate={c['unknown']/tot:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
