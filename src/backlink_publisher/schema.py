"""Schema definitions and validation for backlink pipeline payloads."""

from __future__ import annotations

import re
from typing import Any

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

SUPPORTED_LANGUAGES = {"zh-CN", "en", "ru"}
SUPPORTED_PLATFORMS = {"blogger", "medium"}
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
    "content_markdown": str,
    "links": list,
    "seo": dict,
}

LINK_KINDS = {"main_domain", "target", "supporting", "extra", "category", "detail"}

MAX_PAYLOAD_SIZE_BYTES = 256 * 1024  # 256 KB


def validate_input_payload(row: dict[str, Any], line_num: int) -> list[str]:
    """Validate an input seed row. Returns list of error messages."""
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

    if "platform" in row and row["platform"] not in SUPPORTED_PLATFORMS:
        errors.append(
            f"line {line_num}: unsupported platform '{row['platform']}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_PLATFORMS))}"
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

    # Validate URLs
    for url_field in ("target_url", "main_domain"):
        if url_field in row:
            url_val = str(row[url_field])
            if not re.match(r"^https?://", url_val):
                errors.append(f"line {line_num}: field '{url_field}' is not a valid URL: {url_val}")

    # Validate seed_keywords if present
    if "seed_keywords" in row:
        if not isinstance(row["seed_keywords"], list):
            errors.append(f"line {line_num}: 'seed_keywords' must be a list")
        else:
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

    # Validate main_domain appears in content_markdown
    # Strip trailing slash from both sides before comparing so that
    # 'https://example.com/' and 'https://example.com' are treated as equal.
    if "main_domain" in row and "content_markdown" in row:
        md = row["content_markdown"]
        md_domain = row["main_domain"]
        if isinstance(md, str) and isinstance(md_domain, str):
            md_domain_norm = md_domain.rstrip("/")
            if md_domain_norm not in md and md_domain not in md:
                errors.append(f"main_domain '{md_domain}' does not appear in content_markdown")

    return errors


def validate_input_payload_strict(row: dict[str, Any]) -> list[str]:
    """Validate an input seed row strictly with exit code 2 semantics."""
    errors = validate_input_payload(row, 0)
    return errors


def validate_publish_payload(row: dict[str, Any]) -> list[str]:
    """Validate a payload ready for publishing. Returns list of error messages."""
    errors = validate_output_payload(row)

    # Additional publish-specific checks
    if "platform" in row and row["platform"] == "linkedin":
        errors.append("platform 'linkedin' is not supported in this version")

    return errors