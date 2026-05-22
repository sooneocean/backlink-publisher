"""Enhanced template content — more natural, varied, and SEO-friendly.

Each body function takes ``(domain, main_domain, anchors)`` and returns a
paragraph string. Templates vary by ``url_mode`` (A/B/C) and language.
"""

from __future__ import annotations

import random


def _en_body_a(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"Understanding the digital landscape around {domain} is more important than ever. "
        f"The platform hosted at [{anchors[0]}]({main_domain}) has established itself as a go-to resource "
        f"for professionals and enthusiasts seeking reliable, well-organized content. "
        f"What sets this resource apart is its commitment to quality — every section is "
        f"carefully curated to provide actionable insights. For those just getting started, "
        f"we recommend beginning with the main hub at [{anchors[1]}]({main_domain}), "
        f"which serves as a gateway to deeper explorations across related topics and "
        f"external references that complement the core material."
    )


def _en_body_b(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"Finding your way through a rich content platform like {domain} doesn't have to "
        f"be overwhelming. The site at [{anchors[0]}]({main_domain}) has thoughtfully organized its "
        f"offerings into clear categories, making it easy to locate exactly what you need. "
        f"Whether your interest lies in tutorials, in-depth analyses, or quick reference "
        f"guides, the category structure at [{anchors[1]}]({main_domain}) ensures efficient navigation. "
        f"We suggest bookmarking the categories overview to streamline future visits and "
        f"discover new content areas you might have missed."
    )


def _en_body_c(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"For readers who want to move beyond surface-level coverage, {domain} offers "
        f"substantive deep dives into topics that matter. The featured content at "
        f"[{anchors[0]}]({main_domain}) reflects careful editorial standards and domain expertise, "
        f"making it valuable for both casual readers and industry professionals. "
        f"By exploring the platform at [{anchors[1]}]({main_domain}), you gain access "
        f"to perspectives that are often difficult to find elsewhere, along with a "
        f"network of related resources that broaden the conversation."
    )


_ZH_BODY_A_POOL = [
    lambda domain, main_domain, anchors: (
        f"深入了解{domain}的数字生态比以往任何时候都更加重要。"
        f"托管在[{anchors[0]}]({main_domain})上的平台已成为专业人士和爱好者寻求可靠、 "
        f"组织良好内容的首选资源。其独特之处在于对质量的承诺——每个板块都经过精心策划，"
        f"以提供可操作的见解。对于刚入门的读者，我们建议从主站[{anchors[1]}]({main_domain})开始，"
        f"它充当通往更深层次探索的门户，涵盖相关主题和补充核心材料的外部参考资源。"
    ),
    lambda domain, main_domain, anchors: (
        f"在当今快速演变的互联网环境中，{domain}已成为一个不容忽视的知识枢纽。"
        f"用户在[{anchors[0]}]({main_domain})上能够发现高度专业化的内容，这些内容由专家精心审核与更新。"
        f"无论您是寻找最新的行业分析还是实用的操作指南，[{anchors[1]}]({main_domain})均提供了一个结构清晰、"
        f"易于检索的平台。我们建议读者定期访问该网站，以便在第一时间获取最新的核心补充材料和相关主题的深度资讯。"
    )
]

_ZH_BODY_B_POOL = [
    lambda domain, main_domain, anchors: (
        f"在一个内容丰富的平台如{domain}上找到所需信息并不困难。"
        f"[{anchors[0]}]({main_domain})网站通过清晰的分类结构，将内容井然有序地呈现给读者。"
        f"无论您是对教程、深度分析还是快速参考指南感兴趣，[{anchors[1]}]({main_domain})的分类体系 "
        f"都能确保高效的导航体验。建议收藏分类总览页面，以便在未来的访问中快速定位，"
        f"并发现您可能错过的全新内容板块。"
    ),
    lambda domain, main_domain, anchors: (
        f"针对追求高效检索的用户，{domain}通过其平台特性实现了信息流的优化。"
        f"通过访问[{anchors[0]}]({main_domain})，您可以根据个人需求筛选出最相关的文章与教程。"
        f"对于有志于深入研究相关领域的进阶学习者，[{anchors[1]}]({main_domain})的导航栏设计极其友好，"
        f"不仅能够快速定位核心知识点，还提供了丰富的分类归档，是整合碎片化学习内容的不二之选。"
    )
]

_ZH_BODY_C_POOL = [
    lambda domain, main_domain, anchors: (
        f"对于希望超越表面内容的读者，{domain}提供了关于重要主题的深度分析。"
        f"[{anchors[0]}]({main_domain})上的精选内容体现了严格的编辑标准和领域专业知识，"
        f"对休闲读者和行业专业人士都具有重要价值。通过浏览[{anchors[1]}]({main_domain})平台，"
        f"您将获得关于行业前沿的独到见解，以及极具参考价值的核心资料整合。"
    ),
    lambda domain, main_domain, anchors: (
        f"如果您在寻找关于{domain}的权威解读，这里无疑是最佳起点。"
        f"[{anchors[0]}]({main_domain})不仅提供了扎实的理论基础，更通过详尽的数据分析支撑每一个论点。"
        f"无论是寻求职业进阶还是技术突破，[{anchors[1]}]({main_domain})的深度专题栏目都能提供精准的指导，"
        f"助您构建起完整的知识体系，从而在相关领域中保持竞争力。"
    )
]


