"""Config Echo Chamber — operator-visible config resolution disclosure.

Round-3 ideation #7. Operators routinely hit the "I edited the config but
nothing changed" footgun because Python's silent merge of default-path TOML,
``--config`` CLI flag, and ``BACKLINK_*`` env vars leaves no diagnostic trail.
This module emits a 4-line banner at every CLI entrypoint:

1. The config.toml file path that ``load_config`` resolved
2. Which ``BACKLINK_*`` env vars are active (names only — values redacted)
3. Which platforms (blogger / medium) have credentials wired
4. SHA256 of the canonicalised effective config dict

The same SHA is stamped into every plan-backlinks payload's metadata
(``metadata.config_sha``) so months-old artifacts can be reverse-mapped to
the config that produced them.

Design choice — SHA scope
-------------------------

The SHA is over the **resolved** Config dataclass (sorted-key JSON
serialization), not raw TOML bytes. Rationale: whitespace / key-order edits
in the TOML file don't change semantics; semantic edits do. An operator
adding a comment shouldn't appear to have a different config from the
auditor's perspective. Trade-off: whitespace-only changes (potentially
worth flagging) aren't detected — but those rarely have operational impact.

Design choice — env var disclosure
----------------------------------

Only NAMES are shown, never VALUES. The redactor in PipelineLogger handles
log records; this banner is plain text. ``BACKLINK_LLM_API_KEY`` and similar
sensitive env vars surface only as "set" / "unset" flags. The operator
remembers the value; the banner just confirms which override path is
active.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from .config import Config

#: ``BACKLINK_*`` env var names this module surfaces in the banner. Operator
#: pastes a different one → not shown (no surprise). Extending the list
#: requires adding the name here; banners stay deterministic.
KNOWN_ENV_OVERRIDES: tuple[str, ...] = (
    "BACKLINK_LLM_API_KEY",
    "BACKLINK_NO_FETCH_VERIFY",
    "BACKLINK_GATE_CACHE_TTL_SECONDS",
    "BACKLINK_PUBLISHER_ALLOW_NETWORK",
)


def _canonicalise_for_sha(value: Any) -> Any:
    """Return a JSON-serialisable, deterministic representation of ``value``.

    - dataclass → dict of its fields (recursively canonicalised)
    - dict → sorted by key (recursively canonicalised)
    - list → recursively canonicalised; order preserved (list order is
      semantic in this codebase — anchor pool order affects rendering)
    - tuple → list (JSON has no tuple)
    - Path → str
    - frozenset → sorted list (sets are unordered but JSON needs order)
    - primitives (str/int/float/bool/None) → passthrough

    Anything else falls back to ``repr()`` so unknown types don't crash
    the SHA computation, at the cost of stability if the object's repr
    changes between releases.
    """
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _canonicalise_for_sha(getattr(value, f.name))
            for f in fields(value)
        }
    if isinstance(value, dict):
        return {k: _canonicalise_for_sha(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [_canonicalise_for_sha(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalise_for_sha(item) for item in value]
    if isinstance(value, frozenset) or isinstance(value, set):
        return [_canonicalise_for_sha(item) for item in sorted(value, key=str)]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def compute_config_sha(config: Config) -> str:
    """Return a 16-char SHA256 prefix of the canonicalised Config.

    Stable across whitespace / key-order edits in the source TOML; sensitive
    to semantic changes (new blog_id, edited anchor pool, env-driven
    override). The 16-char prefix matches our run-ID convention (8 random
    hex + dash) so the SHA fits cleanly into JSONL headers.
    """
    canon = _canonicalise_for_sha(config)
    serialised = json.dumps(canon, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
    return digest[:16]


def detect_env_overrides() -> list[str]:
    """Return the subset of ``KNOWN_ENV_OVERRIDES`` whose value is set in
    the current process environment. Empty string and unset are both
    treated as "not active". Caller renders names only — never values.
    """
    return [
        name for name in KNOWN_ENV_OVERRIDES
        if os.environ.get(name, "").strip()
    ]


def active_platforms(config: Config) -> list[str]:
    """Return the platform names with wired credentials.

    ``blogger`` is active when ``blogger_blog_ids`` is non-empty (regardless
    of OAuth credentials — the API call path needs blog_id mapping).
    ``medium`` is active when any viable publishing path exists: integration
    token, stored OAuth token file, or Playwright library installed (browser
    fallback). This mirrors verify_adapter_setup's ready-set so the CLI banner
    and the UI badge agree on the same truth. Returned in stable alphabetical order.
    """
    out: list[str] = []
    if config.blogger_blog_ids:
        out.append("blogger")

    from backlink_publisher.config import load_medium_token
    from backlink_publisher.publishing.adapters.medium_browser import (
        sync_playwright as _spw,
    )
    medium_ready = bool(
        config.medium_integration_token
        or load_medium_token()
        or _spw is not None
    )
    if medium_ready:
        out.append("medium")

    return sorted(out)


def banner_lines(
    config: Config, *, config_path: Path | str | None = None,
) -> list[str]:
    """Build the 4-line banner content. Caller renders to stderr.

    Lines are deliberately short — the banner is operator-glance, not a
    diagnostic dump. The SHA appears on line 4 so it's grep-friendly.
    """
    if config_path is None:
        config_path = config.config_dir / "config.toml"
    path_str = str(config_path)

    env_names = detect_env_overrides()
    env_summary = ", ".join(env_names) if env_names else "(none)"

    platforms = active_platforms(config)
    platforms_summary = ", ".join(platforms) if platforms else "(none)"

    sha = compute_config_sha(config)

    return [
        f"  config:    {path_str}",
        f"  env:       {env_summary}",
        f"  platforms: {platforms_summary}",
        f"  sha:       {sha}",
    ]


def emit_banner(
    config: Config, cli_name: str, *,
    config_path: Path | str | None = None,
    stream=None,
) -> str:
    """Write the banner to ``stream`` (default ``sys.stderr``) and return
    the config SHA for downstream stamping into JSONL payloads.

    The CLI's main() calls this once at start. Subsequent recon / log
    events also reference the SHA (via the returned value or via
    :func:`compute_config_sha` again — both are cheap and deterministic).
    """
    if stream is None:
        stream = sys.stderr

    lines = banner_lines(config, config_path=config_path)
    header = f"[{cli_name}] effective config:"

    print(header, file=stream)
    for line in lines:
        print(line, file=stream)
    stream.flush()

    # Return the SHA so caller can stamp it into JSONL metadata.
    return compute_config_sha(config)
