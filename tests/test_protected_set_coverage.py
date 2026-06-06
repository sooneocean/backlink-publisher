"""Layer 3 — derived protected set + coverage CI check (Plan 2026-05-27-005 Unit 6).

Derives the protected credential-file set from the actual namers in source code
and verifies that every resolver-derived credential file is covered by at least
one glob in PROTECTED_GLOBS, and that the hard-coded FLOOR_SET is always present.

The check is **two-sided**:
  (a) every ``_config_dir()``/``config.config_dir``-derived credential file found
      in source is matched by at least one glob in PROTECTED_GLOBS;
  (b) the FLOOR_SET of must-watch files is asserted present — because the most
      dangerous files (``llm-settings.json``, ``config.toml``, ``events.db``,
      ``persona.salt``) may not surface through a pure-derivation scan (they are
      written outside the standard namer pattern or via a constant).

CI impact: adding an uncovered credential namer to source reds this test, prompting
the developer to extend PROTECTED_GLOBS.  Shrinking PROTECTED_GLOBS without
covering all discovered namers also reds it.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterator

import pytest

# ---------------------------------------------------------------------------
# Protected globs (patterns matched against config-dir-relative filenames)
# ---------------------------------------------------------------------------

#: Infix-pattern globs that cover the known credential files.
#: Use infix globs (*-token.json) for channel-prefixed files —
#: NOT prefix globs (blogger-token.json) which must be updated per channel.
PROTECTED_GLOBS: frozenset[str] = frozenset(
    {
        # Token / OAuth files (channel-prefixed)
        "*-token.json",
        # Credentials files (session cookies, username+password, API keys)
        "*-credentials.json",
        # Browser-session cookies (Playwright storage state, raw cookie JSON)
        "*-cookies.json",
        # Legacy Playwright storage-state files
        "*-storage-state.json",
        # Exact-name files that don't fit the infix patterns above
        "config.toml",
        "llm-settings.json",
        "events.db",
        "persona.salt",
        # WebUI history / queue / settings (contain operator URLs and schedules)
        "publish-history.json",
        "campaign-profiles.json",
        "draft-queue.json",
        "schedule-settings.json",
        "publish-queue.json",
        "channel-status.json",
        # Medium auth-state files (0600, written by bind recipe)
        "*-last-account.txt",
        "*-last-account.tentative",
        "*-meta.json",
        "*-meta.json.tentative",
        # Canary health state (contains per-platform drift history, operator platform data)
        "canary-health.json",
        # Asset version stamp (written to config dir by _compute_asset_version)
        "asset-version.stamp",
    }
)

#: Floor set: these specific file basenames MUST be matched by PROTECTED_GLOBS
#: regardless of whether they surface through the derivation scan.  A pure-
#: derivation scan misses the most dangerous files because they are written
#: through indirect paths (constants, writer helpers, WebUI helpers).
FLOOR_SET: frozenset[str] = frozenset(
    {
        "config.toml",
        "llm-settings.json",
        "events.db",
        "persona.salt",
    }
)

# ---------------------------------------------------------------------------
# Source scanner
# ---------------------------------------------------------------------------

#: Directories that contain resolver-derived credential namers.
_SCAN_ROOTS: tuple[str, ...] = (
    "src/backlink_publisher",
    "webui_app",
    "webui_store",
)

#: Regex that matches a quoted filename following a config_dir expression.
#: Covers:
#:   _config_dir() / "some-file.json"
#:   config.config_dir / "some-file.json"
#:   _bp_config_dir() / "some-file.json"
#:   _resolve_config_dir() / "some-file.json"
#:   _cfg._config_dir() / "some-file.json"
#:   _cfg._cache_dir() / "some-file.json"   (screenshots dir — excluded by extension)
#: It deliberately does NOT match bare string literals (e.g. "events.db" in
#: comments or doc strings) — those are handled by the FLOOR_SET.
_NAMER_RE = re.compile(
    r'(?:_config_dir|config\.config_dir|_bp_config_dir|_resolve_config_dir'
    r'|_cfg\._config_dir|_cfg\._cache_dir'
    r')\s*\(\s*\)\s*/\s*["\']([^"\']+)["\']'
)


def _source_files(repo_root: Path) -> Iterator[Path]:
    """Yield every .py file under the scan roots."""
    for root in _SCAN_ROOTS:
        scan_dir = repo_root / root
        if not scan_dir.exists():
            continue
        for path in scan_dir.rglob("*.py"):
            yield path


def collect_credential_namers(repo_root: Path) -> list[tuple[str, Path, int]]:
    """Return ``(filename, source_path, lineno)`` tuples for every resolver-derived
    credential file discovered in the source tree.

    The scan uses a regex over raw source text (fast, no AST overhead) and is
    intentionally conservative: it only matches the patterns in ``_NAMER_RE``.
    A file that cannot be detected by this pattern is tracked by the FLOOR_SET
    instead.
    """
    results: list[tuple[str, Path, int]] = []
    for src_path in _source_files(repo_root):
        try:
            text = src_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _NAMER_RE.finditer(line):
                filename = match.group(1)
                # Keep only files, not directories like "screenshots" or "banners"
                if "." not in filename:
                    continue
                results.append((filename, src_path, lineno))
    return results


def is_covered(filename: str) -> bool:
    """Return True if ``filename`` matches at least one glob in PROTECTED_GLOBS."""
    return any(fnmatch.fnmatch(filename, glob) for glob in PROTECTED_GLOBS)


# ---------------------------------------------------------------------------
# Helpers for parametrize
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Return the repository root (parent of tests/)."""
    return Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_floor_set_covered_by_protected_globs() -> None:
    """Every member of FLOOR_SET must be matched by at least one PROTECTED_GLOB.

    The floor set is the hard-coded guard for files that are either written via
    constants (events.db → _DB_FILENAME) or helpers not visible to _NAMER_RE.
    If someone accidentally removes a glob that covers a floor entry, this test
    fails immediately — before Unit 7's tripwire even runs.
    """
    uncovered = [f for f in sorted(FLOOR_SET) if not is_covered(f)]
    assert not uncovered, (
        f"FLOOR_SET members not matched by any PROTECTED_GLOB: {uncovered!r}. "
        f"Update PROTECTED_GLOBS to cover them."
    )


