"""Markdown utilities and template engine for the backlink pipeline."""

from __future__ import annotations

import re
from typing import Any

_mdit_instance = None


def _get_mdit():
    global _mdit_instance
    if _mdit_instance is None:
        from markdown_it import MarkdownIt
        mdit = MarkdownIt("commonmark").enable(["table", "strikethrough"])
        default_link_open = mdit.renderer.rules.get("link_open")

        def _link_open(tokens, idx, options, env):
            token = tokens[idx]
            token.attrSet("target", "_blank")
            token.attrSet("rel", "noopener")
            if default_link_open is not None:
                return default_link_open(tokens, idx, options, env)
            return mdit.renderer.renderToken(tokens, idx, options, env)

        mdit.renderer.rules["link_open"] = _link_open
        _mdit_instance = mdit
    return _mdit_instance


def render_to_html(md: str) -> str:
    """Render markdown to HTML using markdown-it-py (CommonMark + GFM extras).

    Links are rendered without nofollow — backlinks must be dofollow — and with
    ``target="_blank" rel="noopener"`` so that clicking a link opens it in a
    new tab (preserving dwell time on the host article) without exposing the
    opener window via ``window.opener``.
    """
    if not md:
        return ""
    return _get_mdit().render(md)


_URL_MODE_OFFSETS = {"A": 0, "B": 1, "C": 2}


def select_anchor_keywords(
    keywords: list[str],
    url_mode: str,
    count: int,
) -> list[str] | None:
    """Pick ``count`` anchor keywords from ``keywords`` deterministically.

    The selection formula is ``keywords[(i + offset) % len(keywords)]`` where
    ``offset`` depends on ``url_mode`` (A=0, B=1, C=2; any other value is
    treated as 0). This guarantees that the same article configuration always
    produces the same anchor distribution, while a varied ``url_mode`` mix
    across articles naturally rotates which keyword anchors which slot.

    Returns ``None`` when the keyword pool is empty — that signal is the
    caller's cue to fall back to bare-domain anchor text.
    """
    if not keywords:
        return None
    offset = _URL_MODE_OFFSETS.get(url_mode, 0)
    n = len(keywords)
    return [keywords[(i + offset) % n] for i in range(count)]


def validate_markdown_convertible(md: str) -> bool:
    """Basic check that markdown content is plausible and non-empty."""
    stripped = md.strip()
    if not stripped:
        return False
    text_only = re.sub(r"[#*_\-\[\]\(\)!`~]", "", stripped)
    text_only = re.sub(r"\s+", " ", text_only).strip()
    return len(text_only) > 5


# ─── zh-CN short-form article generator ─────────────────────────────────────
#
# Replaces the legacy ``_zh_body_a/b/c`` templates for the zh-CN path. Output
# is HTML (not Markdown) containing 2-3 ``<a>`` tags wrapping anchor text in
# the prose, no ``## References`` section, no density paragraph.
#
# Target plain-text body length: 150-200 characters. Templates aim for ~165
# chars at median input (5-char keyword, 5-char anchors); short fills get
# padded with a random filler clause, the rare overflow gets surfaced as a
# warning to the caller via length but is not auto-trimmed (Unit 8's
# validator is the strict 150-200 gate).

_ZH_SHORT_TARGET_MIN: int = 150
_ZH_SHORT_TARGET_MAX: int = 200

