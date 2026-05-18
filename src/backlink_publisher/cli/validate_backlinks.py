"""Validate planned backlink payloads with structured logging."""

from __future__ import annotations

import json
import sys
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from .. import config_echo, errors
from backlink_publisher.anchor.lang import check_anchor_language
from backlink_publisher.config import Config, get_anchor_pool_v2, load_config
from backlink_publisher._util.errors import emit_error, InputValidationError
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.linkcheck.language import (
    SUPPORTED_LANGUAGES,
    detect_language,
    detect_language_from_html,
    detect_language_from_markdown,
    language_matches,
)
from backlink_publisher.linkcheck.http import check_urls_strict
from backlink_publisher.publishing.content_negotiation import route_tier_for
from backlink_publisher._util.logger import validate_logger
from backlink_publisher._util.markdown import validate_markdown_convertible
from ..schema import SUPPORTED_PLATFORMS, _is_field_present, validate_output_payload


def _resolve_branded_pool(row: dict[str, Any], config: Config | None) -> list[str]:
    """Return the branded_pool to use for R4 exemption checks.

    Resolution order (per plan 2026-05-14-001):
    1. ``row.metadata.branded_pool`` snapshot emitted by plan-backlinks.
       Closes the validate→publish TOCTOU window — the snapshot is what
       plan-time considered branded.
    2. Live ``get_anchor_pool_v2`` lookup against the loaded config.
       Fallback for older JSONL produced before this PR shipped.
    3. Empty list. The gate proceeds with no exemption; legitimate Latin
       brand-name anchors will fail R4. Surfaced via a one-time WARN per
       row so the operator notices.
    """
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        snap = metadata.get("branded_pool")
        if isinstance(snap, list):
            return [str(x) for x in snap]
    if config is None:
        return []
    main_domain = row.get("main_domain", "")
    if not main_domain:
        return []
    return list(get_anchor_pool_v2(config, main_domain, "home", "branded"))