def test_every_discovered_namer_is_covered() -> None:
    """Every credential filename discovered by the source scan must match a glob.

    If a new ``_config_dir() / "newchan-token.json"`` namer is added to source
    and 'newchan-token.json' is covered by '*-token.json', this test stays green.
    If the file has an unusual name that no existing glob covers, the test fails
    and the developer must extend PROTECTED_GLOBS.
    """
    repo_root = _repo_root()
    namers = collect_credential_namers(repo_root)
    uncovered = [
        (filename, str(src.relative_to(repo_root)), lineno)
        for filename, src, lineno in namers
        if not is_covered(filename)
    ]
    if uncovered:
        lines = "\n".join(
            f"  {fname!r} in {src}:{lineno}" for fname, src, lineno in uncovered
        )
        pytest.fail(
            f"Resolver-derived credential files not covered by any PROTECTED_GLOB:\n"
            f"{lines}\n"
            f"Add a glob to PROTECTED_GLOBS in tests/test_protected_set_coverage.py "
            f"(or fold the namer to use _config_dir() if it currently uses raw Path.home())."
        )


def test_scanner_finds_at_least_n_namers() -> None:
    """Anti-no-op: the scanner must find a minimum count of credential namers.

    Prevents a silently-vacuous scanner (e.g., regex accidentally matches
    nothing) from making all coverage checks trivially pass.
    """
    namers = collect_credential_namers(_repo_root())
    filenames = {fname for fname, _, _ in namers}
    # Known minimum: frw-token, blogger-token, velog-cookies, medium-cookies,
    # telegraph-token, devto-token, linkedin-token, tumblr-credentials,
    # substack-credentials, publish-history, channel-status → ≥11 distinct files.
    assert len(filenames) >= 11, (
        f"Scanner found only {len(filenames)} distinct credential files — "
        f"expected ≥11. The regex may have stopped matching."
    )


