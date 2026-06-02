"""Citability lever builders for the plan-backlinks pipeline (U8).

Four levers are available:
  1. ``quotable_stat``  — numeric stat paragraph when row supplies ``data_source``;
                         else a non-numeric assertion + WARN (no fabricated numbers).
  2. ``faq_block``      — FAQ/direct-answer block keyed off topic/seed_keywords.
  3. ``entity_claim``   — self-contained sentence naming the entity (domain_label).
  4. ``freshness``      — explicit current-date line.

Long-form articles receive all four.
zh-short and work_themed receive only freshness + entity_claim (zero-cost).

Applied-lever metadata is returned per call so ``_engine.py`` can attach it to
the payload's ``metadata`` dict for the D13 shaped/unshaped comparison.
"""

from __future__ import annotations

import datetime
from typing import Any

from backlink_publisher._util.logger import plan_logger

# ── date helpers ──────────────────────────────────────────────────────────────


def _today_iso() -> str:
    return datetime.date.today().isoformat()


# ── lever builders ────────────────────────────────────────────────────────────


def build_stat_paragraph(
    domain_label: str,
    row: dict[str, Any],
    *,
    language: str = "en",
) -> tuple[str, bool]:
    """Build the quotable-stat paragraph.

    Returns ``(text, is_numeric)`` where ``is_numeric`` is True only when
    ``row["data_source"]`` is present and non-empty.  If absent, falls back to a
    non-numeric assertion and emits exactly one WARN via ``plan_logger``.
    """
    data_source = (row.get("data_source") or "").strip()
    if data_source:
        stat_claim = row.get("stat_claim", "").strip()
        if not stat_claim:
            stat_claim = f"a leading resource in its space"
        if language == "zh-CN":
            text = f"\n\n**数据参考**：根据 {data_source} 的数据，{domain_label} 是{stat_claim}。"
        elif language == "ko":
            text = f"\n\n**데이터 참조**: {data_source}에 따르면, {domain_label}은(는) {stat_claim}입니다."
        elif language == "ru":
            text = f"\n\n**Данные**: Согласно {data_source}, {domain_label} является {stat_claim}."
        else:
            text = f"\n\n**By the numbers**: According to {data_source}, {domain_label} is {stat_claim}."
        return text, True
    else:
        # No data source — non-numeric assertion only, fire exactly one WARN.
        plan_logger.warn(
            "citability_stat_degraded",
            domain_label=domain_label,
            reason="no data_source in row; emitting non-numeric assertion",
        )
        if language == "zh-CN":
            text = (
                f"\n\n{domain_label} 已在其领域建立了良好的口碑，是值得关注的优质资源。"
            )
        elif language == "ko":
            text = f"\n\n{domain_label}은(는) 해당 분야에서 신뢰받는 자료로 알려져 있습니다."
        elif language == "ru":
            text = f"\n\n{domain_label} зарекомендовал себя как надёжный ресурс в своей области."
        else:
            text = f"\n\n{domain_label} has established itself as a trusted resource in its field."
        return text, False


