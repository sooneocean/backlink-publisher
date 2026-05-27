"""PipelineAPI — structured wrapper around plan/validate/publish CLI invocations.

Phase A: still delegates to ``run_pipe`` (subprocess).  Phase B will replace
the subprocess bridge with in-process ``main(argv)`` calls.

Every method returns a ``PipeResult`` so callers never touch raw ``run_pipe``
or parse JSONL inline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from backlink_publisher._util.error_envelope import parse as _parse_envelope

from ..helpers.cli_runner import (
    _MAX_SURFACED_ERROR,
    run_pipe,
    run_pipe_capture,
    strip_cli_diagnostic_banner,
    surface_cli_error,
)


# ── structured result ──────────────────────────────────────────────────────


@dataclass
class PipeResult:
    """Structured result from a single pipeline CLI invocation.

    Callers interact with ``.success`` / ``.error`` / ``.rows`` instead of
    raw stdout / stderr strings.

    On failure, ``.error`` is the full operator-facing message (never the old
    ``stderr[:200]`` truncation), and ``.error_class`` / ``.exit_code`` carry the
    typed-error envelope's fields when the CLI emitted one (Unit 1/2). When no
    envelope is present (argparse usage error, crash, uninstrumented exit), the
    QUARANTINE branch sets ``error_class="unrecognized"`` and ``.error`` to the
    full banner-stripped stderr — loud, never empty (silent-drop lesson).
    """

    stdout: str = ""
    stderr: str = ""
    success: bool = True
    error: str | None = None
    error_class: str | None = None
    exit_code: int | None = None

    # ── derived helpers ──────────────────────────────────────────────────

    @property
    def stderr_cleaned(self) -> str:
        """Stderr with the config-echo diagnostic banner stripped."""
        return strip_cli_diagnostic_banner(self.stderr)

    @property
    def rows(self) -> list[dict[str, Any]]:
        """Parse stdout as JSONL into a list of dict rows.

        Returns ``[]`` when stdout is empty or unparseable — caller checks
        ``.success`` first.
        """
        if not self.stdout:
            return []
        result: list[dict[str, Any]] = []
        for line in self.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return result


def _typed_error_result(stderr: str, fallback_label: str) -> PipeResult:
    """Build a failed ``PipeResult``, parsing the typed-error envelope if present.

    Envelope present → ``error_class``/``exit_code``/``error`` from the CLI's own
    taxonomy (e.g. ``AuthExpiredError``/3). Envelope absent → QUARANTINE:
    ``error_class="unrecognized"`` and ``error`` = the full banner-stripped stderr
    (never truncated), so a usage error / crash / uninstrumented exit still
    surfaces in full.
    """
    env = _parse_envelope(stderr)
    if env is not None:
        # Bound the message the same way surface_cli_error bounds QUARANTINE text:
        # an envelope message can be large (validate aggregate) or carry untrusted
        # content (a target URL / fetched snippet), and it flows verbatim into logs
        # and the persisted history JSON. Cap it so it can't flood either.
        message = env.message
        if len(message) > _MAX_SURFACED_ERROR:
            message = message[:_MAX_SURFACED_ERROR].rstrip() + " …(truncated)"
        return PipeResult(
            stderr=stderr,
            success=False,
            error=message,
            error_class=env.error_class,
            exit_code=env.exit_code,
        )
    return PipeResult(
        stderr=stderr,
        success=False,
        error=surface_cli_error(stderr) or fallback_label,
        error_class="unrecognized",
    )


# ── helpers used by both PipelineAPI and external callers (scheduler) ──────


def parse_publish_results(jsonl_str: str) -> list[dict[str, Any]]:
    """Parse publish-backlinks JSONL stdout into result rows.

    Duplicate of ``helpers.history._parse_publish_results`` — consolidated
    here so the scheduler and routes share one canonical parser.
    """
    results: list[dict[str, Any]] = []
    for line in (jsonl_str or "").strip().split("\n"):
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return results


def publish_state_summary(
    publish_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate publish state from per-row results.

    Returns ``{"n_ok", "n_failed", "state"}`` where *state* is one of
    ``"all_success"``, ``"all_failed"``, ``"partial_success"``.
    """
    n_ok = sum(
        1 for r in publish_results
        if (r.get("published_url") or "").strip()
        or (r.get("draft_url") or "").strip()
    )
    n_failed = len(publish_results) - n_ok

    if n_failed == 0:
        state = "all_success"
    elif n_ok == 0:
        state = "all_failed"
    else:
        state = "partial_success"

    failure_msgs = [
        (r.get("error") or "").strip() or f"{r.get('status') or 'failed'} (no URL)"
        for r in publish_results
        if not ((r.get("published_url") or "").strip()
                or (r.get("draft_url") or "").strip())
    ]

    return {
        "n_ok": n_ok,
        "n_failed": n_failed,
        "state": state,
        "failure_detail": "；".join(m for m in failure_msgs if m),
    }


# ── PipelineAPI ────────────────────────────────────────────────────────────