# 6 body templates, each with both a 2-secondary and 1-secondary variant.
# Style intentionally varied across openings to avoid programmatic
# fingerprinting: discovery, friend recommendation, direct pitch, forum
# mention, personal experience, station sharing.
_ZH_SHORT_TEMPLATES_2SEC: tuple[str, ...] = (
    "最近一直在追 {kw} 这一类的内容更新，圈子里相关讨论也挺热闹。前段时间偶然发现 "
    "{main}，整体使用体验比想象中要好——资源相对齐全，分类整理也算用心，搜索体验也"
    "比较顺畅。日常我会顺手刷 {sec1} 看看最近的新进度和热门作品，{sec2} 那一块也挺"
    "值得花时间慢慢翻看。属于愿意收藏长期跟进的一个站点，喜欢这类内容的朋友可以"
    "试试。",
    "最近周围不少朋友都在聊 {kw} 相关的内容更新，自己也跟着试了一段时间下来。比较"
    "稳定的渠道是 {main}，更新节奏不算慢，分类导航做得也清楚，几个常用入口都很顺"
    "手好找。除此之外 {sec1} 也是日常会扫一眼的页面，里头的整理偏精选风格，{sec2} "
    "也值得收藏一下慢慢翻看。整体逛起来比一般聚合站好用不少。",
    "想找 {kw} 相关内容的朋友可以试试 {main}，用了一阵子下来作品库还算齐全，更新频"
    "率也算比较稳定，分类整理用心，几个常用入口都很顺手好找。日常我会刷 {sec1} 看"
    "最近的新进展和热门更新，顺手再看一眼 {sec2} 里的精选作品，能挖到不少之前没注"
    "意过的小众内容。整体属于值得长期收藏的一个站点。",
    "在论坛上看到有人推荐 {kw} 相关的资源整理，自己也跟着试了试 {main}，整体来说作"
    "品库比较全，更新频率算稳定，页面加载速度也还行，分类入口好找。日常会在 {sec1} "
    "里翻翻新作和热门更新，{sec2} 偶尔也会逛一下，能挖到一些冷门但质量不错的作品，"
    "体验比想象中好。属于愿意长期跟着看的站点。",
    "用 {main} 看 {kw} 相关内容已经有一段时间了，整体感受比预期要好不少。资源相对"
    "齐全，分类整理细致，搜索功能也比较好用，几个常用入口都顺手好找，加载速度也算"
    "稳定。日常 {sec1} 是必刷的页面，{sec2} 偶尔也会翻一翻，能发现一些之前没注意过"
    "的精选作品，整体推荐给同样口味的朋友。",
    "分享一个最近经常在用的站点 {main}，主要用来看 {kw} 相关的内容，作品比较全更新"
    "也勤快。资源整理偏精细，{sec1} 那一块的更新值得花点时间慢慢翻，{sec2} 和分类"
    "区也都挺方便，几个常用入口都很顺手好找。整体逛起来体验顺畅，属于愿意持续收藏"
    "长期跟进的一个不错的站点，推荐给同好。",
)

_ZH_SHORT_TEMPLATES_1SEC: tuple[str, ...] = (
    "最近一直在追 {kw} 这一类的内容更新，圈子里相关讨论也挺热闹。前段时间偶然发现 "
    "{main}，整体使用体验比想象中要好——资源相对齐全，分类整理也算用心，搜索体验也"
    "比较顺畅，加载速度也算稳定可靠。日常我会顺手刷 {sec1} 看看最近的新进度和热门"
    "精选作品，整体属于愿意收藏长期跟进的站点，喜欢的朋友可以试试。",
    "最近周围不少朋友都在聊 {kw} 相关的内容更新，自己也跟着试了一段时间下来。比较"
    "稳定的渠道是 {main}，更新节奏不算慢，分类导航做得也清楚，几个常用入口都很顺"
    "手好找，资源整理也用心。除此之外 {sec1} 也是日常我会扫一眼的页面，里头的整理"
    "偏精选风格，整体逛起来比一般聚合站好用不少。",
    "想找 {kw} 相关内容的朋友可以试试 {main}，用了一阵子下来作品库还算齐全，更新频"
    "率也算比较稳定，分类整理用心，几个常用入口都顺手好找，搜索体验也不错。日常我"
    "会刷 {sec1} 看最近的新进展和精选内容，能发现一些之前没注意过的冷门作品，整体"
    "属于值得长期收藏的一个站点。",
    "在论坛上看到有人推荐 {kw} 相关的资源整理，自己也跟着试了试 {main}，整体来说作"
    "品库比较全，更新频率算稳定，页面加载速度也还可以，分类导航做得清楚。日常会在 "
    "{sec1} 里翻翻新作和精选区，能挖到一些冷门但质量不错的小众作品，属于愿意长期跟"
    "着看下去的一个站点，比聚合站好用。",
    "用 {main} 看 {kw} 相关内容已经有一段时间了，整体感受比预期要好不少。资源相对"
    "齐全，分类整理细致，搜索功能也比较好用，几个常用入口都顺手好找，加载速度也算"
    "稳定可靠。日常 {sec1} 是我必刷的页面，偶尔翻一翻能发现一些之前没注意过的精选"
    "作品，整体推荐给同样口味的朋友。",
    "分享一个最近经常在用的站点 {main}，主要用来看 {kw} 相关的内容，作品比较全更新"
    "也算勤快，分类导航做得清楚。资源整理偏精细，{sec1} 那一块的更新值得花点时间"
    "慢慢翻，能挖到一些冷门但质量很好的作品。整体逛起来体验顺畅，属于愿意持续收藏"
    "长期跟进的不错站点。",
)

