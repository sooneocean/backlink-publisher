"""CLI dispatch and subprocess pipeline helpers — Plan 2026-05-21-007 Unit 4."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from backlink_publisher.content import fetch as content_fetch

from .url_meta import _is_fetch_verify_disabled


# Matches the 5-line config_echo banner emitted by every CLI entrypoint:
#   [<cli>] effective config:
#     config:    <path>
#     env:       <names or (none)>
#     platforms: <names or (none)>
#     sha:       <16hex>
# Followed optionally by `<cli>: run_id=<id>`. Removing both unmasks the real
# diagnostic line, which would otherwise be hidden by [:200] truncation in
# downstream displays (banner + run_id ≈ 210 chars on a typical config).
_BANNER_RE = re.compile(
    r"^\[[a-z0-9_-]+\]\s+effective\s+config:\s*\n"
    r"\s*config:\s+\S.*\n"
    r"\s*env:\s+\S.*\n"
    r"\s*platforms:\s+\S.*\n"
    r"\s*sha:\s+\S.*\n"
    r"(?:[a-z0-9_-]+:\s+run_id=\S+\s*\n)?",
    re.MULTILINE,
)


def strip_cli_diagnostic_banner(stderr: str) -> str:
    """Remove the config_echo banner + optional ``run_id`` line from stderr.

    The banner is diagnostic, not an error — but when stderr gets repurposed
    as an "error message" in publish-history or the WebUI red div, the banner
    eats the first ~200 chars of the [:200] preview and hides the real cause.
    Strip it so the actual ImportError / AuthExpiredError / etc. surfaces.

    Returns the cleaned stderr. If the banner WAS the entire stderr (no real
    error followed), returns a brief explicit sentinel so the operator knows
    the CLI exited without a diagnostic line — rather than the misleading
    full-banner echo that looked like an error.
    """
    if not stderr:
        return stderr
    # Drop the machine-readable typed-error envelope line(s) first. They are parsed
    # separately into PipeResult.error_class / describe_cli_error and must never
    # reach a human view — including the QUARANTINE fallback (a malformed envelope
    # that parse() rejected) and the scheduler's failure path, both of which clean
    # stderr through here. Removed in place (line + its newline) so the banner's
    # own newline structure survives for _BANNER_RE below.
    from backlink_publisher._util.error_envelope import SENTINEL

    stderr = re.sub(
        rf"^[ \t]*{re.escape(SENTINEL)}.*\n?", "", stderr, flags=re.MULTILINE
    )
    cleaned, n = _BANNER_RE.subn("", stderr, count=1)
    cleaned = cleaned.lstrip("\n").rstrip()
    if n and not cleaned:
        return "(CLI exited without an error message; check the WebUI log file for the full diagnostic)"
    if cleaned:
        return cleaned
    return stderr.strip()


def _parse_lines(raw: str) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _wire_content_fetch_ttl_from_env() -> None:
    if _is_fetch_verify_disabled():
        return
    raw = os.environ.get("BACKLINK_GATE_CACHE_TTL_SECONDS", "900").strip()
    try:
        seconds = float(raw)
    except ValueError:
        seconds = 900.0
    if seconds <= 0:
        return
    content_fetch.set_default_max_age(seconds)


_CLI_MODULES = {
    'publish-backlinks': 'backlink_publisher.cli.publish_backlinks',
    'plan-backlinks': 'backlink_publisher.cli.plan_backlinks',
    'validate-backlinks': 'backlink_publisher.cli.validate_backlinks',
    'footprint': 'backlink_publisher.cli.footprint',
    'report-anchors': 'backlink_publisher.cli.report_anchors',
    'equity-ledger': 'backlink_publisher.cli.equity_ledger',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_REPO_ROOT, 'src')


def _rewrite_cli_cmd(cmd):
    """Rewrite bare CLI command to ``sys.executable -m <module>`` with PYTHONPATH=./src.

    Why: the installed entry-point shims can point at a stale editable-install
    path. Running via the current interpreter + repo src/ bypasses that.
    """
    if not cmd:
        return cmd, None
    module = _CLI_MODULES.get(cmd[0])
    if module is None:
        return cmd, None
    new_cmd = [sys.executable, '-m', module, *cmd[1:]]
    env = os.environ.copy()
    env['PYTHONPATH'] = _SRC_DIR + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else ''
    )
    return new_cmd, env


def _base_subprocess_kwargs(stdin, cwd, env, timeout: int = 300):
    """Shared kwargs for subprocess.run in run_pipe and run_pipe_capture."""
    return dict(
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


# Cap on surfaced CLI error text. Long enough that the real diagnostic (an
# ImportError / AuthExpiredError / traceback) is never cut off, bounded so an
# attacker-influenced message (a target URL, a fetched-page snippet folded into
# str(exc)) can't flood the WebUI/logs. Rendering paths are autoescaped (Jinja);
# this is the length half of the untrusted-content guard.
_MAX_SURFACED_ERROR = 4000


def surface_cli_error(stderr: str | None, *, limit: int = _MAX_SURFACED_ERROR) -> str:
    """Banner-stripped, length-bounded CLI error text for operator display.

    Replaces the old ``stderr[:200]`` truncation: strips the config_echo banner
    (which would otherwise eat the first ~210 chars) so the real error survives,
    then caps the result so untrusted/voluminous stderr can't flood the UI.
    """
    cleaned = strip_cli_diagnostic_banner(stderr or "")
    if len(cleaned) > limit:
        return cleaned[:limit].rstrip() + " …(truncated)"
    return cleaned


def describe_cli_error(stderr, *, limit=_MAX_SURFACED_ERROR):
    """Operator-facing description of a failed CLI's stderr, envelope-aware.

    Prefers the Unit 1 typed-error envelope (rendered ``[<error_class>] <message>``)
    when the CLI emitted one — the in-scope CLIs now do for every fatal exit. Falls
    back to :func:`surface_cli_error` (the full banner-stripped text) when no
    envelope is present: an argparse usage error, a crash, or any uninstrumented
    exit. Either way the result is full (never the old ``[:200]`` truncation) and
    length-bounded. The string form, for callers that don't need the structured
    ``error_class``/``exit_code`` fields (the checkpoint route).
    """
    from backlink_publisher._util.error_envelope import parse

    env = parse(stderr or "")
    if env is not None:
        msg = f"[{env.error_class}] {env.message}"
        return msg if len(msg) <= limit else msg[:limit].rstrip() + " …(truncated)"
    return surface_cli_error(stderr, limit=limit)


def run_pipe_capture(cmd, stdin) -> dict[str, str | int]:
    """Run a pipeline command and return ``{stdout, stderr, returncode}``.

    The non-raising sibling of :func:`run_pipe`. Callers that must branch on the
    exit code *with stdout intact* — publish exit-4 partial-failure, checkpoint
    ``--resume`` — use this; ``run_pipe`` discards stdout by raising on any
    non-zero exit.
    """
    new_cmd, env = _rewrite_cli_cmd(cmd)
    try:
        result = subprocess.run(
            new_cmd,
            **_base_subprocess_kwargs(stdin, _REPO_ROOT or os.getcwd(), env),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            'stdout': exc.stdout or '',
            'stderr': exc.stderr or f'CLI timeout after 300 seconds',
            'returncode': -1,  # Indicate timeout
        }
    return {
        'stdout': result.stdout,
        'stderr': result.stderr,
        'returncode': result.returncode,
    }


def run_pipe(cmd, stdin):
    """Run a pipeline command, raising on non-zero exit or silent failure."""
    new_cmd, env = _rewrite_cli_cmd(cmd)
    try:
        result = subprocess.run(
            new_cmd,
            **_base_subprocess_kwargs(stdin, _REPO_ROOT or os.getcwd(), env),
        )
    except subprocess.TimeoutExpired as exc:
        raise Exception(f'CLI timeout after 300 seconds: {exc.stderr or ""}')
    captured = {
        'stdout': result.stdout,
        'stderr': result.stderr,
        'returncode': result.returncode,
    }
    if captured['returncode'] != 0:
        raise Exception(captured['stderr'] or f"Exit code: {captured['returncode']}")
    # Detect silent-failure: exit 0 with empty stdout AND empty stderr is
    # almost always a broken entry-point (missing __main__.py or
    # `if __name__ == "__main__":` guard). Surface a real diagnostic.
    if stdin and not captured['stdout'].strip() and not captured['stderr'].strip():
        invoked = ' '.join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
        new_cmd, _ = _rewrite_cli_cmd(cmd)
        raise Exception(
            f"CLI '{invoked}' produced no output (exit 0, stdout/stderr empty). "
            f"Likely a missing __main__.py or `if __name__ == \"__main__\":` "
            f"guard. Rewritten command: {new_cmd}"
        )
    return {'stdout': captured['stdout'], 'stderr': captured['stderr']}


# ─────────────────────────────────────────────────────────────────────────────
# In-memory work-themed run store (shared between /sites routes)
# ─────────────────────────────────────────────────────────────────────────────

_WORK_THEMED_RUNS: dict[str, dict] = {}
_WORK_THEMED_RUNS_MAX = 50


def _parse_run_result(stdout: str, entry) -> list[dict]:
    """Parse plan-backlinks JSONL stdout into per-work-url status rows."""
    rows = []
    work_urls = list(entry.work_urls or [])
    by_url: dict[str, dict] = {}
    for line in (stdout or '').strip().split('\n'):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        wurl = obj.get('work_url') or obj.get('target_url', '')
        if wurl:
            by_url[wurl] = obj
    for wurl in work_urls:
        obj = by_url.get(wurl)
        if obj is None:
            rows.append({'work_url': wurl, 'status': 'missing'})
        elif obj.get('error'):
            rows.append({'work_url': wurl, 'status': 'scrape_failed',
                         'error': obj.get('error', '')})
        else:
            rows.append({'work_url': wurl, 'status': 'success'})
    return rows