class PipelineAPI:
    """Encapsulates the three pipeline stage invocations.

    Usage::

        api = PipelineAPI()
        result = api.plan(seed_json)
        if result.success:
            plans = result.rows
    """

    # ── shared invocation ──────────────────────────────────────────────────

    def _invoke(self, cmd: list[str], stdin: str, label: str) -> PipeResult:
        """Run one pipeline CLI; success → rows, failure → typed error.

        ``run_pipe`` raises with the CLI's full stderr (banner + envelope) on any
        non-zero exit or silent failure; :func:`_typed_error_result` turns that
        into a typed/QUARANTINE ``PipeResult``.
        """
        try:
            raw = run_pipe(cmd, stdin)
            return PipeResult(
                stdout=raw["stdout"],
                stderr=raw.get("stderr", ""),
                success=True,
            )
        except Exception as exc:
            return _typed_error_result(str(exc), label)

    def _invoke_capture(self, cmd: list[str], stdin: str, label: str) -> PipeResult:
        """Run one pipeline CLI, **preserving stdout on non-zero exit**.

        The non-raising sibling of :meth:`_invoke`. ``run_pipe`` discards stdout
        by raising on any non-zero exit; some CLIs carry meaningful stdout *with*
        a non-zero code — ``report-anchors`` exit-6 (alarm raised, but the report
        document is on stdout) and ``publish-backlinks`` exit-4 (partial success:
        some rows published). Those callers branch on ``exit_code`` and still need
        the rows, so they use this path.

        On success ``exit_code`` is set to ``0`` (not ``None``) so callers like
        ``checkpoint.py`` can branch 0 / 4 / else uniformly. Carries the same
        silent-failure guard as ``run_pipe``: a 0-exit with empty stdout *and*
        stderr on non-empty stdin is almost always a broken entry-point.
        """
        captured = run_pipe_capture(cmd, stdin)
        rc = captured["returncode"]
        stdout = captured["stdout"]
        stderr = captured.get("stderr", "")
        if rc == 0:
            if stdin and not stdout.strip() and not stderr.strip():
                return PipeResult(
                    success=False,
                    error=f"{label}: CLI produced no output (exit 0, stdout/stderr "
                    "empty) — likely a broken entry-point.",
                    error_class="unrecognized",
                    exit_code=0,
                )
            return PipeResult(stdout=stdout, stderr=stderr, success=True, exit_code=0)
        # Non-zero: keep stdout, attach the typed error, and ensure exit_code
        # reflects the real code (the envelope's exit_code wins when present).
        result = _typed_error_result(stderr, label)
        result.stdout = stdout
        if result.exit_code is None:
            result.exit_code = rc
        return result

    # ── plan ─────────────────────────────────────────────────────────────

    def plan(self, seed_json: str, *, work_count: int | None = None) -> PipeResult:
        """Run ``plan-backlinks`` with the given JSONL seed data."""
        cmd = ["plan-backlinks"]
        if work_count is not None:
            cmd += ["--work-count", str(work_count)]
        return self._invoke(cmd, seed_json, "plan-backlinks failed")

    # ── validate ─────────────────────────────────────────────────────────

    def validate(
        self,
        plans_jsonl: str,
        *,
        no_check_urls: bool = True,
    ) -> PipeResult:
        """Run ``validate-backlinks`` with optional ``--no-check-urls``."""
        cmd = ["validate-backlinks"]
        if no_check_urls:
            cmd.append("--no-check-urls")
        return self._invoke(cmd, plans_jsonl, "validate-backlinks failed")

    # ── publish ──────────────────────────────────────────────────────────

    def publish(
        self,
        plans_jsonl: str,
        platform: str,
        mode: str,
    ) -> PipeResult:
        """Run ``publish-backlinks --platform <p> --mode <m>``."""
        cmd = ["publish-backlinks", "--platform", platform, "--mode", mode]
        return self._invoke(cmd, plans_jsonl, "publish-backlinks failed")

    def publish_seed(self, seed_jsonl: str) -> PipeResult:
        """Run bare ``publish-backlinks`` (platform/mode carried in the seed row).

        The queue processor (``scheduler._process_queue_job``) builds a self-
        describing seed where ``platform``/``publish_mode`` live in the payload,
        so it invokes the CLI with no flags. Capture-based so a partial-success
        exit still carries the published rows and the typed error/exit-code reach
        the 429-backoff branch.
        """
        return self._invoke_capture(
            ["publish-backlinks"], seed_jsonl, "publish-backlinks failed"
        )

    # ── resume ───────────────────────────────────────────────────────────

    def resume(self, run_id: str) -> PipeResult:
        """Run ``publish-backlinks --resume <run_id>`` for checkpoint recovery.

        Capture-based: ``checkpoint.py`` distinguishes exit 0 (full) / 4 (partial,
        rows still on stdout) / else (failed) via ``PipeResult.exit_code`` — so the
        exit code must survive and stdout must not be discarded on exit-4.
        """
        return self._invoke_capture(
            ["publish-backlinks", "--resume", run_id], "", "publish resume failed"
        )

    # ── report-anchors ─────────────────────────────────────────────────────

    def report_anchors(self, profile: str, *, as_json: bool = True) -> PipeResult:
        """Run ``report-anchors --from-profile <profile>`` (default ``--json``).

        Capture-based because ``report-anchors`` emits a single JSON/markdown
        **document** (not JSONL rows) and exits 6 when the anchor-distribution
        alarm fires — but the document is still on stdout. ``run_pipe`` would
        discard it; this keeps it, with the alarm surfaced as ``error_class``/
        ``exit_code`` (advisory) while ``stdout`` stays parseable. Read the
        document via ``result.stdout`` (``.rows`` cannot parse a single document).
        """
        cmd = ["report-anchors", "--from-profile", profile]
        if as_json:
            cmd.append("--json")
        return self._invoke_capture(cmd, "", "report-anchors failed")
