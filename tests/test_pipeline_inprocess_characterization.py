"""Subprocess golden baseline for the thin-WebUI in-process migration.

Phase 2 Unit 5 (plan ``2026-05-27-004``, audit
``docs/spike-notes/2026-05-27-inprocess-global-state-audit.md`` → "Characterization
corpus"). Three read-only CLIs — ``validate-backlinks``, ``plan-backlinks``,
``report-anchors`` — run TODAY as fresh subprocesses, so each call starts with
pristine process state. Unit 6/7 will run them **in-process** inside the
long-lived Flask process behind ``PipelineAPI``, where a request thread and the
``BackgroundScheduler`` thread share every module-level global the CLIs touch
(see audit hazards H1 ``set_log_level``, H2 ``content.fetch._STATS``,
H3 shared ``sys.stdout``/``sys.stderr``).

This module **locks the current subprocess-path behavior** so Unit 6 can assert
the in-process path produces identical results. Parity per R6 = typed
result/error class + exit code + byte-identical stdout *data* (banner-normalized
stderr). These tests are characterization-first: they describe what the code
does NOW, not what it should do. If one fails after a refactor, that is a real
behavior change Unit 6 must reconcile, not a test to "fix".

Why subprocesses (not the in-process ``_run`` helper used by the unit tests):
the autouse conftest fixtures (socket-block, URL-check-pass, content-fetch-pass,
config-sandbox) only patch IN-PROCESS pytest code. A subprocess we spawn does
NOT inherit them. So this test must keep itself network-free explicitly:

* ``validate-backlinks`` → ``--no-validate-url-check`` (the ``--no-check-urls``
  alias is deprecated and emits a WARN that pollutes stderr — use the new name).
* ``plan-backlinks`` → ``--no-fetch-verify`` (the *only* lever that flips
  ``fetch_verify_enabled`` in ``plan_backlinks/core.py:321``; the
  ``BACKLINK_NO_FETCH_VERIFY`` env var is read by ``config_echo`` for the banner
  but does NOT disable the gate by itself). We set the env var too, belt and
  suspenders.
* ``report-anchors`` stdin-aggregate / ``--from-profile`` (empty profile) need
  no network.

Every spawn also points ``BACKLINK_PUBLISHER_CONFIG_DIR`` at an isolated empty
tmp dir so the suite never reads the operator's real ``~/.config`` (which holds
live target domains) and never reaches a configured target URL.

The raw captured ``stdout``/``stderr``/``returncode`` are returned from
``_run_cli`` (and surfaced in the ``GOLDEN`` cases) so Unit 6 can byte-diff the
in-process output against these baselines. Assertions here pin *stable
structural* properties (exit code, row count, key fields, typed envelope
class/exit_code, document-vs-rows shape) rather than full stdout, which would be
brittle against timestamps and run-ids.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from backlink_publisher._util.error_envelope import parse

# Mirror webui_app/helpers/cli_runner.py:_rewrite_cli_cmd — run via the current
# interpreter + repo ``src/`` so we exercise THIS tree, not a stale editable
# install. _REPO_ROOT here is the worktree root (parent of tests/).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"

_MODULES = {
    "validate-backlinks": "backlink_publisher.cli.validate_backlinks",
    "plan-backlinks": "backlink_publisher.cli.plan_backlinks",
    "report-anchors": "backlink_publisher.cli.report_anchors",
}


@dataclass(frozen=True)
class CliResult:
    """Structured subprocess result. ``stdout``/``stderr`` kept raw for Unit 6 diffs."""

    stdout: str
    stderr: str
    returncode: int

    def jsonl_rows(self) -> list[dict]:
        """Parse stdout as JSONL data rows (skips blank lines). Raises on non-JSON."""
        return [
            json.loads(line)
            for line in self.stdout.splitlines()
            if line.strip()
        ]

    @property
    def envelope(self):
        """The typed-error envelope parsed from stderr, or None."""
        return parse(self.stderr)


def _run_cli(
    module_key: str,
    argv: list[str],
    stdin: str = "",
    extra_env: dict[str, str] | None = None,
) -> CliResult:
    """Spawn ``[python, -m, <module>, *argv]`` exactly like the WebUI cli_runner.

    Env always carries ``PYTHONPATH=<repo>/src`` + ``PYTHONHASHSEED=0`` (footprint
    determinism) and an isolated empty ``BACKLINK_PUBLISHER_CONFIG_DIR`` so the
    spawn never reads the operator's real config. ``cwd`` = repo root.
    """
    cfg_dir = extra_env.pop("__cfg_dir", None) if extra_env else None
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONHASHSEED"] = "0"
    if cfg_dir is not None:
        env["BACKLINK_PUBLISHER_CONFIG_DIR"] = cfg_dir
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-m", _MODULES[module_key], *argv],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
    )
    return CliResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)


# ---------------------------------------------------------------------------
# Input fixtures — grounded in tests/test_validate_backlinks.py and
# tests/test_plan_backlinks.py (NOT guessed). A validate payload short of the
# full ~6-link + seo shape exits 2 for the WRONG reason ("link count" / missing
# field), so the good-payload golden must carry the whole valid shape.
# ---------------------------------------------------------------------------


def _valid_validate_payload() -> dict:
    """Full valid validate-backlinks payload (6 links + seo block).

    Copied from tests/test_validate_backlinks.py:_make_valid_payload so the
    golden is anchored to the established valid shape, not a fresh guess.
    """
    return {
        "id": "abc123",
        "platform": "medium",
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": (
            "This is a test article about https://example.com and some content here."
        ),
        "links": [
            {"url": "https://example.com", "anchor": "Example",
             "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article",
             "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki",
             "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN",
             "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO",
             "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub",
             "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }


def _valid_plan_seed() -> dict:
    """Minimal valid plan-backlinks seed (from test_plan_backlinks.py)."""
    return {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Test Topic",
    }


@pytest.fixture
def cfg_dir(tmp_path) -> str:
    """Isolated empty config dir passed into every spawn via extra_env."""
    d = tmp_path / "cfg"
    d.mkdir()
    return str(d)


def _env(cfg_dir: str, **extra: str) -> dict[str, str]:
    return {"__cfg_dir": cfg_dir, **extra}


# ===========================================================================
# validate-backlinks
# ===========================================================================


def test_validate_good_payload_exit0_emits_row(cfg_dir):
    """(a) Good payload → exit 0, exactly one JSONL data row, no error envelope."""
    res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=json.dumps(_valid_validate_payload()) + "\n",
        extra_env=_env(cfg_dir),
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    rows = res.jsonl_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "abc123"
    assert row["validation"]["status"] == "passed"
    assert "checked_at" in row["validation"]
    # Success path emits the RECON reconciliation line but NO typed-error envelope.
    assert res.envelope is None
    assert "validate_reconciliation" in res.stderr


def test_validate_malformed_payload_typed_error_exit2(cfg_dir):
    """(b) Malformed payload → InputValidationError envelope, exit 2, empty stdout."""
    res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=json.dumps({"id": "r0", "platform": "linkedin"}) + "\n",
        extra_env=_env(cfg_dir),
    )
    assert res.returncode == 2
    env = res.envelope
    assert env is not None, f"expected typed envelope on stderr; got: {res.stderr!r}"
    assert env.error_class == "InputValidationError"
    assert env.exit_code == 2
    assert "validation failed" in env.message
    assert res.jsonl_rows() == []  # zero passing rows → no JSONL data


def test_validate_bad_config_fail_soft(tmp_path):
    """(c) Bad/empty config dir → validate FAIL-SOFT (audit surface 7).

    An empty BACKLINK_PUBLISHER_CONFIG_DIR (no config.toml) must NOT crash:
    validate swallows non-InputValidationError config-load failures, WARNs, and
    processes the row anyway. In-process this is the difference between a
    degraded Flask thread and a crashed one — Unit 6 must preserve it.
    """
    empty = tmp_path / "empty_cfg"
    empty.mkdir()  # exists but contains no config.toml
    res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=json.dumps(_valid_validate_payload()) + "\n",
        extra_env=_env(str(empty)),
    )
    # Fail-soft: still exit 0, still emits the validated row, no fatal envelope.
    assert res.returncode == 0, f"bad config should fail soft, not crash: {res.stderr}"
    rows = res.jsonl_rows()
    assert len(rows) == 1
    assert rows[0]["validation"]["status"] == "passed"
    assert res.envelope is None


# ---------------------------------------------------------------------------
# In-process parity (thin-WebUI Phase 2 Unit 6): PipelineAPI.validate now runs
# the engine in-process. Its data + typed-error MUST match the subprocess
# golden above. We compare on stable structural properties + parsed JSONL rows
# (not byte-identical stdout — the only differences are the run-invariant
# ``checked_at`` timestamps, which we normalize away).
# ---------------------------------------------------------------------------


def _normalize_validate_rows(rows: list[dict]) -> list[dict]:
    """Drop the run-variant ``validation.checked_at`` so two runs compare equal."""
    out = []
    for row in rows:
        row = json.loads(json.dumps(row))  # deep copy
        if isinstance(row.get("validation"), dict):
            row["validation"].pop("checked_at", None)
        out.append(row)
    return out


def test_validate_inprocess_matches_subprocess_good_payload(cfg_dir):
    """In-process validate good-payload rows == subprocess golden rows."""
    from webui_app.api.pipeline_api import PipelineAPI

    stdin = json.dumps(_valid_validate_payload()) + "\n"
    sub = _run_cli(
        "validate-backlinks", ["--no-validate-url-check"],
        stdin=stdin, extra_env=_env(cfg_dir),
    )
    assert sub.returncode == 0, f"stderr: {sub.stderr}"

    res = PipelineAPI().validate(stdin, no_check_urls=True)
    assert res.success is True
    assert res.exit_code == 0
    assert res.error is None
    assert _normalize_validate_rows(res.rows) == _normalize_validate_rows(
        sub.jsonl_rows()
    )


def test_validate_inprocess_matches_subprocess_malformed(cfg_dir):
    """In-process validate malformed-payload → same typed error/exit as golden."""
    from webui_app.api.pipeline_api import PipelineAPI

    stdin = json.dumps({"id": "r0", "platform": "linkedin"}) + "\n"
    sub = _run_cli(
        "validate-backlinks", ["--no-validate-url-check"],
        stdin=stdin, extra_env=_env(cfg_dir),
    )
    assert sub.returncode == 2
    sub_env = sub.envelope
    assert sub_env is not None

    res = PipelineAPI().validate(stdin, no_check_urls=True)
    assert res.success is False
    assert res.error_class == sub_env.error_class == "InputValidationError"
    assert res.exit_code == sub_env.exit_code == 2
    assert res.rows == []  # zero passing rows → no JSONL data, matching golden
    assert "validation failed" in (res.error or "")


# ===========================================================================
# plan-backlinks  (network-free via --no-fetch-verify)
# ===========================================================================


def test_plan_good_seed_exit0_emits_rows(cfg_dir):
    """(a) Good seed → exit 0, one planned payload row with required fields."""
    res = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(_valid_plan_seed()) + "\n",
        extra_env=_env(cfg_dir, BACKLINK_NO_FETCH_VERIFY="1"),
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    rows = res.jsonl_rows()
    assert len(rows) == 1
    row = rows[0]
    for field in ("id", "platform", "title", "content_markdown", "links"):
        assert field in row, f"plan row missing {field!r}"
    assert 5 <= len(row["links"]) <= 8
    assert res.envelope is None


def test_plan_error_seed_typed_error(cfg_dir):
    """(b) Unsupported-platform seed → InputValidationError envelope, exit 2."""
    bad = _valid_plan_seed()
    bad["platform"] = "xyznonexistent"
    res = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(bad) + "\n",
        extra_env=_env(cfg_dir, BACKLINK_NO_FETCH_VERIFY="1"),
    )
    assert res.returncode == 2
    env = res.envelope
    assert env is not None, f"expected typed envelope; got: {res.stderr!r}"
    assert env.error_class == "InputValidationError"
    assert env.exit_code == 2
    assert res.jsonl_rows() == []


# ===========================================================================
# report-anchors  — emits a DOCUMENT (markdown table / single JSON object),
# NOT JSONL rows. Assert document shape accordingly.
# ===========================================================================


def test_report_anchors_stdin_aggregate_markdown(cfg_dir):
    """Stdin-aggregate path (markdown): exit 0, NOTE-to-stderr, markdown document.

    Feed it a real plan-backlinks row so the report has something to aggregate.
    The structural property: NOTE warning on stderr (no alarm), stdout is a
    markdown table document — its first non-blank line is a ``| ... |`` header,
    NOT a JSON object — and there is no exit-6 alarm and no error envelope.
    """
    plan = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(_valid_plan_seed()) + "\n",
        extra_env=_env(cfg_dir, BACKLINK_NO_FETCH_VERIFY="1"),
    )
    assert plan.returncode == 0, f"plan seed failed: {plan.stderr}"

    res = _run_cli(
        "report-anchors", [], stdin=plan.stdout, extra_env=_env(cfg_dir)
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    # NOTE hint to stderr (the false-safety guard), no alarm, no envelope.
    assert "anchor distribution alarm requires --from-profile" in res.stderr
    assert res.envelope is None
    # Document, not JSONL: first non-blank stdout line is a markdown table row.
    first = next(l for l in res.stdout.splitlines() if l.strip())
    assert first.lstrip().startswith("|"), f"expected markdown table, got: {first!r}"
    with pytest.raises(json.JSONDecodeError):
        json.loads(first)  # confirm it is NOT a JSON row


def test_report_anchors_stdin_aggregate_json(cfg_dir):
    """Stdin-aggregate path (--json): stdout is ONE JSON document object, exit 0."""
    plan = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(_valid_plan_seed()) + "\n",
        extra_env=_env(cfg_dir, BACKLINK_NO_FETCH_VERIFY="1"),
    )
    assert plan.returncode == 0, f"plan seed failed: {plan.stderr}"

    res = _run_cli(
        "report-anchors", ["--json"], stdin=plan.stdout, extra_env=_env(cfg_dir)
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    doc = json.loads(res.stdout)  # ONE document, not line-delimited rows
    assert isinstance(doc, dict)
    # Per-target aggregate keyed by main_domain, plus the dofollow-tier block.
    assert "_dofollow_tiers" in doc
    assert any(k.startswith("https://example.com") for k in doc)
    assert res.envelope is None


def test_report_anchors_from_profile_empty_document(cfg_dir):
    """--from-profile (empty profile, no fixture): exit 0, profile-report document.

    With no profile JSON on disk the profile defaults to empty (0 entries) — so
    this path is exercisable WITHOUT a heavy fixture and stays network-free
    (no alarm breach → no exit-6). Captures the JSON document shape + that
    ``alarm.any_breach`` is False on an empty profile.

    NOTE for Unit 6: this is only the *empty-profile* from-profile golden. The
    audit's exit-6 ``AnchorDistributionAlarm`` branch (a profile whose 90d
    window breaches thresholds) needs a populated profile fixture and is left
    for Unit 6 to add as a second from-profile golden — see the deferred note in
    this module's report.
    """
    res = _run_cli(
        "report-anchors",
        ["--from-profile", "https://example.com", "--json"],
        stdin="",
        extra_env=_env(cfg_dir),
    )
    assert res.returncode == 0, f"stderr: {res.stderr}"
    doc = json.loads(res.stdout)
    assert isinstance(doc, dict)
    assert doc["main_domain"] == "https://example.com"
    assert doc["total_entries"] == 0
    assert "alarm" in doc
    assert doc["alarm"]["any_breach"] is False
    assert res.envelope is None  # no breach → no exit-6 alarm envelope


# ---------------------------------------------------------------------------
# In-process parity for plan + report-anchors (thin-WebUI Unit 7 closeout).
# `validate` already had its `inprocess_matches_subprocess` anchors above;
# `plan` and `report-anchors` were missing theirs — added by plan
# 2026-06-01-009 Unit 2 step 0 as the Unit-8 migration safety gate. Both
# subprocess calls here OMIT `__cfg_dir`, so they inherit the autouse
# sandbox `BACKLINK_PUBLISHER_CONFIG_DIR` from os.environ — i.e. the SAME
# config the in-process PipelineAPI call resolves. The only intentional
# asymmetry is fetch-verify: subprocess uses `--no-fetch-verify` (gate
# skipped), in-process plan() runs the gate but the autouse content-fetch
# fixture passes every URL, so no row is dropped either way.
# ---------------------------------------------------------------------------


def _normalize_plan_rows(rows: list[dict]) -> list[dict]:
    """Drop run-variant fields so two plan runs compare equal.

    ``run_id`` and any ``*_at`` timestamp vary per run; the planned-payload
    identity (``id``) is content-derived and stable under ``PYTHONHASHSEED=0``,
    so it is NOT dropped — a drift there would be a real behavior change.
    """
    out = []
    for row in rows:
        row = json.loads(json.dumps(row))  # deep copy
        row.pop("run_id", None)
        for k in list(row):
            if k.endswith("_at"):
                row.pop(k, None)
        out.append(row)
    return out


def test_plan_inprocess_matches_subprocess_good_seed():
    """In-process plan good-seed rows == subprocess golden (same sandbox config)."""
    from webui_app.api.pipeline_api import PipelineAPI

    stdin = json.dumps(_valid_plan_seed()) + "\n"
    # No __cfg_dir → subprocess inherits the autouse sandbox config dir, matching
    # the in-process call. BACKLINK_NO_FETCH_VERIFY=1 + --no-fetch-verify skip the
    # network gate on the subprocess side.
    sub = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=stdin,
        extra_env={"BACKLINK_NO_FETCH_VERIFY": "1"},
    )
    assert sub.returncode == 0, f"subprocess plan failed: {sub.stderr}"

    res = PipelineAPI().plan(stdin)
    assert res.success is True, f"in-process plan failed: {res.error}"
    assert res.exit_code == 0
    assert _normalize_plan_rows(res.rows) == _normalize_plan_rows(sub.jsonl_rows())


def test_plan_inprocess_matches_subprocess_error_seed():
    """In-process plan unsupported-platform → same typed error/exit as golden."""
    from webui_app.api.pipeline_api import PipelineAPI

    bad = _valid_plan_seed()
    bad["platform"] = "xyznonexistent"
    stdin = json.dumps(bad) + "\n"
    sub = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=stdin,
        extra_env={"BACKLINK_NO_FETCH_VERIFY": "1"},
    )
    assert sub.returncode == 2
    sub_env = sub.envelope
    assert sub_env is not None

    res = PipelineAPI().plan(stdin)
    assert res.success is False
    assert res.error_class == sub_env.error_class == "InputValidationError"
    assert res.exit_code == sub_env.exit_code == 2


def test_report_anchors_inprocess_matches_subprocess_from_profile_empty():
    """In-process report_anchors(--from-profile, empty) == subprocess golden document."""
    from webui_app.api.pipeline_api import PipelineAPI

    # No __cfg_dir → subprocess inherits the autouse sandbox config (empty profile),
    # matching the in-process PipelineAPI().report_anchors() config resolution.
    sub = _run_cli(
        "report-anchors",
        ["--from-profile", "https://example.com", "--json"],
        stdin="",
    )
    assert sub.returncode == 0, f"subprocess report failed: {sub.stderr}"

    res = PipelineAPI().report_anchors("https://example.com", as_json=True)
    assert res.success is True, f"in-process report failed: {res.error}"
    assert res.exit_code == 0
    # report-anchors emits a single JSON document, not JSONL rows — compare the
    # parsed documents (read via .stdout, per the report_anchors docstring).
    assert json.loads(res.stdout) == json.loads(sub.stdout)


# ===========================================================================
# Concurrency baseline — pins audit hazard H3 (shared sys.stdout/stderr).
# ===========================================================================


def test_concurrent_validate_outputs_non_interleaved(cfg_dir):
    """Two concurrent validate subprocesses each produce well-formed, isolated output.

    TODAY each subprocess owns its own process stdout, so outputs cannot
    interleave. Unit 6 moves these in-process onto a SHARED ``sys.stdout`` in a
    single Flask process — the in-process harness MUST give each PipelineAPI
    call its own captured buffer to preserve this property (audit H3). This test
    is the baseline that property is asserted against: each result is
    independently parseable JSONL with the right row identity, no cross-talk.
    """
    payload_a = _valid_validate_payload()
    payload_a["id"] = "concurrent-a"
    payload_b = _valid_validate_payload()
    payload_b["id"] = "concurrent-b"

    def run(payload: dict) -> CliResult:
        return _run_cli(
            "validate-backlinks",
            ["--no-validate-url-check"],
            stdin=json.dumps(payload) + "\n",
            extra_env=_env(cfg_dir),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(run, payload_a)
        fut_b = pool.submit(run, payload_b)
        res_a, res_b = fut_a.result(), fut_b.result()

    for res, expected_id in ((res_a, "concurrent-a"), (res_b, "concurrent-b")):
        assert res.returncode == 0, f"stderr: {res.stderr}"
        rows = res.jsonl_rows()  # non-interleaved → cleanly parseable
        assert len(rows) == 1
        assert rows[0]["id"] == expected_id  # no cross-talk between the two runs
        assert rows[0]["validation"]["status"] == "passed"
