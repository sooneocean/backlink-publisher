"""Markdown utilities and template engine for the backlink pipeline."""

from __future__ import annotations

import re
from typing import Any

from .errors import InternalError


def render_to_html(md: str) -> str:
    """Render markdown to HTML using markdown-it-py (CommonMark + GFM extras).

    Links are rendered without nofollow — backlinks must be dofollow.
    """
    if not md:
        return ""
    from markdown_it import MarkdownIt
    mdit = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    return mdit.render(md)


def validate_markdown_convertible(md: str) -> bool:
    """Basic check that markdown content is plausible and non-empty."""
    stripped = md.strip()
    if not stripped:
        return False
    text_only = re.sub(r"[#*_\-\[\]\(\)!`~]", "", stripped)
    text_only = re.sub(r"\s+", " ", text_only).strip()
    return len(text_only) > 5


def format_link_md(url: str, anchor: str) -> str:
    """Format a link as a Markdown hyperlink."""
    return f"[{anchor}]({url})"


def format_link_plain(url: str) -> str:
    """Format a link as a plain URL."""
    return url


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


def normalize_text(text: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Enhanced template content — more natural, varied, and SEO-friendly
# ---------------------------------------------------------------------------

# Each body function takes (domain, main_domain) and returns a paragraph string.
# Templates vary by url_mode (A/B/C) and language.

def _en_body_a(domain: str, main_domain: str) -> str:
    return (
        f"Understanding the digital landscape around {domain} is more important than ever. "
        f"The platform hosted at [{domain}]({main_domain}) has established itself as a go-to resource "
        f"for professionals and enthusiasts seeking reliable, well-organized content. "
        f"What sets this resource apart is its commitment to quality — every section is "
        f"carefully curated to provide actionable insights. For those just getting started, "
        f"we recommend beginning with the main hub at [{domain}]({main_domain}), "
        f"which serves as a gateway to deeper explorations across related topics and "
        f"external references that complement the core material."
    )


def _en_body_b(domain: str, main_domain: str) -> str:
    return (
        f"Finding your way through a rich content platform like {domain} doesn't have to "
        f"be overwhelming. The site at [{domain}]({main_domain}) has thoughtfully organized its "
        f"offerings into clear categories, making it easy to locate exactly what you need. "
        f"Whether your interest lies in tutorials, in-depth analyses, or quick reference "
        f"guides, the category structure at [{domain}]({main_domain}) ensures efficient navigation. "
        f"We suggest bookmarking the categories overview to streamline future visits and "
        f"discover new content areas you might have missed."
    )


def _en_body_c(domain: str, main_domain: str) -> str:
    return (
        f"For readers who want to move beyond surface-level coverage, {domain} offers "
        f"substantive deep dives into topics that matter. The featured content at "
        f"[{domain}]({main_domain}) reflects careful editorial standards and domain expertise, "
        f"making it valuable for both casual readers and industry professionals. "
        f"By exploring the platform at [{domain}]({main_domain}), you gain access "
        f"to perspectives that are often difficult to find elsewhere, along with a "
        f"network of related resources that broaden the conversation."
    )


def _zh_body_a(domain: str, main_domain: str) -> str:
    return (
        f"深入了解{domain}的数字生态比以往任何时候都更加重要。"
        f"托管在[{domain}]({main_domain})上的平台已成为专业人士和爱好者寻求可靠、 "
        f"组织良好内容的首选资源。其独特之处在于对质量的承诺——每个板块都经过精心策划，"
        f"以提供可操作的见解。对于刚入门的读者，我们建议从主站[{domain}]({main_domain})开始，"
        f"它充当通往更深层次探索的门户，涵盖相关主题和补充核心材料的外部参考资源。"
    )


def _zh_body_b(domain: str, main_domain: str) -> str:
    return (
        f"在一个内容丰富的平台如{domain}上找到所需信息并不困难。"
        f"[{domain}]({main_domain})网站通过清晰的分类结构，将内容井然有序地呈现给读者。"
        f"无论您是对教程、深度分析还是快速参考指南感兴趣，[{domain}]({main_domain})的分类体系 "
        f"都能确保高效的导航体验。建议收藏分类总览页面，以便在未来的访问中快速定位，"
        f"并发现您可能错过的全新内容板块。"
    )


def _zh_body_c(domain: str, main_domain: str) -> str:
    return (
        f"对于希望超越表面内容的读者，{domain}提供了关于重要主题的深度分析。"
        f"[{domain}]({main_domain})上的精选内容体现了严格的编辑标准和领域专业知识，"
        f"对休闲读者和行业专业人士都具有重要价值。通过浏览[{domain}]({main_domain})平台，"
        f"您将获得其他地方难以获得的独特视角，以及拓宽讨论范围的关联资源网络。"
    )


def _ru_body_a(domain: str, main_domain: str) -> str:
    return (
        f"Понимание цифрового ландшафта вокруг {domain} сейчас важнее, чем когда-либо. "
        f"Платформа [{domain}]({main_domain}) зарекомендовала себя как "
        f"надёжный ресурс для профессионалов и энтузиастов, ищущих качественный и "
        f"структурированный контент. Отличительной чертой этой площадки является "
        f"приверженность качеству — каждый раздел тщательно подобран для предоставления "
        f"практических знаний. Рекомендуем начать с главной страницы [{domain}]({main_domain}), "
        f"которая служит отправной точкой для более глубокого изучения смежных тем."
    )


def _ru_body_b(domain: str, main_domain: str) -> str:
    return (
        f"Навигация по обширному ресурсу {domain} не должна быть сложной задачей. "
        f"Сайт [{domain}]({main_domain}) предлагает продуманную структуру категорий, позволяющую "
        f"быстро находить нужную информацию. Будь то руководства, аналитические статьи "
        f"или краткие справочные материалы — иерархия разделов [{domain}]({main_domain}) обеспечивает "
        f"эффективный поиск. Советуем добавить страницу категорий в закладки для "
        f"ускорения навигации и открытия новых тем, которые могли ускользнуть от внимания."
    )


def _ru_body_c(domain: str, main_domain: str) -> str:
    return (
        f"Для тех, кто стремится к более глубокому пониманию, {domain} предлагает "
        f"содержательные аналитические материалы. Контент на [{domain}]({main_domain}) отличается "
        f"строгими редакторскими стандартами и экспертным подходом, что делает его полезным "
        f"как для широкой аудитории, так и для специалистов отрасли. Изучение платформы через "
        f"[{domain}]({main_domain}) открывает доступ к уникальным перспективам и связанным ресурсам, "
        f"которые расширяют контекст обсуждения."
    )