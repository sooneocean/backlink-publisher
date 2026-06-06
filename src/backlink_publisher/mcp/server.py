"""MCP server exposing all 22 backlink-publisher CLI entrypoints as MCP tools.

Usage:
    bp-mcp                          # stdio transport (for AI agents/IDEs)
    bp-mcp --transport sse          # SSE transport (for web clients)

Each CLI tool becomes a ``@mcp.tool()`` that wraps the entrypoint's ``main()``
by constructing argv from function parameters.

Stdin/stdout tools accept an ``input_jsonl`` parameter instead of reading
stdin, and return stdout content as a string.

Pipe tools (``plan-backlinks | validate-backlinks | publish-backlinks``)
accept pre-computed JSONL input and return JSONL output so an MCP client
can compose the pipeline across tool calls.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from backlink_publisher._util.jsonl import read_jsonl, write_jsonl

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "backlink-publisher",
    instructions="""Backlink publishing pipeline — plan, validate, publish, and monitor dofollow backlinks across 46+ platforms.

All 22 CLI entrypoints are exposed as MCP tools. Tools follow the CLI naming convention (e.g. ``plan_backlinks``, ``validate_backlinks``).

Pipeline tools that process JSONL streams accept ``input_jsonl`` as a string parameter rather than reading stdin. The output is returned as a string.

Pipe the pipeline:
    1. ``plan_backlinks(input_jsonl=seeds)`` → plan JSONL
    2. ``validate_backlinks(input_jsonl=planned_jsonl)`` → validated JSONL
    3. ``publish_backlinks(input_jsonl=validated_jsonl, mode="draft")`` → publish

Read-only diagnostic tools (``footprint``, ``equity_ledger``, ``audit_state``, etc.) take file or data parameters directly.""",
)


# ---------------------------------------------------------------------------
# Helper: run a CLI main() with captured stdin/stdout
# ---------------------------------------------------------------------------

def _run_cli(
    main_fn: Any,
    argv: list[str],
    input_data: str | None = None,
) -> str:
    """Run ``main_fn(argv)`` with optional stdin injection and stdout capture.

    Restores ``sys.stdin`` / ``sys.stdout`` after the call, even on
    ``SystemExit`` (which argparse raises on ``--help`` or ``--version``).
    """
    old_stdin = sys.stdin
    old_stdout = sys.stdout

    stdin_io: io.TextIOWrapper | io.StringIO
    if input_data is not None:
        stdin_io = io.StringIO(input_data)
    else:
        stdin_io = old_stdin

    stdout_buf = io.StringIO()

    try:
        sys.stdin = stdin_io
        sys.stdout = stdout_buf
        main_fn(argv)
    except SystemExit:
        pass
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout

    return stdout_buf.getvalue()


def _input_or_stdin(input_jsonl: str | None = None) -> str | None:
    """Return ``input_jsonl`` if provided, else read from real stdin.

    Used for tools that CAN read stdin but we want to prefer the parameter.
    """
    if input_jsonl is not None:
        return input_jsonl
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return None


# ---------------------------------------------------------------------------
# Tool registration helpers
# ---------------------------------------------------------------------------

_CLI_TOOLS: dict[str, dict[str, Any]] = {}


def _register_cli(
    name: str,
    description: str,
    main_fn: Any,
    *,
    stdin_param: str | None = "input_jsonl",
    skip_args: set[str] | None = None,
    extra_params: list[dict[str, Any]] | None = None,
    param_map: dict[str, str] | None = None,
) -> None:
    """Register a CLI entrypoint as an MCP tool.

    Args:
        name: MCP tool name (lower_snake).
        description: Tool description for the MCP schema.
        main_fn: The CLI ``main()`` function.
        stdin_param: Name of the parameter that accepts stdin content.
            ``None`` means this tool never reads stdin.
        skip_args: CLI arguments to NOT expose as MCP parameters.
        extra_params: Additional non-CLI parameters.
        param_map: Map CLI arg names → MCP param names (e.g. ``{"--foo": "foo"}``).
    """
    _CLI_TOOLS[name] = {
        "description": description,
        "main_fn": main_fn,
        "stdin_param": stdin_param,
        "skip_args": skip_args or set(),
        "extra_params": extra_params or [],
        "param_map": param_map or {},
    }


# ---------------------------------------------------------------------------
# Credential health
# ---------------------------------------------------------------------------

@mcp.tool()
def check_credentials(
    platforms: str | None = None,
) -> str:
    """Check credential health for publishing platforms.

    Verifies tokens, storage state, and proactively refreshes near-expiry
    OAuth tokens (Blogger). Run this before a publish batch to catch
    AuthExpiredError early.

    Args:
        platforms: Comma-separated platform names (e.g. "blogger,medium,velog").
            If omitted, checks all known platforms.
    """
    from backlink_publisher.credentials import check_credentials as _check
    from backlink_publisher.config import load_config

    config = load_config()
    platform_list = [p.strip() for p in platforms.split(",")] if platforms else None
    health = _check(config, platform_list)
    return health.summary()


# ---------------------------------------------------------------------------
# Read-only / data tools
# ---------------------------------------------------------------------------

@mcp.tool()
def footprint(
    input_jsonl: str | None = None,
    baseline: str | None = None,
    fmt: str = "text",
    log_level: str = "WARN",
) -> str:
    """Link footprint analysis — detect anchor-pattern over-optimisation across targets.

    Args:
        input_jsonl: Input JSONL content (seeds or published rows). If omitted, reads from stdin.
        baseline: Path to a baseline JSONL for comparison.
        fmt: Output format: "text" (default) or "jsonl".
        log_level: Log verbosity (DEBUG/INFO/WARN/ERROR).
    """
    from backlink_publisher.cli.footprint import main as _main
    argv = ["--fmt", fmt, "--log-level", log_level]
    if baseline:
        argv += ["--baseline", baseline]
    data = _input_or_stdin(input_jsonl)
    return _run_cli(_main, argv, input_data=data)


@mcp.tool()
def equity_ledger(
    input_jsonl: str | None = None,
    log_level: str = "WARN",
) -> str:
    """Per-target backlink scorecard — read-only diagnostic.

    Args:
        input_jsonl: Input JSONL content. If omitted, reads from stdin.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.equity_ledger import main as _main
    data = _input_or_stdin(input_jsonl)
    return _run_cli(_main, ["--log-level", log_level], input_data=data)