# Filler clauses appended when a generated body falls short of 150 chars.
# Each is 15-22 chars so 1-3 appends covers the typical shortfall. Phrases
# are intentionally generic (no anchor or keyword) so they read naturally
# tacked on after any template body.
_ZH_SHORT_FILLERS: tuple[str, ...] = (
    "如果你也喜欢这一类的内容可以收藏起来慢慢看。",
    "总体来说是个值得长期保存收藏的不错站点。",
    "推荐给同样口味喜欢这类内容的同好朋友们。",
    "整体上是体验顺畅且让人想长期回访的选择。",
    "希望这个分享对在找类似站点的朋友有点用。",
)


def render_zh_short_article(
    keyword: str,
    main_domain: str,
    main_anchor: str,
    secondary_links: list[tuple[str, str]],
    style_seed: int = 0,
) -> str:
    """Render a 150-200-character zh-CN backlink short article as HTML.

    Produces a single paragraph of natural-tone Chinese prose containing
    exactly ``1 + len(secondary_links)`` ``<a>`` tags — one main link to
    ``main_domain`` and 1-2 secondary links to the URLs in ``secondary_links``.
    All anchors carry ``target="_blank" rel="noopener noreferrer"``.

    ``secondary_links`` is a list of ``(url, anchor_text)`` tuples; the
    scheduler is expected to pass 1 or 2 entries. Other counts raise
    ``InputValidationError`` — the short-form contract is 2-3 total links.

    ``style_seed`` selects a template variant deterministically. Different
    seeds yield different opening phrasing so a batch of 50+ articles to one
    site doesn't look programmatically identical.

    Length contract: aims for [150, 200] plain-character body. Templates land
    near 165 chars at median input; short renders are padded with random
    filler clauses keyed off the seed. Overflows (>200) are not auto-trimmed;
    Unit 8's validator is the strict gate and triggers retry/degrade if it
    sees one.
    """
    from backlink_publisher._util.errors import InputValidationError

    n_sec = len(secondary_links)
    if n_sec not in (1, 2):
        raise InputValidationError(
            f"zh-CN short article requires 1 or 2 secondary links, got {n_sec}"
        )

    templates = _ZH_SHORT_TEMPLATES_2SEC if n_sec == 2 else _ZH_SHORT_TEMPLATES_1SEC
    template = templates[style_seed % len(templates)]

    main_html = _format_anchor_html(main_domain, main_anchor)
    sec_htmls = [_format_anchor_html(url, anchor) for url, anchor in secondary_links]

    fmt_args: dict[str, str] = {"kw": keyword, "main": main_html, "sec1": sec_htmls[0]}
    if n_sec == 2:
        fmt_args["sec2"] = sec_htmls[1]

    body = template.format(**fmt_args)

    # Pad with filler clauses until we clear the 150-char minimum or run out
    # of distinct fillers (5 max). Each filler is appended once at most so
    # back-to-back identical articles don't all end with the same phrase.
    fillers_used: set[int] = set()
    for offset in range(len(_ZH_SHORT_FILLERS)):
        if len(_strip_html(body)) >= _ZH_SHORT_TARGET_MIN:
            break
        idx = (style_seed + offset) % len(_ZH_SHORT_FILLERS)
        if idx in fillers_used:
            continue
        fillers_used.add(idx)
        body += _ZH_SHORT_FILLERS[idx]

    return body