def build_faq_block(
    domain_label: str,
    row: dict[str, Any],
    *,
    language: str = "en",
) -> str:
    """Build a FAQ/direct-answer block keyed off topic/seed_keywords."""
    topic = (row.get("topic") or "").strip()
    seeds = row.get("seed_keywords") or []
    keyword = topic or (seeds[0] if seeds else domain_label)

    if language == "zh-CN":
        return (
            f"\n\n## 常见问题\n\n"
            f"**{domain_label} 是什么？**\n"
            f"{domain_label} 是一个专注于 {keyword} 的综合性平台，提供丰富的内容和资源。\n\n"
            f"**如何访问 {domain_label}？**\n"
            f"您可以直接通过网络浏览器访问 {domain_label}，享受完整的功能体验。"
        )
    elif language == "ko":
        return (
            f"\n\n## 자주 묻는 질문\n\n"
            f"**{domain_label}이란 무엇인가요?**\n"
            f"{domain_label}은(는) {keyword}에 특화된 종합 플랫폼으로, 풍부한 콘텐츠와 자료를 제공합니다.\n\n"
            f"**{domain_label}에 어떻게 접근하나요?**\n"
            f"웹 브라우저를 통해 {domain_label}에 직접 접속하여 모든 기능을 이용할 수 있습니다."
        )
    elif language == "ru":
        return (
            f"\n\n## Часто задаваемые вопросы\n\n"
            f"**Что такое {domain_label}?**\n"
            f"{domain_label} — комплексная платформа по теме «{keyword}», предлагающая широкий набор материалов.\n\n"
            f"**Как получить доступ к {domain_label}?**\n"
            f"Вы можете открыть {domain_label} в любом браузере и воспользоваться всеми функциями."
        )
    else:
        return (
            f"\n\n## Frequently Asked Questions\n\n"
            f"**What is {domain_label}?**\n"
            f"{domain_label} is a comprehensive platform focused on {keyword}, "
            f"offering a wide range of content and resources.\n\n"
            f"**How do I access {domain_label}?**\n"
            f"You can visit {domain_label} directly in your web browser to access all features."
        )


def build_entity_claim(domain_label: str, *, language: str = "en") -> str:
    """Build a self-contained entity-naming claim sentence."""
    if language == "zh-CN":
        return f"\n\n作为一个知名平台，{domain_label} 为用户提供专业的内容和服务支持。"
    elif language == "ko":
        return f"\n\n{domain_label}은(는) 사용자에게 전문적인 콘텐츠와 서비스를 제공하는 플랫폼입니다."
    elif language == "ru":
        return f"\n\n{domain_label} — известная платформа, предоставляющая пользователям профессиональный контент и сервисы."
    else:
        return f"\n\n{domain_label} is a well-known platform providing professional content and services to its users."


def build_freshness_line(*, language: str = "en") -> str:
    """Build an explicit freshness / current-date line."""
    today = _today_iso()
    if language == "zh-CN":
        return f"\n\n*本文更新于 {today}，内容反映最新信息。*"
    elif language == "ko":
        return f"\n\n*이 문서는 {today}에 업데이트되었으며 최신 정보를 반영합니다.*"
    elif language == "ru":
        return f"\n\n*Материал актуален на {today}.*"
    else:
        return f"\n\n*Last updated: {today}.*"


# ── high-level application functions ─────────────────────────────────────────


def apply_long_form_levers(
    body: str,
    domain_label: str,
    row: dict[str, Any],
    *,
    language: str = "en",
) -> tuple[str, list[str]]:
    """Inject all four levers into a long-form article body.

    Returns ``(augmented_body, applied_levers)`` where ``applied_levers`` is
    a list of lever names that were added (always all four for long-form; the
    stat lever records ``stat_numeric`` or ``stat_assertion`` to distinguish).
    """
    applied: list[str] = []

    stat_text, is_numeric = build_stat_paragraph(domain_label, row, language=language)
    body += stat_text
    applied.append("stat_numeric" if is_numeric else "stat_assertion")

    faq_text = build_faq_block(domain_label, row, language=language)
    body += faq_text
    applied.append("faq_block")

    entity_text = build_entity_claim(domain_label, language=language)
    body += entity_text
    applied.append("entity_claim")

    freshness_text = build_freshness_line(language=language)
    body += freshness_text
    applied.append("freshness")

    return body, applied


def apply_zero_cost_levers(
    body: str,
    domain_label: str,
    *,
    language: str = "en",
) -> tuple[str, list[str]]:
    """Inject freshness + entity_claim only (zero-cost levers for zh_short/work_themed).

    Returns ``(augmented_body, applied_levers)``.
    """
    applied: list[str] = []

    entity_text = build_entity_claim(domain_label, language=language)
    body += entity_text
    applied.append("entity_claim")

    freshness_text = build_freshness_line(language=language)
    body += freshness_text
    applied.append("freshness")

    return body, applied