@mcp.tool()
def audit_state(
    log_level: str = "WARN",
) -> str:
    """Dual-state divergence auditor — compare events.db projection vs JSON stores.

    Args:
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.audit_state import main as _main
    return _run_cli(_main, ["--log-level", log_level])


@mcp.tool()
def channel_scorecard(
    log_level: str = "WARN",
) -> str:
    """Per-channel keep/prune scorecard — dofollow, referral value, liveness.

    Args:
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.channel_scorecard import main as _main
    return _run_cli(_main, ["--log-level", log_level])


@mcp.tool()
def canary_targets(
    log_level: str = "WARN",
) -> str:
    """Adapter-contract canary — re-fetch dofollow-tier canary posts.

    Args:
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.canary_targets import main as _main
    return _run_cli(_main, ["--log-level", log_level])


@mcp.tool()
def cull_channels(
    log_level: str = "WARN",
) -> str:
    """Channel-quality cull advisory — read-only blast-radius analysis.

    Args:
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.cull_channels import main as _main
    return _run_cli(_main, ["--log-level", log_level])


@mcp.tool()
def preflight_targets(
    input_jsonl: str | None = None,
    log_level: str = "WARN",
) -> str:
    """Destination-page health check before publish.

    Args:
        input_jsonl: Input JSONL with target URLs. If omitted, reads from stdin.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.preflight_targets import main as _main
    data = _input_or_stdin(input_jsonl)
    return _run_cli(_main, ["--log-level", log_level], input_data=data)


@mcp.tool()
def plan_check(
    plan_doc: str,
    json_output: bool = False,
    log_level: str = "WARN",
) -> str:
    """Validate a plan document's claims frontmatter.

    Args:
        plan_doc: Path to the plan document (or "-" for stdin).
        json_output: If True, emit JSON output instead of human-readable.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.plan_check import main as _main
    argv = ["--log-level", log_level, plan_doc]
    if json_output:
        argv.insert(0, "--json")
    return _run_cli(_main, argv)


@mcp.tool()
def report_anchors(
    input_jsonl: str | None = None,
    min_count: int = 1,
    log_level: str = "WARN",
) -> str:
    """Post-hoc anchor profile report.

    Args:
        input_jsonl: Input JSONL content. If omitted, reads from stdin.
        min_count: Minimum anchor occurrences to include.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.report_anchors import main as _main
    data = _input_or_stdin(input_jsonl)
    return _run_cli(_main, ["--min-count", str(min_count), "--log-level", log_level], input_data=data)


@mcp.tool()
def probe_citations(
    log_level: str = "WARN",
) -> str:
    """GEO AI-citation closed-loop probe — check Perplexity citation presence.

    Args:
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.probe_citations import main as _main
    return _run_cli(_main, ["--log-level", log_level])


