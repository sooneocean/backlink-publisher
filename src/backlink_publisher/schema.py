"""Schema definitions and validation for backlink pipeline payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .linkcheck.language import SUPPORTED_LANGUAGES

INPUT_SCHEMA_FIELDS = {
    "target_url": str,
    "main_domain": str,
    "language": str,
    "platform": str,
    "url_mode": str,
    "publish_mode": str,
}

INPUT_OPTIONAL_FIELDS = {
    "topic": str,
    "seed_keywords": list,
    "extra_urls": list,
    "custom_title": str,
    "custom_tags": str,
}

#: Re-export from :mod:`backlink_publisher.linkcheck.language` for back-compat —
#: the canonical source is :data:`linkcheck.language.SUPPORTED_LANGUAGES`. Plan
#: 2026-05-18-006 Unit 1 de-duplicated the previous parallel ``set`` literal.
#: (Post-Plan-2026-05-18-001 Unit 6 packaging refactor: language_check.py
#: moved to linkcheck/language.py; legacy import path still works via the
#: MetaPathFinder shim in :mod:`backlink_publisher.__init__`.)
__all__ = ["SUPPORTED_LANGUAGES", "supported_platforms", "reject_unsupported_platform"]


def supported_platforms() -> frozenset[str]:
    """Return the set of platform names with at least one registered adapter.

    Delegates to :func:`backlink_publisher.publishing.registry.registered_platforms`
    so the schema-layer enum stays in lockstep with the dispatch registry
    (plan 2026-05-18-009 R9e). The lazy import inside the function forces the
    adapter side-effect registration via ``backlink_publisher.publishing.adapters``
    so callers do not need to remember to import it first.
    """
    from .publishing import adapters  # noqa: F401  populate registry
    from .publishing.registry import registered_platforms

    return frozenset(registered_platforms())


def reject_unsupported_platform(platform: str) -> str | None:
    """Return a user-facing rejection message if ``platform`` lacks an adapter.

    Plan 2026-05-18-009 R9d — folds the three coordinated LinkedIn-specific
    rejection sites (``schema.py``, ``publish_backlinks.py``,
    ``validate_backlinks.py``) into a single registry-driven helper. Coverage
    now extends beyond linkedin to any unregistered platform (e.g. tiktok,
    threads). Returns ``None`` when the platform is registered.
    """
    if platform in supported_platforms():
        return None
    supported = ", ".join(sorted(supported_platforms()))
    return f"platform '{platform}' is not supported. Supported: {supported}"


URL_MODES = {"A", "B", "C"}
PUBLISH_MODES = {"draft", "publish"}

# Output payload fields
OUTPUT_REQUIRED_FIELDS = {
    "id": str,
    "platform": str,
    "language": str,
    "publish_mode": str,
    "target_url": str,
    "main_domain": str,
    "url_mode": str,
    "title": str,
    "slug": str,
    "excerpt": str,
    "tags": list,
    "links": list,
    "seo": dict,
}

#: Output fields that are individually optional but appear in groups where at
#: least one must be present. Plan 2026-05-18-006 R2 / Unit 1 — ``content_html``
#: was added as a peer to ``content_markdown``; rows must carry at least one.
#: Future Telegraph node format can extend the existing group rather than
#: requiring a new top-level structure (extensibility per arch-strategist).
OUTPUT_ONE_OF_GROUPS: tuple[tuple[str, ...], ...] = (
    ("content_markdown", "content_html"),
)

#: Optional output fields with type expectations. Validated only when present.
OUTPUT_OPTIONAL_FIELDS = {
    "content_markdown": str,
    "content_html": str,
    "main_domain_normalized": str,
}

LINK_KINDS = {"main_domain", "target", "supporting", "extra", "category", "detail"}

MAX_PAYLOAD_SIZE_BYTES = 256 * 1024  # 256 KB

#: Cap on ``content_html`` byte length. Defends the script/style strip regex
#: in :mod:`language_check` and stdlib ``html.parser`` (Unit 6) from
#: regex-bomb / memory-pressure inputs. ``content_markdown`` left uncapped
#: in v1 (existing baseline; no regression). Plan 2026-05-18-006 Unit 1 +
#: Threat Model DoS row.
MAX_CONTENT_HTML_BYTES = 1_048_576  # 1 MiB


def _is_field_present(value: Any) -> bool:
    """Return True iff ``value`` is a non-empty, non-whitespace string.

    Field-presence predicate shared between schema-time validation (this module)
    and validate-time dispatch (:mod:`backlink_publisher.cli.validate_backlinks`).
    A ``None`` value or whitespace-only string is treated as absent.

    Plan 2026-05-18-006 Unit 1 + Unit 6 (consistent semantics across phases).
    """
    return isinstance(value, str) and bool(value.strip())


def _normalize_main_domain(url: str) -> str:
    """Return ``url`` with hostname IDN-encoded to ASCII punycode + lowercased.

    Operator-supplied ``main_domain`` is a full URL with scheme. Splits the
    URL, extracts the hostname, IDN-encodes (handling Unicode hostnames like
    ``löve.de`` → ``xn--lve-1la.de``), lowercases, strips trailing dot, and
    reconstructs.

    Raises :class:`ValueError` if the URL has no hostname or the IDN-encode
    fails (e.g. label longer than 63 octets, fully empty hostname after split).
    Callers should handle the exception as a per-row validation error rather
    than aborting the batch (plan 2026-05-18-006 security P2).
    """
    parts = urlsplit(url.strip())
    if not parts.hostname:
        raise ValueError("main_domain has no parseable hostname")
    try:
        hostname_ascii = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError(f"main_domain IDN-encode failed: {exc}") from exc
    if not hostname_ascii:
        raise ValueError("main_domain IDN-encode produced empty hostname")
    # Reconstruct with the normalized hostname. Preserve scheme, port, path,
    # query, fragment as-is — only the host component is normalized.
    netloc = hostname_ascii
    if parts.port is not None:
        netloc = f"{hostname_ascii}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _check_main_domain_presence(row: dict[str, Any]) -> str | None:
    """Verify ``main_domain`` appears in the row's content.

    Returns an error message if the invariant is violated, ``None`` otherwise.
    Routing:

    - ``content_markdown`` present → existing substring check, unchanged
      behavior.
    - ``content_html`` present, ``content_markdown`` absent → defers to the
      host-aware check at validate-time
      (:mod:`backlink_publisher.cli.validate_backlinks`); this schema-level
      helper returns ``None`` so the row isn't rejected at schema time
      (the actual HTML check requires :mod:`html.parser` which lives outside
      schema).
    - Both present → substring check still runs on the markdown side; the
      HTML side is also checked downstream.
    - Neither present → handled by ``OUTPUT_ONE_OF_GROUPS``, not here.

    Plan 2026-05-18-006 Unit 1 (refactor of inline check) + Unit 6 (HTML
    host-parse).
    """
    if "main_domain" not in row:
        return None
    md_domain = row["main_domain"]
    if not isinstance(md_domain, str):
        return None
    if _is_field_present(row.get("content_markdown")):
        md = row["content_markdown"]
        md_domain_norm = md_domain.rstrip("/")
        if md_domain_norm not in md and md_domain not in md:
            return f"main_domain '{md_domain}' does not appear in content_markdown"
    # HTML-only path is validated in cli.validate_backlinks (Unit 6) which
    # has access to html.parser.
    return None


def validate_input_payload(row: dict[str, Any], line_num: int) -> list[str]:
    """Validate an input seed row. Returns list of error messages.

    Side effect (plan 2026-05-18-006 Unit 1): when ``main_domain`` is a valid
    URL, the normalized punycode form is stored as ``row["main_domain_normalized"]``
    for downstream consumers (Unit 6 HTML host-parse). The original
    ``row["main_domain"]`` is preserved verbatim for display / logging.
    Normalization failures become per-row errors, not batch-aborting
    ``SystemExit`` (plan-review security P2).
    """
    errors: list[str] = []

    for field, ftype in INPUT_SCHEMA_FIELDS.items():
        if field not in row:
            errors.append(f"line {line_num}: missing required field '{field}'")
        elif not isinstance(row[field], ftype):
            errors.append(f"line {line_num}: field '{field}' must be {ftype.__name__}")

    # Check optional fields types
    for field, ftype in INPUT_OPTIONAL_FIELDS.items():
        if field in row and not isinstance(row[field], ftype):
            errors.append(f"line {line_num}: field '{field}' must be {ftype.__name__}")

    # Validate enumerated values
    if "language" in row and row["language"] not in SUPPORTED_LANGUAGES:
        errors.append(
            f"line {line_num}: unsupported language '{row['language']}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
        )

    if "platform" in row and row["platform"] not in supported_platforms():
        errors.append(
            f"line {line_num}: unsupported platform '{row['platform']}'. "
            f"Supported: {', '.join(sorted(supported_platforms()))}"
        )

    if "url_mode" in row and row["url_mode"] not in URL_MODES:
        errors.append(
            f"line {line_num}: invalid url_mode '{row['url_mode']}'. "
            f"Supported: {', '.join(sorted(URL_MODES))}"
        )

    if "publish_mode" in row and row["publish_mode"] not in PUBLISH_MODES:
        errors.append(
            f"line {line_num}: invalid publish_mode '{row['publish_mode']}'. "
            f"Supported: {', '.join(sorted(PUBLISH_MODES))}"
        )

    # Validate URLs (scheme prefix) and normalize main_domain for downstream
    # host-parse comparison (Unit 6). target_url is not normalized — it flows
    # to adapters which need the operator's exact URL.
    for url_field in ("target_url", "main_domain"):
        if url_field in row:
            url_val = str(row[url_field])
            if not re.match(r"^https?://", url_val):
                errors.append(f"line {line_num}: field '{url_field}' is not a valid URL: {url_val}")
                continue
            if url_field == "main_domain":
                try:
                    row["main_domain_normalized"] = _normalize_main_domain(url_val)
                except ValueError as exc:
                    errors.append(
                        f"line {line_num}: field 'main_domain' could not be normalized: {exc}"
                    )

    # Validate seed_keywords item types (list type already checked above)
    if "seed_keywords" in row and isinstance(row["seed_keywords"], list):
        for kw in row["seed_keywords"]:
            if not isinstance(kw, str):
                errors.append(f"line {line_num}: 'seed_keywords' items must be strings")

    return errors


def validate_output_payload(row: dict[str, Any]) -> list[str]:
    """Validate a planned output payload. Returns list of error messages."""
    errors: list[str] = []

    for field, ftype in OUTPUT_REQUIRED_FIELDS.items():
        if field not in row:
            errors.append(f"missing required output field '{field}'")
        elif not isinstance(row[field], ftype):
            errors.append(f"field '{field}' must be {ftype.__name__}, got {type(row[field]).__name__}")

    # Validate optional output fields' types when present (e.g., content_html
    # peer of content_markdown). Plan 2026-05-18-006 Unit 1.
    for field, ftype in OUTPUT_OPTIONAL_FIELDS.items():
        if field in row and not isinstance(row[field], ftype):
            errors.append(f"field '{field}' must be {ftype.__name__}, got {type(row[field]).__name__}")

    # At-least-one cross-field predicate per OUTPUT_ONE_OF_GROUPS. Uses
    # _is_field_present (treats whitespace-only as absent — symmetric with
    # validate-time dispatch in Unit 6).
    for group in OUTPUT_ONE_OF_GROUPS:
        if not any(_is_field_present(row.get(field)) for field in group):
            errors.append(
                f"at least one of {list(group)} must be present and non-empty"
            )

    # content_html size cap — defends downstream regex + html.parser from
    # regex-bomb / memory-pressure attacks. Plan Threat Model DoS row.
    if "content_html" in row and isinstance(row["content_html"], str):
        size = len(row["content_html"].encode("utf-8"))
        if size > MAX_CONTENT_HTML_BYTES:
            errors.append(
                f"content_html size {size} bytes exceeds {MAX_CONTENT_HTML_BYTES} byte cap"
            )

    # Validate links structure
    if "links" in row and isinstance(row["links"], list):
        for i, link in enumerate(row["links"]):
            if not isinstance(link, dict):
                errors.append(f"links[{i}] must be a dict")
            else:
                for req in ("url", "anchor", "kind", "required"):
                    if req not in link:
                        errors.append(f"links[{i}]: missing field '{req}'")
                if "url" in link and not re.match(r"^https?://", link["url"]):
                    errors.append(f"links[{i}]: invalid URL format: {link['url']}")
                if "kind" in link and link["kind"] not in LINK_KINDS:
                    errors.append(f"links[{i}]: invalid kind '{link['kind']}'")

    # Validate SEO structure
    if "seo" in row and isinstance(row["seo"], dict):
        for req in ("title", "description", "canonical_url"):
            if req not in row["seo"]:
                errors.append(f"seo: missing field '{req}'")
            elif not isinstance(row["seo"][req], str):
                errors.append(f"seo.{req} must be a string")

    # Validate link count (6-8 for backlink articles)
    link_count = len(row.get("links", []))
    if link_count < 6 or link_count > 8:
        errors.append(f"link count {link_count} is not between 6 and 8")

    # Validate title not empty
    if "title" in row and isinstance(row["title"], str) and not row["title"].strip():
        errors.append("title must not be empty")

    # Validate excerpt not empty
    if "excerpt" in row and isinstance(row["excerpt"], str) and not row["excerpt"].strip():
        errors.append("excerpt must not be empty")

    # Validate slug not empty
    if "slug" in row and isinstance(row["slug"], str) and not row["slug"].strip():
        errors.append("slug must not be empty")

    # Validate main_domain appears in content (markdown substring; HTML host-parse
    # lives in cli.validate_backlinks per Unit 6).
    main_domain_error = _check_main_domain_presence(row)
    if main_domain_error is not None:
        errors.append(main_domain_error)

    return errors


def validate_input_payload_strict(row: dict[str, Any]) -> list[str]:
    """Validate an input seed row strictly with exit code 2 semantics."""
    errors = validate_input_payload(row, 0)
    return errors


def validate_publish_payload(row: dict[str, Any]) -> list[str]:
    """Validate a payload ready for publishing. Returns list of error messages."""
    errors = validate_output_payload(row)

    # Additional publish-specific checks
    if "platform" in row:
        msg = reject_unsupported_platform(row["platform"])
        if msg is not None:
            errors.append(msg)

    return errors
