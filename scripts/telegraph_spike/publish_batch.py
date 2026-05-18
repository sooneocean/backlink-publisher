#!/usr/bin/env python3
"""Telegraph Phase 0 batch publisher — operator helper for Unit 1.

Pipeline per post:
    markdown template (with {{TARGET_URL}} placeholder)
    └→ substitute target_url
    └→ Unit 3 ``markdown_to_telegraph_nodes`` (re-used: same converter the
       production adapter will eventually call)
    └→ POST https://api.telegra.ph/createPage (anonymous; one createAccount
       at startup)
    └→ GET the published URL and parse every ``<a>`` for ``rel`` + ``target``
    └→ append to a JSON results file and print a markdown table block
       pasteable directly into the report §3.

Operator usage::

    # Smoke (no network):
    python scripts/telegraph_spike/publish_batch.py --target-url https://51acgs.com --dry-run

    # Velocity burst (V1/V2/V3 within 24h):
    python scripts/telegraph_spike/publish_batch.py --target-url https://51acgs.com --velocity-only

    # The remaining 7 non-velocity pages:
    python scripts/telegraph_spike/publish_batch.py --target-url https://51acgs.com --no-velocity \
        --reuse-token /tmp/telegraph-phase0-token.json

Outputs (default ``--output-dir scripts/telegraph_spike/run_output/``):
    - ``telegraph-phase0-token.json`` (0600) — access_token + short_name
    - ``results.json`` — full per-page record (URL, slug, link attrs)
    - ``report-table.md`` — markdown table block for report §3

The script DOES NOT modify the report file in-place; the operator pastes
``report-table.md`` into the report to keep a clear audit trail of which
PR run produced which rows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# ── Minimal markdown → Telegraph Node converter (spike-local) ──
#
# The production adapter will use the full Unit 3 converter
# (``backlink_publisher.adapters.telegraph_node``). That converter is
# intentionally not imported here so Phase 0 PR is independent of the
# Unit 3 PR. The 10 operator-curated templates only use a small subset:
#   #/##         headings (h1 is consumed as Telegraph ``title``, ## → h3)
#   **bold**     inline emphasis → ``strong``
#   [text](url)  links → ``a``
#   - item       bullet list → ``ul``/``li``
#   blank line   paragraph separator
# Other markdown is rendered as plain text. The full whitelist /
# downgrade / UTF-8 budget machinery lives in Unit 3.
_LINK_OR_BOLD = __import__("re").compile(
    r"\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*")


def _inline(text: str) -> list:
    out: list = []
    cursor = 0
    for m in _LINK_OR_BOLD.finditer(text):
        if m.start() > cursor:
            out.append(text[cursor:m.start()])
        if m.group(1) is not None:
            out.append({"tag": "a",
                        "attrs": {"href": m.group(2)},
                        "children": [m.group(1)]})
        else:
            out.append({"tag": "strong", "children": [m.group(3)]})
        cursor = m.end()
    if cursor < len(text):
        out.append(text[cursor:])
    return out


def markdown_to_telegraph_nodes(
        md: str) -> tuple[list, dict[str, int], Optional[str]]:
    """Return (nodes, stats, title_consumed).

    The first ``# heading`` line is consumed and returned separately so it
    can be passed to Telegraph's ``createPage(title=...)`` argument
    rather than duplicated inside ``content``.
    """
    title: Optional[str] = None
    nodes: list = []
    list_buf: list = []

    def flush_list() -> None:
        if list_buf:
            nodes.append({"tag": "ul", "children": list(list_buf)})
            list_buf.clear()

    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(paragraph).strip()
            paragraph.clear()
            if text:
                nodes.append({"tag": "p", "children": _inline(text)})

    for raw in md.splitlines():
        line = raw.rstrip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("# ") and title is None:
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            nodes.append({"tag": "h3",
                          "children": _inline(line[3:].strip())})
            continue
        if line.startswith("- "):
            flush_paragraph()
            list_buf.append({"tag": "li",
                             "children": _inline(line[2:].strip())})
            continue
        paragraph.append(line)
    flush_paragraph()
    flush_list()

    anchors = sum(1 for n in _walk(nodes) if isinstance(n, dict)
                  and n.get("tag") == "a")
    utf8_bytes = len(json.dumps(nodes, ensure_ascii=False).encode("utf-8"))
    return nodes, {"anchors": anchors, "utf8_bytes": utf8_bytes,
                   "downgrades": 0}, title


def _walk(nodes):
    for n in nodes:
        yield n
        if isinstance(n, dict):
            yield from _walk(n.get("children", []))

API = "https://api.telegra.ph"
PLACEHOLDER = "{{TARGET_URL}}"
INTER_CALL_DELAY = 1.5  # seconds between createPage calls; gentle on the API

# (idx, group, velocity_marker, filename, title).
# Velocity assignments mirror the report's §3 table: V1=#4 (B group),
# V2=#5 (B group), V3=#7 (C group). These three pages must be published
# within a 24h window to make the velocity sub-experiment valid.
POSTS: list[tuple[int, str, str, str, str]] = [
    (1, "A", "",
     "A1-anime-recommendation-guide.md",
     "A Beginner's Roadmap for Anime Genre Exploration"),
    (2, "A", "",
     "A2-manga-reading-order-primer.md",
     "Reading-Order Pitfalls in Long-Running Manga Series"),
    (3, "A", "",
     "A3-seasonal-anime-tracking-habits.md",
     "Sustainable Habits for Seasonal Anime Tracking"),
    (4, "B", "V1",
     "B1-studio-comparison-essay.md",
     "How Studio Identity Shapes Anime Adaptations"),
    (5, "B", "V2",
     "B2-voice-acting-craft-notes.md",
     "Notes on Voice Acting as Performance, Not Decoration"),
    (6, "B", "",
     "B3-manga-platforms-overview.md",
     "A Practical Comparison of Manga Reading Platforms"),
    (7, "C", "V3",
     "C1-cosplay-photography-deepdive.md",
     "A Working Photographer's Notes on Cosplay Shoots"),
    (8, "C", "",
     "C2-doujin-circle-economics.md",
     "The Economics of Independent Doujin Circles"),
    (9, "C", "",
     "C3-soundtrack-composition-tour.md",
     "A Listener's Tour Through Anime Soundtrack Composition"),
    (10, "C", "",
     "C4-translation-workflows-fan-and-pro.md",
     "Translation Workflows: Fan Subs, Scanlation, and Professional Localisation"),
]


def create_account(short_name: str) -> str:
    r = requests.post(f"{API}/createAccount",
                      data={"short_name": short_name}, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise SystemExit(f"createAccount failed: {data}")
    return data["result"]["access_token"]


def publish_page(token: str, title: str,
                 nodes: list) -> tuple[str, str]:
    payload = {
        "access_token": token,
        "title": title,
        "content": json.dumps(nodes, ensure_ascii=False),
        "return_content": "false",
    }
    r = requests.post(f"{API}/createPage", data=payload, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise SystemExit(f"createPage failed for {title!r}: {data}")
    return data["result"]["url"], data["result"]["path"]


def fetch_link_attrs(url: str) -> list[dict[str, Optional[str]]]:
    """Return [{href, rel, target}] for every <a> in the published article.

    Uses the stdlib HTML parser to avoid a hard ``beautifulsoup4`` dep
    here (the production code already uses bs4, but this helper is
    spike-only and we want it runnable with stock requests).
    """
    from html.parser import HTMLParser

    class _A(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.anchors: list[dict[str, Optional[str]]] = []

        def handle_starttag(self, tag: str,
                            attrs: list[tuple[str, Optional[str]]]) -> None:
            if tag.lower() != "a":
                return
            a = dict(attrs)
            self.anchors.append({
                "href": a.get("href"),
                "rel": a.get("rel"),       # None if attribute is absent
                "target": a.get("target"),
            })

    r = requests.get(url, timeout=15)
    r.raise_for_status()
    parser = _A()
    parser.feed(r.text)
    return parser.anchors


def render_report_row(rec: dict, target_url: str) -> str:
    """One pipe-delimited row for report §3 table."""
    target_link = None
    norm_target = target_url.rstrip("/")
    for la in rec["link_attrs"]:
        href = la.get("href") or ""
        if href.rstrip("/").startswith(norm_target):
            target_link = la
            break

    if target_link is None:
        rel_str = "?"
        tgt_str = "?"
    else:
        rel_val = target_link.get("rel")
        rel_str = "null" if rel_val is None else str(rel_val)
        tgt_str = target_link.get("target") or "null"

    return (f"| {rec['idx']} | {rec['url']} | {rec['group']} | "
            f"{rec['velocity'] or '—'} | {rec['anchors_count']} | "
            f"{target_url} | {rel_str} | {tgt_str} | — | — | — | — | — |")


def select_posts(velocity_only: bool,
                 no_velocity: bool) -> list[tuple]:
    if velocity_only and no_velocity:
        raise SystemExit("--velocity-only and --no-velocity are mutually exclusive")
    if velocity_only:
        return [p for p in POSTS if p[2]]
    if no_velocity:
        return [p for p in POSTS if not p[2]]
    return list(POSTS)


def load_or_create_token(reuse_path: Optional[Path], short_name: str,
                         token_out: Path) -> str:
    if reuse_path and reuse_path.exists():
        token = json.loads(reuse_path.read_text())["access_token"]
        print(f"reusing token from {reuse_path}")
        return token
    token = create_account(short_name)
    token_out.parent.mkdir(parents=True, exist_ok=True)
    token_out.write_text(json.dumps(
        {"access_token": token, "short_name": short_name}, indent=2))
    os.chmod(token_out, 0o600)
    print(f"created account, token saved to {token_out} (0600)")
    return token


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--target-url", required=True,
                   help="Your main site URL (e.g. https://51acgs.com). "
                        "Replaces {{TARGET_URL}} in every template.")
    p.add_argument("--short-name", default="phase0-spike",
                   help="Telegraph account short_name (max 32 chars)")
    p.add_argument("--templates-dir", type=Path,
                   default=Path(__file__).resolve().parent / "post_templates")
    p.add_argument("--output-dir", type=Path,
                   default=Path(__file__).resolve().parent / "run_output")
    p.add_argument("--reuse-token", type=Path,
                   help="Reuse access_token from an earlier run (for the "
                        "non-velocity batch after the V1/V2/V3 burst)")
    p.add_argument("--dry-run", action="store_true",
                   help="Do not call the Telegraph API; just print what "
                        "would happen (uses Unit 3 converter to validate "
                        "every template + report anchors/byte stats)")
    p.add_argument("--velocity-only", action="store_true",
                   help="Publish only V1 / V2 / V3 (3 pages, for the 24h "
                        "velocity sub-experiment)")
    p.add_argument("--no-velocity", action="store_true",
                   help="Publish only the non-velocity pages (7 pages)")
    args = p.parse_args()

    selected = select_posts(args.velocity_only, args.no_velocity)
    print(f"selected {len(selected)} post(s) "
          f"({'velocity-only' if args.velocity_only else 'no-velocity' if args.no_velocity else 'full batch'})")

    # ── dry-run: validates every template via Unit 3 without touching API ──
    if args.dry_run:
        for idx, group, vel, fname, declared_title in selected:
            md = (args.templates_dir / fname).read_text().replace(
                PLACEHOLDER, args.target_url)
            nodes, stats, parsed_title = markdown_to_telegraph_nodes(md)
            mismatch = "" if parsed_title == declared_title else \
                f"  ⚠ title mismatch: parsed={parsed_title!r}"
            print(f"  #{idx:2} {group} {vel:3} {fname:48} "
                  f"anchors={stats['anchors']:2}  "
                  f"utf8_bytes={stats['utf8_bytes']:6}{mismatch}")
        print("\n[dry-run] no Telegraph API calls made")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    token_out = args.output_dir / "telegraph-phase0-token.json"
    token = load_or_create_token(args.reuse_token, args.short_name, token_out)

    results: list[dict] = []
    for idx, group, vel, fname, declared_title in selected:
        md = (args.templates_dir / fname).read_text().replace(
            PLACEHOLDER, args.target_url)
        nodes, stats, parsed_title = markdown_to_telegraph_nodes(md)
        title = parsed_title or declared_title
        url, slug = publish_page(token, title, nodes)
        time.sleep(INTER_CALL_DELAY)
        try:
            link_attrs = fetch_link_attrs(url)
            fetch_error = None
        except Exception as exc:
            link_attrs = []
            fetch_error = repr(exc)
        rec = {
            "idx": idx,
            "group": group,
            "velocity": vel,
            "title": title,
            "filename": fname,
            "url": url,
            "slug": slug,
            "anchors_count": stats["anchors"],
            "utf8_bytes": stats["utf8_bytes"],
            "downgrades": stats["downgrades"],
            "link_attrs": link_attrs,
            "fetch_error": fetch_error,
        }
        results.append(rec)
        rel_target = next((la for la in link_attrs
                           if (la.get("href") or "").startswith(
                               args.target_url.rstrip("/"))), None)
        rel_t0 = "null" if rel_target and rel_target.get("rel") is None else (
            str(rel_target.get("rel")) if rel_target else "?")
        tgt_t0 = (rel_target.get("target") if rel_target else "?") or "null"
        print(f"  #{idx:2} {group} {vel:3} → {url}")
        print(f"       rel_t0={rel_t0!r}  target_t0={tgt_t0!r}  "
              f"total_anchors_on_page={len(link_attrs)}")

    # Persist results + pasteable table
    results_path = args.output_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    table_path = args.output_dir / "report-table.md"
    rows = [render_report_row(r, args.target_url) for r in results]
    table_path.write_text("\n".join(rows) + "\n")

    print(f"\nresults written to {results_path}")
    print(f"report rows  to    {table_path}")
    print(f"\n--- paste below into report §3 (filter by idx if partial run) ---\n")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