@mcp.tool()
def gate_probe(
    gate: str,
    log_level: str = "WARN",
) -> str:
    """Phase-0 falsification gate — run one governance gate and emit verdict.

    Args:
        gate: Gate ID (e.g. "g2", "g3", "g5").
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.gate_probe import main as _main
    return _run_cli(_main, ["--gate", gate, "--log-level", log_level])


# ---------------------------------------------------------------------------
# Pipeline tools (plan → validate → publish)
# ---------------------------------------------------------------------------

@mcp.tool()
def plan_backlinks(
    input_jsonl: str = "",
    default_platform: str = "blogger",
    default_language: str = "zh-CN",
    default_url_mode: str = "A",
    default_publish_mode: str = "draft",
    work_count: int = 10,
    log_level: str = "WARN",
    no_fetch_verify: bool = False,
    zero_auth: bool = False,
    from_csv: str | None = None,
    from_sitemap: str | None = None,
) -> str:
    """Generate backlink article payloads from seed JSONL.

    Args:
        input_jsonl: Input seed JSONL content.
        default_platform: Default platform for generated articles.
        default_language: Content language (zh-CN/en/ru/ko).
        default_url_mode: URL mode (A/B/C).
        default_publish_mode: Publish mode (draft/publish).
        work_count: Per-row article count.
        log_level: Log verbosity.
        no_fetch_verify: Skip URL content verification.
        zero_auth: Restrict to zero-auth platforms only.
        from_csv: Read targets from CSV file instead of JSONL.
        from_sitemap: Fetch targets from sitemap URL instead.
    """
    from backlink_publisher.cli.plan_backlinks import main as _main
    argv = [
        "--default-platform", default_platform,
        "--default-language", default_language,
        "--default-url-mode", default_url_mode,
        "--default-publish-mode", default_publish_mode,
        "--work-count", str(work_count),
        "--log-level", log_level,
    ]
    if no_fetch_verify:
        argv.append("--no-fetch-verify")
    if zero_auth:
        argv.append("--zero-auth")
    if from_csv:
        argv += ["--from-csv", from_csv]
    if from_sitemap:
        argv += ["--from-sitemap", from_sitemap]
    return _run_cli(_main, argv, input_data=input_jsonl)


@mcp.tool()
def validate_backlinks(
    input_jsonl: str = "",
    log_level: str = "WARN",
    zero_auth: bool = False,
) -> str:
    """Validate planned backlink payloads.

    Args:
        input_jsonl: Input JSONL content (output of plan_backlinks).
        log_level: Log verbosity.
        zero_auth: Restrict validation to zero-auth platforms.
    """
    from backlink_publisher.cli.validate_backlinks import main as _main
    argv = ["--log-level", log_level]
    if zero_auth:
        argv.append("--zero-auth")
    return _run_cli(_main, argv, input_data=input_jsonl)


@mcp.tool()
def publish_backlinks(
    input_jsonl: str = "",
    platform: str | None = None,
    mode: str = "draft",
    log_level: str = "WARN",
    dry_run: bool = False,
    resume: str | None = None,
    zero_auth: bool = False,
) -> str:
    """Publish validated backlink payloads via adapter dispatcher.

    Args:
        input_jsonl: Input validated JSONL content.
        platform: Target platform (omit for row-level platform).
        mode: Publish mode (draft/publish).
        log_level: Log verbosity.
        dry_run: Preview without publishing.
        resume: Resume file path for interrupted runs.
        zero_auth: Filter to zero-auth platforms only.
    """
    from backlink_publisher.cli.publish_backlinks import main as _main
    argv = ["--mode", mode, "--log-level", log_level]
    if platform:
        argv += ["--platform", platform]
    if dry_run:
        argv.append("--dry-run")
    if resume:
        argv += ["--resume", resume]
    if zero_auth:
        argv.append("--zero-auth")
    return _run_cli(_main, argv, input_data=input_jsonl)


# ---------------------------------------------------------------------------
# Planning / gap analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def plan_gap(
    input_jsonl: str = "",
    desired: int = 3,
    language: str = "en",
    log_level: str = "WARN",
    desired_map: str | None = None,
) -> str:
    """Deficit-driven re-plan — generate seed JSONL from equity-ledger data.

    Args:
        input_jsonl: Input equity-ledger JSONL.
        desired: Target per-target live-dofollow count.
        language: Target language for new articles.
        log_level: Log verbosity.
        desired_map: Path to JSON mapping target_url→desired count.
    """
    from backlink_publisher.cli.plan_gap import main as _main
    argv = [
        "--desired", str(desired),
        "--language", language,
        "--log-level", log_level,
    ]
    if desired_map:
        argv += ["--desired-map", desired_map]
    return _run_cli(_main, argv, input_data=input_jsonl)


@mcp.tool()
def recheck_backlinks(
    log_level: str = "WARN",
    probe: bool = False,
    fail_on_dead: bool = False,
    input_jsonl: str | None = None,
) -> str:
    """Post-publish backlink survival re-verification.

    Args:
        log_level: Log verbosity.
        probe: Enable network probing (default: dry-run).
        fail_on_dead: Exit 6 on deterministic dead links.
        input_jsonl: Optional stdin JSONL to recheck (omit to check from events.db).
    """
    from backlink_publisher.cli.recheck_backlinks import main as _main
    argv = ["--log-level", log_level]
    if probe:
        argv.append("--probe")
    if fail_on_dead:
        argv.append("--fail-on-dead")
    data = _input_or_stdin(input_jsonl)
    return _run_cli(_main, argv, input_data=data)


# ---------------------------------------------------------------------------
# Credential binding
# ---------------------------------------------------------------------------

@mcp.tool()
def bind_channel(
    channel: str,
    log_level: str = "WARN",
) -> str:
    """Bind a browser-based publishing channel (headed Playwright session).

    Args:
        channel: Channel name (velog/medium/blogger).
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.bind_channel import main as _main
    return _run_cli(_main, ["--channel", channel, "--log-level", log_level])