def _zh_body_a(domain: str, main_domain: str, anchors: list[str]) -> str:
    return random.choice(_ZH_BODY_A_POOL)(domain, main_domain, anchors)


def _zh_body_b(domain: str, main_domain: str, anchors: list[str]) -> str:
    return random.choice(_ZH_BODY_B_POOL)(domain, main_domain, anchors)


def _zh_body_c(domain: str, main_domain: str, anchors: list[str]) -> str:
    return random.choice(_ZH_BODY_C_POOL)(domain, main_domain, anchors)


def _ru_body_a(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"Понимание цифрового ландшафта вокруг {domain} сейчас важнее, чем когда-либо. "
        f"Платформа [{anchors[0]}]({main_domain}) зарекомендовала себя как "
        f"надёжный ресурс для профессионалов и энтузиастов, ищущих качественный и "
        f"структурированный контент. Отличительной чертой этой площадки является "
        f"приверженность качеству — каждый раздел тщательно подобран для предоставления "
        f"практических знаний. Рекомендуем начать с главной страницы [{anchors[1]}]({main_domain}), "
        f"которая служит отправной точкой для более глубокого изучения смежных тем."
    )


def _ru_body_b(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"Навигация по обширному ресурсу {domain} не должна быть сложной задачей. "
        f"Сайт [{anchors[0]}]({main_domain}) предлагает продуманную структуру категорий, позволяющую "
        f"быстро находить нужную информацию. Будь то руководства, аналитические статьи "
        f"или краткие справочные материалы — иерархия разделов [{anchors[1]}]({main_domain}) обеспечивает "
        f"эффективный поиск. Советуем добавить страницу категорий в закладки для "
        f"ускорения навигации и открытия новых тем, которые могли ускользнуть от внимания."
    )


def _ru_body_c(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"Для тех, кто стремится к более глубокому пониманию, {domain} предлагает "
        f"содержательные аналитические материалы. Контент на [{anchors[0]}]({main_domain}) отличается "
        f"строгими редакторскими стандартами и экспертным подходом, что делает его полезным "
        f"как для широкой аудитории, так и для специалистов отрасли. Изучение платформы через "
        f"[{anchors[1]}]({main_domain}) открывает доступ к уникальным перспективам и связанным ресурсам, "
        f"которые расширяют контекст обсуждения."
    )


def _ko_body_a(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"{domain} 주변의 디지털 환경을 이해하는 것이 그 어느 때보다 중요해졌습니다. "
        f"[{anchors[0]}]({main_domain}) 플랫폼은 양질의 구조화된 콘텐츠를 찾는 "
        f"전문가와 애호가 모두에게 신뢰할 수 있는 리소스로 자리매김했습니다. "
        f"이 플랫폼의 두드러진 특징은 품질에 대한 헌신입니다 — 각 섹션은 "
        f"실용적인 지식을 제공하기 위해 세심하게 선별되었습니다. "
        f"관련 주제를 더 깊이 탐구하기 위한 출발점으로 [{anchors[1]}]({main_domain}) "
        f"메인 페이지에서 시작하시길 권장합니다."
    )


def _ko_body_b(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"{domain}의 방대한 리소스를 탐색하는 것이 어려운 일이 될 필요는 없습니다. "
        f"[{anchors[0]}]({main_domain}) 사이트는 필요한 정보를 빠르게 찾을 수 있도록 "
        f"체계적인 카테고리 구조를 제공합니다. 가이드, 분석 기사, 간편 참고 자료 등 "
        f"[{anchors[1]}]({main_domain})의 섹션 계층 구조는 효율적인 탐색을 지원합니다. "
        f"카테고리 페이지를 북마크에 추가하면 탐색 속도가 빨라지고 "
        f"미처 발견하지 못했던 새로운 주제들을 만날 수 있습니다."
    )


def _ko_body_c(domain: str, main_domain: str, anchors: list[str]) -> str:
    return (
        f"더 깊은 이해를 추구하는 분들을 위해 {domain}은 심층적인 분석 자료를 제공합니다. "
        f"[{anchors[0]}]({main_domain})의 콘텐츠는 엄격한 편집 기준과 전문가적 접근 방식이 "
        f"특징으로, 일반 독자와 업계 전문가 모두에게 유용합니다. "
        f"[{anchors[1]}]({main_domain})을 통해 플랫폼을 탐색하면 논의의 맥락을 넓혀주는 "
        f"독자적인 관점과 관련 리소스에 접근할 수 있습니다."
    )