def test_synthetic_uncovered_namer_is_detected() -> None:
    """Anti-no-op: a filename that matches no glob is flagged as uncovered.

    Verifies that is_covered() actually rejects files that don't fit any pattern.
    """
    assert not is_covered("totally-unknown-xyzzy.txt"), (
        "'totally-unknown-xyzzy.txt' should NOT be covered by any PROTECTED_GLOB"
    )
    assert not is_covered("newchan-2fa-secret.bin"), (
        "'newchan-2fa-secret.bin' should NOT be covered by any PROTECTED_GLOB"
    )


def test_infix_glob_covers_velog_storage_state() -> None:
    """Edge case: velog-storage-state.json (channel-prefixed) matches the infix glob."""
    assert is_covered("velog-storage-state.json"), (
        "'velog-storage-state.json' should be covered by '*-storage-state.json'"
    )


def test_non_secret_config_json_not_covered() -> None:
    """Over-match guard: a non-secret '*-config.json' file is NOT falsely swept in.

    Broad globs must not match ordinary config/settings files that don't hold
    secrets — otherwise every JSON config file in the project would be "protected"
    and the tripwire would fire on benign non-secret writes.
    """
    # These look like plausible non-secret config files that must NOT be matched
    assert not is_covered("adapter-config.json"), (
        "'adapter-config.json' should NOT be covered — it is not a credential file"
    )
    assert not is_covered("platform-settings.json"), (
        "'platform-settings.json' should NOT be covered"
    )


def test_floor_set_members_are_in_protected_globs_or_exact_match() -> None:
    """Floor set integrity: each member is either an exact glob or matched by a pattern."""
    for fname in FLOOR_SET:
        assert is_covered(fname), (
            f"Floor set member {fname!r} not covered by PROTECTED_GLOBS — "
            f"add it as an exact entry or extend the globs."
        )


def test_scanner_recurses_into_adapters_and_webui() -> None:
    """The scanner must reach files in src/publishing/adapters/ and webui_store/."""
    repo_root = _repo_root()
    namers = collect_credential_namers(repo_root)
    source_paths = {src for _, src, _ in namers}

    # At least one hit must come from the adapter tree
    from_adapters = [
        s for s in source_paths
        if "publishing/adapters" in str(s)
    ]
    assert from_adapters, (
        "Scanner found no credential namers under publishing/adapters/ — "
        "check that _SCAN_ROOTS includes 'src/backlink_publisher' and that "
        "_NAMER_RE matches the adapter patterns."
    )

    # At least one hit must come from webui_store/
    from_webui_store = [s for s in source_paths if "webui_store" in str(s)]
    assert from_webui_store, (
        "Scanner found no credential namers under webui_store/ — "
        "check that _SCAN_ROOTS includes 'webui_store'."
    )


def test_medium_cookies_discovered_by_scanner() -> None:
    """medium-cookies.json (written via _config_dir() in medium_browser.py) is found."""
    namers = collect_credential_namers(_repo_root())
    filenames = {fname for fname, _, _ in namers}
    assert "medium-cookies.json" in filenames, (
        "'medium-cookies.json' not found by scanner — check that "
        "medium_browser.py's _config_dir() / 'medium-cookies.json' "
        "is still present and _NAMER_RE matches it."
    )


def test_llm_settings_discovered_by_scanner() -> None:
    """llm-settings.json (written via _config_dir() in webui_app/helpers/contexts.py) is found."""
    namers = collect_credential_namers(_repo_root())
    filenames = {fname for fname, _, _ in namers}
    assert "llm-settings.json" in filenames, (
        "'llm-settings.json' not found by scanner — check that "
        "webui_app/helpers/contexts.py's _config_dir() / 'llm-settings.json' "
        "is still present and _NAMER_RE matches it."
    )
