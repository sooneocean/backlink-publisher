#!/usr/bin/env python3
"""Velog Phase 0 P0-2 — JWT storage location probe.

Opens velog.io in a headed Chromium window. The operator completes
social login manually. After login, the script dumps
``context.cookies()`` + ``localStorage`` into a JSON file and prints a
short summary so we can decide whether persistence should be a cookie
jar or Playwright's ``storage_state`` blob (R9 in the brainstorm).

This is a spike helper — **not** part of the production adapter. The
real ``backlink-publisher velog-login`` command will be designed once
Phase 0 closes.

Usage::

    python scripts/velog_spike/dump_session.py --output /tmp/velog-session.json

Then paste the printed candidate fields into the §3 table of
``docs/phase0/2026-05-15-velog-spike-report.md``.

Scope notes
-----------
- Cookies are filtered to ``*.velog.io`` to avoid leaking IdP cookies
  (Google / GitHub / Facebook) into the dump file. This is the same
  R16 invariant the production ``velog-login`` will enforce.
- The script does not include any credentials — login is fully manual.
- The output file is written 0600.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

VELOG_LOGIN_URL = "https://velog.io"
ALLOWED_COOKIE_DOMAINS = (".velog.io", "velog.io", "v3.velog.io")
JWT_HINTS = ("token", "jwt", "auth", "session")


def _is_velog_domain(domain: str) -> bool:
    domain = domain.lstrip(".")
    return any(domain == d.lstrip(".") or domain.endswith("." + d.lstrip("."))
               for d in ALLOWED_COOKIE_DOMAINS)


def _flag_jwt_candidates(items: list[dict[str, Any]], key_field: str) -> list[str]:
    out = []
    for item in items:
        name = str(item.get(key_field, "")).lower()
        if any(hint in name for hint in JWT_HINTS):
            out.append(item[key_field])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True, type=Path,
                        help="Path to dump JSON (will be chmod 0600)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright "
              "&& playwright install chromium", file=sys.stderr)
        return 2

    print(f"Opening {VELOG_LOGIN_URL} ... complete social login in the window, "
          "then press Enter here.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(VELOG_LOGIN_URL)

        try:
            input("Press Enter after you see your velog profile loaded > ")
        except EOFError:
            print("ERROR: stdin closed without confirmation — aborting",
                  file=sys.stderr)
            browser.close()
            return 3

        raw_cookies = context.cookies()
        velog_cookies = [c for c in raw_cookies if _is_velog_domain(c.get("domain", ""))]
        leaked_idp = [c for c in raw_cookies if not _is_velog_domain(c.get("domain", ""))]

        local_storage = page.evaluate(
            "() => Object.fromEntries(Object.entries(localStorage))"
        )

        browser.close()

    cookie_jwt_candidates = _flag_jwt_candidates(velog_cookies, "name")
    ls_jwt_candidates = [k for k in local_storage if any(h in k.lower() for h in JWT_HINTS)]

    payload = {
        "cookies_velog_scope_only": velog_cookies,
        "local_storage": local_storage,
        "summary": {
            "cookie_count_velog": len(velog_cookies),
            "cookie_count_filtered_out_non_velog": len(leaked_idp),
            "non_velog_domains_seen": sorted({c.get("domain", "?") for c in leaked_idp}),
            "local_storage_keys": sorted(local_storage.keys()),
            "jwt_candidates_in_cookies": cookie_jwt_candidates,
            "jwt_candidates_in_local_storage": ls_jwt_candidates,
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    os.chmod(args.output, 0o600)

    s = payload["summary"]
    print(f"\nWrote {args.output} (0600)")
    print(f"  velog cookies        : {s['cookie_count_velog']}")
    print(f"  filtered IdP cookies : {s['cookie_count_filtered_out_non_velog']} "
          f"(domains: {s['non_velog_domains_seen']})")
    print(f"  localStorage keys    : {s['local_storage_keys']}")
    print(f"  JWT? (cookies)       : {cookie_jwt_candidates or '(none matched hints)'}")
    print(f"  JWT? (localStorage)  : {ls_jwt_candidates or '(none matched hints)'}")
    print("\nNext: paste the above into §3 of the velog spike report and "
          "decide R9 persistence format.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