class _HrefCollector(HTMLParser):
    """Stdlib HTML parser subclass that collects ``<a href>`` attribute values.

    Plan 2026-05-18-006 Unit 6 R3 host-parse + Threat Model anti-injection:
    extract real href values from ``content_html`` so the main_domain check
    cannot be bypassed by placing ``main_domain`` inside ``data-*`` attributes,
    HTML comments, or non-linking text nodes.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value is not None:
                self.hrefs.append(value)


def _extract_hrefs_from_html(html: str) -> list[str]:
    """Return a list of all ``<a href>`` attribute values in ``html``.

    Stdlib ``html.parser`` is permissive about malformed HTML; the validate
    gate's job is to inspect the hrefs the parser actually finds, not to
    validate HTML well-formedness.
    """
    if not isinstance(html, str) or not html.strip():
        return []
    collector = _HrefCollector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:  # noqa: BLE001 — parser may raise on extreme inputs
        return collector.hrefs
    return collector.hrefs


def _check_main_domain_in_html(html: str, main_domain_normalized: str) -> bool:
    """Plan 2026-05-18-006 Unit 6 R3: verify ``main_domain_normalized`` is the
    host of at least one ``<a href>`` link in ``html``. Closes the
    subdomain-spoof / userinfo-injection / javascript-href / data-href /
    Punycode-spoof attack surface.

    Implementation contract (R3 6-step):
    1. Parse hrefs via stdlib :class:`HTMLParser` (collected in
       :class:`_HrefCollector`).
    2. ``urlsplit`` each href; ValueError → treat as non-matching.
    3. Pre-IDN-encode rejects: scheme.lower() not in {http, https};
       userinfo (username / password) set; hostname None or empty;
       hostname contains ``:`` (IPv6 literals — out of v1); whitespace
       or control codepoints in hostname.
    4. IDN-encode hostname to ASCII punycode via stdlib ``encodings.idna``
       (the encode-failure-on-overflow safety net for label > 63 octets).
    5. Match rule: ``hostname_ascii == main_domain_normalized`` OR
       ``hostname_ascii.endswith("." + main_domain_normalized)``. The
       leading dot prevents ``evil-main-domain.com`` matching
       ``main-domain.com`` as a suffix.
    6. Return True if any href matches; else False.

    ``main_domain_normalized`` is the punycode-form host produced by
    :func:`backlink_publisher.schema._normalize_main_domain` at Unit 1
    schema-time, stored on the row as ``main_domain_normalized``.
    """
    if not main_domain_normalized:
        return False
    target_host = main_domain_normalized.strip().lower()
    target_suffix = "." + target_host

    for href in _extract_hrefs_from_html(html):
        try:
            parsed = urlsplit(href.strip())
        except ValueError:
            continue

        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            continue

        # Userinfo (username/password) reject — closes
        # `https://main-domain.com@evil.com/` injection
        if parsed.username or parsed.password:
            continue

        host = parsed.hostname
        if host is None or host == "":
            continue

        # IPv6 detection — urlsplit strips brackets, so check for colon
        if ":" in host:
            continue

        # Whitespace / control codepoints in host
        if any(c.isspace() or unicodedata.category(c).startswith("C") for c in host):
            continue

        try:
            hostname_ascii = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            # Label-length overflow / reserved chars — treat as non-matching
            continue

        if hostname_ascii == target_host or hostname_ascii.endswith(target_suffix):
            return True

    return False


def _row_field_text(row: dict[str, Any], field: str) -> str:
    """Read a row field as a string, treating non-strings as empty."""
    value = row.get(field, "")
    return value if isinstance(value, str) else ""


def _nfc_normalize_in_place(row: dict[str, Any]) -> None:
    """Plan 2026-05-18-006 Unit 6 R13 + Hangul Jamo deferred-question
    resolution: apply NFC normalization to row-resident string fields at
    validate-time entry.

    Closes the macOS-NFD risk that splits Hangul Syllables into Jamo
    codepoints outside ``U+AC00..U+D7AF`` and defeats the ko codepoint
    short-circuit. ``zh-CN`` / ``en`` / ``ru`` paths unaffected because
    their codepoint ranges don't decompose.

    Row-level fields normalized: ``content_markdown``, ``content_html``,
    and each ``link["anchor"]``. ``branded_pool`` / ``anchor_keywords``
    are config-resident (not on the row) and get NFC at Unit 7 config-load.
    """
    for field in ("content_markdown", "content_html"):
        value = row.get(field)
        if isinstance(value, str):
            row[field] = unicodedata.normalize("NFC", value)

    links = row.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and isinstance(link.get("anchor"), str):
                link["anchor"] = unicodedata.normalize("NFC", link["anchor"])


def _detect_row_body_language(row: dict[str, Any]) -> tuple[str, str]:
    """Plan 2026-05-18-006 Unit 6 R15: dispatch body-language detection by
    source-field presence. Returns ``(detected, source_used)`` where
    ``source_used`` is one of ``"markdown"``, ``"html"``, ``"both-match"``,
    ``"both-mismatch:<md>/<html>"``, or ``"absent"``.

    Field-presence semantics: a field is present iff non-empty + non-whitespace
    string (see ``schema._is_field_present``). Whitespace-only strings are
    treated as absent for dispatch.

    Both-present rule (R3 / R15 strict mode): run both detectors; if they
    disagree, return ``"unknown"`` and a mismatch tag so the caller can emit
    a clear validation error. If they agree, return the agreed language.
    """
    md = row.get("content_markdown")
    html = row.get("content_html")
    md_present = _is_field_present(md)
    html_present = _is_field_present(html)

    if md_present and html_present:
        md_lang = detect_language_from_markdown(md)
        html_lang = detect_language_from_html(html)
        if md_lang == html_lang:
            return md_lang, "both-match"
        # Disagreement: surface as "unknown" so language_matches's escape
        # valve doesn't accidentally pass; the caller separately emits the
        # mismatch error using the explicit tag.
        return "unknown", f"both-mismatch:md={md_lang}/html={html_lang}"

    if md_present:
        return detect_language_from_markdown(md), "markdown"
    if html_present:
        return detect_language_from_html(html), "html"
    return "unknown", "absent"


def _enhance_payload(row: dict[str, Any], config: Config | None = None) -> dict[str, Any]:
    """Attach a ``validation`` block; populate errors[] on R2/R4/R5 failure.

    Contract (R11): ``validation.status`` is ``"failed"`` if any error fired,
    else ``"passed"``. ``validation.errors`` is the structured failure list.
    ``validation.warnings`` is preserved as an empty list for back-compat
    (test_validate_backlinks.py:189 asserts shape).
    """
    errors_list: list[str] = []
    warnings_list: list[str] = []

    # Plan 2026-05-18-006 Unit 6: NFC-normalize row-resident string fields
    # before any codepoint-dependent gate runs. Closes the macOS-NFD risk
    # that splits Hangul Syllables outside U+AC00..U+D7AF.
    _nfc_normalize_in_place(row)

    requested = row.get("language", "")

    # R3 enum guard — non-enum row.language skips R2/R4 with a WARN.
    if requested not in SUPPORTED_LANGUAGES:
        validate_logger.warn(
            f"row {row.get('id', '?')}: language '{requested}' outside enum "
            f"{sorted(SUPPORTED_LANGUAGES)}; skipping language and anchor gates"
        )
    else:
        # R2 / R15: body-language match. Dispatch on (content_markdown,
        # content_html) presence — supports HTML-source rows (Unit 1 R2)
        # without losing the body-language gate (pass-1 feasibility P1).
        detected, source_used = _detect_row_body_language(row)
        if source_used.startswith("both-mismatch:"):
            # Explicit dual-source disagreement — surface a precise error.
            tag = source_used.removeprefix("both-mismatch:")
            errors_list.append(
                f"body language mismatch between content_markdown and "
                f"content_html ({tag}); operator must use single-source "
                f"workflow or update both fields"
            )
        elif not language_matches(detected, requested):
            errors_list.append(
                f"body language '{detected}' does not match requested '{requested}'"
            )

        # R4/R5: per-anchor codepoint check for kind in {main_domain, target}.
        branded_pool = _resolve_branded_pool(row, config)
        for idx, link in enumerate(row.get("links", [])):
            anchor = link.get("anchor", "") if isinstance(link, dict) else ""
            kind = link.get("kind", "") if isinstance(link, dict) else ""
            ok, reason = check_anchor_language(anchor, requested, kind, branded_pool)
            if not ok:
                errors_list.append(
                    f"link[{idx}] anchor {anchor!r} failed: {reason}"
                )

    # Plan 2026-05-18-006 Unit 6 R3: HTML host-parse main_domain check.
    # Runs only when content_html is present (the markdown substring check
    # at schema-time already validated MD-only and both-fields rows). For
    # HTML rows, the substring check is unsafe (data-* attribute injection,
    # comment placement) so we run the attribute-aware host-parse here.
    html_present = _is_field_present(row.get("content_html"))
    if html_present:
        main_domain_normalized = row.get("main_domain_normalized", "")
        if not isinstance(main_domain_normalized, str) or not main_domain_normalized:
            # Schema-time normalization should have populated this; if it
            # didn't, validation can't proceed for the HTML host-parse path.
            errors_list.append(
                "content_html present but main_domain_normalized missing — "
                "schema-time normalization should have populated it"
            )
        else:
            host_part_match = _check_main_domain_in_html(
                row["content_html"], main_domain_normalized
            )
            if not host_part_match:
                errors_list.append(
                    f"main_domain '{main_domain_normalized}' is not the host "
                    f"of any <a href> in content_html (substring matches in "
                    f"comments / attributes do not count)"
                )

    row["validation"] = {
        "status": "failed" if errors_list else "passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "warnings": warnings_list,
        "errors": errors_list,
    }
    return row


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="validate-backlinks",
        description="Validate planned backlink payloads.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--no-validate-url-check",
        action="store_true",
        default=False,
        dest="no_validate_url_check",
        help="Skip URL reachability checks at validate-time",
    )
    parser.add_argument(
        "--no-check-urls",
        action="store_true",
        default=False,
        dest="no_validate_url_check_legacy",
        help=(
            "DEPRECATED alias for --no-validate-url-check. "
            "Will be removed in a future version."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    validate_logger.info("validate-backlinks started")

    # R10: --no-check-urls remains as a deprecated alias for back-compat.
    # Either flag set => URL checks disabled.
    if args.no_validate_url_check_legacy and not args.no_validate_url_check:
        validate_logger.warn(
            "--no-check-urls is deprecated; use --no-validate-url-check. "
            "Will be removed in a future version."
        )
    check_urls = not (args.no_validate_url_check or args.no_validate_url_check_legacy)

    # R4 branded-pool fallback source. Failure here is non-fatal — payload-first
    # snapshot from plan-backlinks is the primary source; missing config just
    # disables the live fallback.
    config: Config | None = None
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — config-load failures are tolerated
        validate_logger.warn(
            f"config load failed ({exc}); branded_pool fallback disabled, "
            "relying on payload-emitted snapshots only"
        )

    # Config Echo Chamber (Round-3 #7): emit a 4-line banner so operators
    # see which config was actually resolved + env overrides + SHA.
    if config is not None:
        config_echo.emit_banner(config, "validate-backlinks")

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    validate_logger.info(f"validating {len(rows)} payloads")

    if check_urls:
        all_urls = set()
        for row in rows:
            all_urls.add(row.get("target_url", ""))
            all_urls.add(row.get("main_domain", ""))
            for link in row.get("links", []):
                all_urls.add(link.get("url", ""))
            # Plan 2026-05-18-006 Unit 6 + pass-2 security P1: also include
            # <a href> URLs from content_html in the reachability scan.
            # Closes the symmetric-coverage gap between content_markdown
            # (URLs found inline) and content_html sources, so a HTML row
            # can't ship dead/malicious-redirect links that a markdown row
            # would have caught.
            html = row.get("content_html")
            if isinstance(html, str) and html.strip():
                for href in _extract_hrefs_from_html(html):
                    href = href.strip()
                    # Only http(s) URLs are reachable; other schemes (data:,
                    # javascript:, etc.) are rejected by R3 elsewhere.
                    if href.startswith(("http://", "https://")):
                        all_urls.add(href)
        all_urls.discard("")

        if all_urls:
            try:
                check_urls_strict(list(all_urls))
            except errors.ExternalServiceError as exc:
                validate_logger.error(f"URL check failed: {exc}")
                raise SystemExit(4) from None

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    # Silent-Drop Tripwire — partition drops by gate so the reconciliation
    # line tells the operator exactly where each row vanished.
    platform_drops: list[int] = []
    validation_drops: list[int] = []

    for idx, row in enumerate(rows, start=1):
        # Check for unsupported platforms (linkedin)
        platform = row.get("platform", "")
        if platform == "linkedin":
            all_errors.append(
                f"row {idx}: platform 'linkedin' is not supported. "
                f"Supported: {', '.join(sorted(SUPPORTED_PLATFORMS))}"
            )
            platform_drops.append(idx)
            continue

        # Plan 2026-05-18-006 Unit 6 R10: tier (b)/(c) content_html-only
        # gate. Runs as the next check after the platform-enum guard. A
        # content_html-only row destined for a platform whose route is not
        # tier (a) is rejected here — closes the silent-empty-publish risk
        # where the adapter would receive an empty content_markdown.
        if (
            _is_field_present(row.get("content_html"))
            and not _is_field_present(row.get("content_markdown"))
            and route_tier_for(platform) != "a"
        ):
            all_errors.append(
                f"row {idx}: platform '{platform}' does not yet accept "
                f"content_html (only markdown). Provide content_markdown or "
                f"wait for adapter retrofit."
            )
            platform_drops.append(idx)
            continue

        errs = validate_output_payload(row)
        if errs:
            all_errors.extend(f"row {idx}: {e}" for e in errs)
            validation_drops.append(idx)
            continue
        enhanced = _enhance_payload(row, config)
        if enhanced["validation"]["status"] == "failed":
            # R2/R5 row-level abort: don't forward to stdout; surface errors to stderr.
            for err in enhanced["validation"]["errors"]:
                all_errors.append(f"row {idx}: {err}")
            continue
        outputs.append(enhanced)

    # R2/R5: per-row skip semantic — passing rows STILL stream to stdout
    # so downstream consumers see partial success; exit code reflects overall
    # success only when zero rows failed. Schema/platform-level failures
    # (which already populated all_errors before _enhance_payload) follow
    # the same per-row pattern under the new contract.
    failed_count = len(rows) - len(outputs)
    write_jsonl(outputs)

    # Emit Silent-Drop Tripwire reconciliation BEFORE the exit guard so failed
    # runs still surface a delta summary.
    validate_logger.recon(
        "validate_reconciliation",
        input_rows=len(rows),
        output_rows=len(outputs),
        delta=len(rows) - len(outputs),
        dropped={
            "platform": len(platform_drops),
            "validation": len(validation_drops),
        },
        dropped_row_indices={
            "platform": platform_drops,
            "validation": validation_drops,
        },
    )

    if all_errors:
        for err in all_errors:
            print(f"validation error: {err}", file=sys.stderr)
        validate_logger.error(
            f"validation failed: {len(all_errors)} errors "
            f"({len(outputs)} passed, {failed_count} failed)"
        )
        raise SystemExit(2)

    validate_logger.info(
        f"validated {len(outputs)} payloads "
        f"({len(outputs)} passed, {failed_count} failed)"
    )