def _format_anchor_html(
    url: str, anchor: str, *, rel: str = "noopener noreferrer"
) -> str:
    """Return ``<a target="_blank" rel="...">`` HTML for ``anchor``.

    Built by hand rather than via markdown-it because brainstorm R4 mandates
    a specific ``rel`` value (the existing ``_link_open`` hook only emits
    ``noopener``). URL is HTML-attribute escaped for safety; anchor text is
    NOT escaped — the anchor_resolver's ``_passes_filters`` already rejects
    structural HTML chars so injecting raw text is safe at this layer.

    ``rel`` is parameterised (Plan 2026-05-13-004 Unit 4): the work-themed
    generator passes ``rel="noopener"`` to keep dofollow weight intact while
    Medium/Blogger long-form callers keep the default ``noopener noreferrer``.
    """
    safe_url = (
        url.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f'<a href="{safe_url}" target="_blank" rel="{rel}">{anchor}</a>'


def _strip_html(text: str) -> str:
    """Strip HTML tags for plain-character length measurement."""
    return re.sub(r"<[^>]+>", "", text)


def validate_zh_short_payload(
    html: str,
    expected_anchors: list[str],
) -> tuple[bool, list[str]]:
    """Validate a zh-CN short-article HTML payload against the contract.

    Six checks, returned as ``(ok, errors)``:

    1. Plain-text body length 150-200 characters (HTML tags stripped).
    2. Exactly 2 or 3 ``<a>`` tags — the short-form contract.
    3. Every ``<a>`` tag carries ``target="_blank"`` AND ``rel="noopener noreferrer"``.
    4. Every anchor text passes ``_passes_filters`` from anchor_resolver
       (2-8 chars, not in FORBIDDEN_ANCHOR_TEXTS, no unsafe characters, ≥50% CJK).
    5. Every anchor text appears in ``expected_anchors`` — guards against
       generator bugs that inject anchor text the resolver never decided.
    6. No bare URL outside an ``<a>`` tag (brainstorm R4 prohibition).

    ``ok`` is True only when all six pass. ``errors`` lists every distinct
    failure so the validator pipeline can log all of them instead of bailing
    at the first one. Unit 8's retry/degrade lifts off this signal.
    """
    # Imported locally so the markdown_utils module doesn't acquire a runtime
    # dependency on the resolver — keeps the module graph clean.
    from backlink_publisher.anchor.resolver import _passes_filters

    errors: list[str] = []

    plain = _strip_html(html)
    plain_len = len(plain)
    if plain_len < _ZH_SHORT_TARGET_MIN:
        errors.append(f"plain_text_length_below_{_ZH_SHORT_TARGET_MIN}:{plain_len}")
    elif plain_len > _ZH_SHORT_TARGET_MAX:
        errors.append(f"plain_text_length_above_{_ZH_SHORT_TARGET_MAX}:{plain_len}")

    # Capture every <a ...>anchor</a> pair so we can re-check both the tag
    # attributes (#3) and the anchor text (#4 & #5) in one pass.
    anchor_pattern = re.compile(r"<a(\s[^>]*)>([^<]*)</a>", re.IGNORECASE)
    matches = anchor_pattern.findall(html)
    anchor_count = len(matches)
    if anchor_count < 2 or anchor_count > 3:
        errors.append(f"anchor_count_out_of_range:{anchor_count}")

    expected_set = set(expected_anchors)
    for attrs, anchor_text in matches:
        if 'target="_blank"' not in attrs:
            errors.append(f"missing_target_blank:{anchor_text}")
        if 'rel="noopener noreferrer"' not in attrs:
            errors.append(f"missing_rel_noopener_noreferrer:{anchor_text}")
        if not _passes_filters(anchor_text):
            errors.append(f"anchor_failed_filters:{anchor_text}")
        if anchor_text not in expected_set:
            errors.append(f"unexpected_anchor_text:{anchor_text}")

    # Check #6: bare URLs outside <a> tags. Strip every <a>...</a> region first
    # (the href inside an anchor is legitimate), then scan the remainder.
    stripped = re.sub(r"<a\s[^>]*>.*?</a>", "", html, flags=re.IGNORECASE)
    if re.search(r"https?://", stripped):
        errors.append("bare_url_outside_anchor")

    return (not errors, errors)


def format_link_md(url: str, anchor: str) -> str:
    """Format a link as a Markdown hyperlink."""
    return f"[{anchor}]({url})"


def links_to_markdown(links: list[dict[str, Any]]) -> str:
    """Convert a list of link dicts to a markdown links section."""
    lines: list[str] = []
    for link in links:
        url = link.get("url", "")
        anchor = link.get("anchor", url)
        kind = link.get("kind", "supporting")
        md_link = format_link_md(url, anchor)
        lines.append(f"- [{kind}] {md_link}")
    return "\n".join(lines)


def slugify(text: str) -> str:
    """Generate a URL-safe slug from text."""
    import unicodedata

    value = str(text)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).lower().strip()
    return re.sub(r"[-\s]+", "-", value)
