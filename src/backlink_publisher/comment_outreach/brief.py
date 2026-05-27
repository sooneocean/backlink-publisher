"""``comment brief`` — conservative CommentBrief generation with post-LLM guardrails.

The deterministic guardrail (:func:`guardrail_comment`) is the module's safety reputation:
it runs on **every** draft — LLM output *and* the template fallback — and enforces the
hard guarantees regardless of what a model (or a prompt-injected page) produced:

- **control/bidi/zero-width strip** on the persisted ``suggested_comment`` (closes the
  paste-injection vector now, while the assisted-navigation feature R12 stays deferred);
- **≤1 link**, and **zero** links when the link policy is ``no-link`` — counted across both
  bare URLs and markdown links so neither form can smuggle extra links past the cap.

The LLM is **optional and never required**: the provider is imported lazily inside
:func:`_load_provider` (so the other four verbs never load the publishing stack), and any
provider failure falls back to a deterministic template — the raw exception is never
logged (it can echo request material), only a redaction-safe RECON marker.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional, TextIO

from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.comment_outreach import schema

brief_logger = PipelineLogger("comment-brief")

#: Control (C0) + DEL + zero-width + bidi marks/embeddings/overrides/isolates, plus the
#: LINE/PARAGRAPH separators (U+2028/U+2029 via the U+2028-U+202E range). Stripped from the
#: persisted comment so a copied draft can't carry an invisible payload. Kept a true
#: superset of the provider's ``_PROMPT_UNSAFE_CHARS`` — a parity test pins the overlap.
_UNSAFE_CHARS_RE = re.compile(
    "[\x00-\x1f\x7f"          # C0 controls + DEL
    "​-‏"           # zero-width space .. RTL mark
    " -‮"           # line/para separators + bidi embeddings / overrides
    "⁠-⁤"           # word joiner / invisible operators
    "⁦-⁩"           # bidi isolates
    "﻿"                  # zero-width no-break space / BOM
    "]"
)

#: A bare ``http(s)://…`` URL (no surrounding markdown). Used to scrub URLs out of a
#: markdown anchor when that link is dropped over the link budget, so a URL-as-anchor
#: cannot re-enter the text as a fresh bare link.
_BARE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

#: Matches a markdown link ``[anchor](http…)`` OR a bare ``http(s)://…`` URL. Markdown is
#: listed first so a URL inside ``](…)`` is consumed there, not double-counted as bare.
_LINK_RE = re.compile(
    r"\[([^\]]*)\]\((https?://[^)\s]+)\)|(https?://\S+)", re.IGNORECASE
)

#: Static, always-attached operator guidance. The tool never posts — these enforce that
#: the human stays in the loop (and pass the ``CommentBrief`` required-list validation).
HUMAN_CHECKLIST = [
    "Read the full article/thread before posting.",
    "Confirm the comment is specific and adds value — not generic praise.",
    "Honor the site's link policy; remove the link entirely if links are discouraged.",
    "Post manually under your own account — this tool never submits comments.",
]
PROHIBITED_ACTIONS = [
    "No exact-match keyword anchor text.",
    "No more than one link (zero when the link policy is no-link).",
    "No keyword stuffing or repeated boilerplate.",
    "No automated submission, login automation, proxy/account rotation, or CAPTCHA bypass.",
]


def _strip_unsafe(text: str) -> str:
    return _UNSAFE_CHARS_RE.sub("", text)


def _enforce_link_policy(text: str, link_policy: str) -> str:
    """Reduce links so the draft satisfies *link_policy*: 0 links for ``no-link``, else
    ≤1. Markdown links over budget collapse to their anchor words; bare URLs over budget
    are dropped. Counts bare + markdown together."""
    max_links = 0 if link_policy == "no-link" else 1
    kept = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal kept
        if m.group(1) is not None:  # markdown link [anchor](url)
            anchor, url = m.group(1), m.group(2)
            if kept < max_links:
                kept += 1
                return f"[{anchor}]({url})"
            # Over budget: keep the anchor words but scrub any URL *inside* the anchor —
            # a URL-as-anchor would otherwise re-enter the text as a fresh bare link that
            # this single left-to-right pass never revisits, leaking past the cap.
            return _BARE_URL_RE.sub("", anchor)
        url = m.group(3)  # bare url
        if kept < max_links:
            kept += 1
            return url
        return ""

    return _LINK_RE.sub(_repl, text)


def count_links(text: str) -> int:
    """Number of links (bare + markdown) in *text* — used by tests and the guardrail."""
    return len(_LINK_RE.findall(text))


def guardrail_comment(text: str, link_policy: str) -> str:
    """Apply the hard guarantees to a draft: strip unsafe chars, enforce the link cap,
    and normalize whitespace. Deterministic; runs on LLM output and the template alike."""
    text = _strip_unsafe(text)
    text = _enforce_link_policy(text, link_policy)
    return " ".join(text.split())


def _template_comment(topic: str, page_title: str) -> str:
    """Deterministic, link-free, on-topic-ish fallback when no LLM draft is available."""
    subject = (topic or page_title or "this topic").strip()
    return (
        f"This is a useful take on {subject}. The trade-offs you describe match what "
        f"I've run into in practice — especially the point about getting the "
        f"fundamentals right before reaching for more complex tooling. Thanks for "
        f"writing it up."
    )


def build_brief(record: dict[str, Any], provider: Optional[Any] = None) -> dict[str, Any]:
    """Build one ``CommentBrief`` from an accept-decision *record*.

    *record* is a ``QualificationResult`` that may additionally carry target context
    (``topic`` / ``page_title`` / ``thread_summary`` / ``target_url``) for a
    context-responsive draft; absent context degrades to the safe template. Every path
    runs through :func:`guardrail_comment` before the text is persisted.
    """
    target_id = record.get("target_id", "")
    topic = record.get("topic") or ""
    page_title = record.get("page_title") or ""
    thread_summary = record.get("thread_summary") or ""
    target_url = record.get("target_url") or ""
    link_policy = record.get("link_policy") or "no-link"
    anchor_policy = record.get("anchor_policy") or "branded-only"

    raw: Optional[str] = None
    source = "template"
    if provider is not None:
        try:
            raw = provider.generate_comment_draft(
                topic=topic,
                page_title=page_title,
                thread_summary=thread_summary,
                target_url=target_url,
                link_policy=link_policy,
                anchor_policy=anchor_policy,
            )
            source = "llm"
        except Exception:  # noqa: BLE001 — never crash, never log the raw exception
            raw = None
            source = "template"
            brief_logger.recon("comment_brief_llm_fallback", target_id=target_id)

    if not raw or not raw.strip():
        raw = _template_comment(topic, page_title)
        source = "template"

    suggested = guardrail_comment(raw, link_policy)
    if not suggested.strip():  # guardrail removed everything → safe template
        suggested = guardrail_comment(_template_comment(topic, page_title), link_policy)
        source = "template"  # the persisted text is the template, not the LLM draft

    return {
        "target_id": target_id,
        "suggested_comment": suggested,
        "suggested_anchor_policy": anchor_policy,
        "suggested_link_policy": link_policy,
        "human_checklist": list(HUMAN_CHECKLIST),
        "prohibited_actions": list(PROHIBITED_ACTIONS),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }


def _load_provider() -> Optional[Any]:
    """Construct the LLM provider from ``[llm.anchor_provider]`` config, or ``None``.

    Imports are **function-local** so importing this module — and running the other four
    verbs — never pulls the publishing registry into the import graph. Loading the
    provider here *does* import the registry (memory-only, accepted for the brief verb):
    no posting, no events.db, no dispatch. Returns ``None`` (template-only) on any config
    or construction failure; the LLM is never required.
    """
    try:
        from backlink_publisher.config.loader import load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001
        return None
    provider_cfg = getattr(cfg, "llm_anchor_provider", None)
    if provider_cfg is None:
        return None
    try:
        from backlink_publisher.publishing.adapters.llm_anchor_provider import (
            OpenAICompatibleProvider,
        )

        return OpenAICompatibleProvider(
            base_url=provider_cfg.base_url,
            api_key=provider_cfg.api_key,
            model=provider_cfg.model,
            timeout_s=provider_cfg.timeout_s,
            temperature=provider_cfg.temperature,
            system_prompt=None,  # generate_comment_draft uses its own system message
        )
    except Exception:  # noqa: BLE001
        return None


def brief_targets(source: Optional[TextIO] = None, dest: Optional[TextIO] = None) -> dict[str, int]:
    """Read QualificationResult JSONL, emit a CommentBrief for each ``accept`` row.

    Invalid rows are surfaced via RECON (not silently dropped); non-``accept`` rows are
    skipped. Always exit-0 semantics; the LLM is optional (template fallback)."""
    provider = _load_provider()
    rows = read_jsonl(source, strict=False)
    briefs: list[dict[str, Any]] = []
    skipped = 0
    non_accept = 0
    for idx, record in enumerate(rows, start=1):
        errors = schema.validate_qualification_result(record)
        if errors:
            skipped += 1
            brief_logger.recon(
                "comment_brief_skip", row=idx, target_id=record.get("target_id"), reasons=errors
            )
            continue
        if record.get("decision") != "accept":
            non_accept += 1
            continue
        briefs.append(build_brief(record, provider))

    write_jsonl(briefs, dest)
    brief_logger.recon(
        "comment_brief_summary",
        briefs=len(briefs),
        skipped=skipped,
        non_accept=non_accept,
        provider="llm" if provider is not None else "template",
    )
    return {"briefs": len(briefs), "skipped": skipped, "non_accept": non_accept}