# ---------------------------------------------------------------------------
# Misc / utility
# ---------------------------------------------------------------------------

@mcp.tool()
def generate_backlink_text(
    input_jsonl: str = "",
    log_level: str = "WARN",
) -> str:
    """Generate backlink anchor text for planned articles.

    Args:
        input_jsonl: Input JSONL content.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.generate_backlink_text import main as _main
    return _run_cli(_main, ["--log-level", log_level], input_data=input_jsonl)


@mcp.tool()
def phase0_seal(
    operation: str,
    log_level: str = "WARN",
    platform: str | None = None,
    channel: str | None = None,
) -> str:
    """Phase0 seal operations — config health checks and token verification.

    Args:
        operation: Seal operation (e.g. "check", "verify", "status").
        log_level: Log verbosity.
        platform: Target platform.
        channel: Target binding channel.
    """
    from backlink_publisher.cli.phase0_seal import main as _main
    argv = [operation, "--log-level", log_level]
    if platform:
        argv += ["--platform", platform]
    if channel:
        argv += ["--channel", channel]
    return _run_cli(_main, argv)


@mcp.tool()
def canonical_expand(
    input_jsonl: str = "",
    log_level: str = "WARN",
) -> str:
    """Expand canonical URLs from gathered link data.

    Args:
        input_jsonl: Input JSONL content.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.canonical_expand import main as _main
    return _run_cli(_main, ["--log-level", log_level], input_data=input_jsonl)


@mcp.tool()
def pr_opportunities(
    input_jsonl: str = "",
    log_level: str = "WARN",
) -> str:
    """Analyse PR opportunities from gathered link data.

    Args:
        input_jsonl: Input JSONL content.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.pr_opportunities import main as _main
    return _run_cli(_main, ["--log-level", log_level], input_data=input_jsonl)


@mcp.tool()
def comment(
    input_jsonl: str = "",
    log_level: str = "WARN",
) -> str:
    """Add comments to published articles.

    Args:
        input_jsonl: Input JSONL content.
        log_level: Log verbosity.
    """
    from backlink_publisher.cli.comment import main as _main
    return _run_cli(_main, ["--log-level", log_level], input_data=input_jsonl)


@mcp.tool()
def overview() -> str:
    """Get a summary of all available MCP tools with their descriptions."""
    lines = ["# backlink-publisher MCP tools\n"]
    tools = {
        "plan_backlinks": "Generate article payloads from seeds",
        "validate_backlinks": "Validate planned payloads",
        "publish_backlinks": "Publish via adapter dispatcher",
        "plan_gap": "Deficit-driven re-planning",
        "recheck_backlinks": "Post-publish survival recheck",
        "footprint": "Link footprint analysis",
        "equity_ledger": "Per-target backlink scorecard",
        "audit_state": "Dual-state divergence auditor",
        "channel_scorecard": "Per-channel keep/prune scorecard",
        "canary_targets": "Adapter-contract canary check",
        "cull_channels": "Channel-quality cull advisory",
        "preflight_targets": "Pre-publish target health check",
        "plan_check": "Plan document claims validation",
        "report_anchors": "Anchor profile report",
        "probe_citations": "GEO AI citation probe",
        "gate_probe": "Phase-0 falsification gate",
        "generate_backlink_text": "Generate backlink anchor text",
        "phase0_seal": "Phase0 seal operations",
        "canonical_expand": "Expand canonical URLs",
        "pr_opportunities": "PR opportunity analysis",
        "comment": "Add comments to articles",
        "bind_channel": "Bind a browser channel",
    }
    for name, desc in tools.items():
        lines.append(f"- `{name}` — {desc}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server.

    Uses stdio transport by default (for AI agents / MCP clients).
    Pass ``--transport sse`` for SSE (web clients).
    """
    import argparse
    parser = argparse.ArgumentParser(prog="bp-mcp", description="Backlink Publisher MCP server")
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for SSE transport")
    parser.add_argument("--port", type=int, default=8910, help="Port for SSE transport")
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
