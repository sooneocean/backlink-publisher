"""Phase 2 Unit 4 — pipeline-CLI invocation funnel + capture-based methods.

Every WebUI invocation of a pipeline CLI funnels through ``PipelineAPI``
(``webui_app/api/pipeline_api.py``), the single consumer of ``run_pipe`` /
``run_pipe_capture``. No route, service, or scheduler job may call them — or
raw ``subprocess`` — directly for a pipeline CLI. The login/bind services keep
their own ``subprocess.Popen`` (R5: login CLIs stay subprocess) and are out of
scope for the funnel.

The first two tests are static AST guards: they catch a regression the moment
someone re-introduces a raw ``run_pipe`` / ``subprocess.run`` into a route or
service. The rest exercise the new capture-based methods that preserve stdout
and exit-code on non-zero exits (``report-anchors`` exit-6, publish exit-4).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

from backlink_publisher._util.error_envelope import ErrorEnvelope
from webui_app.api.pipeline_api import PipelineAPI

_WEBUI = Path(__file__).resolve().parents[1] / "webui_app"

# run_pipe / run_pipe_capture: cli_runner defines them, pipeline_api is the sole
# consumer. Nothing else may call them.
_RUN_PIPE_ALLOWED = {"helpers/cli_runner.py", "api/pipeline_api.py"}

# subprocess.run / .Popen: cli_runner implements run_pipe_capture; the login &
# bind services spawn their own (browser-login) processes — R5 out-of-scope.
_SUBPROCESS_ALLOWED = {
    "helpers/cli_runner.py",
    "services/browser_login.py",
    "services/bind_job.py",
}

_RUN_PIPE_NAMES = {"run_pipe", "run_pipe_capture"}
_SUBPROCESS_ATTRS = {"subprocess.run", "subprocess.Popen"}


def _rel(path: Path) -> str:
    return path.relative_to(_WEBUI).as_posix()


def _call_quals(tree: ast.AST):
    """Yield ('name'|'attr', qualname) for every function-call node."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            yield "name", func.id
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            yield "attr", f"{func.value.id}.{func.attr}"


# ── static funnel guards ────────────────────────────────────────────────────


def test_run_pipe_only_called_through_pipeline_api():
    offenders = []
    for path in sorted(_WEBUI.rglob("*.py")):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, qual in _call_quals(tree):
            if kind == "name" and qual in _RUN_PIPE_NAMES and rel not in _RUN_PIPE_ALLOWED:
                offenders.append(f"{rel}: calls {qual}()")
    assert not offenders, (
        "run_pipe/run_pipe_capture must be called only through PipelineAPI:\n"
        + "\n".join(offenders)
    )


def test_subprocess_not_used_for_pipeline_clis():
    offenders = []
    for path in sorted(_WEBUI.rglob("*.py")):
        rel = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for kind, qual in _call_quals(tree):
            if kind == "attr" and qual in _SUBPROCESS_ATTRS and rel not in _SUBPROCESS_ALLOWED:
                offenders.append(f"{rel}: calls {qual}()")
    assert not offenders, (
        "Raw subprocess for a pipeline CLI is forbidden outside cli_runner / "
        "login services — funnel through PipelineAPI:\n" + "\n".join(offenders)
    )


# ── capture-based methods preserve stdout + exit-code ───────────────────────


def _captured(stdout: str, stderr: str, returncode: int) -> dict:
    return {"stdout": stdout, "stderr": stderr, "returncode": returncode}


def test_report_anchors_retains_stdout_on_exit6_alarm():
    # exit-6 = anchor-distribution alarm, but the JSON document is still on
    # stdout. run_pipe would discard it by raising; report_anchors must keep it.
    envelope = ErrorEnvelope("AnchorDistributionAlarm", 6, "anchor alarm").serialize()
    captured = _captured('{"main_domain":"x","total_entries":3}', envelope + "\n", 6)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe_capture", return_value=captured
    ):
        result = PipelineAPI().report_anchors("example.com")
    assert result.stdout == '{"main_domain":"x","total_entries":3}'  # NOT discarded
    assert result.exit_code == 6
    assert result.error_class == "AnchorDistributionAlarm"
    assert result.success is False


def test_report_anchors_success_path():
    captured = _captured('{"main_domain":"x"}', "", 0)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe_capture", return_value=captured
    ):
        result = PipelineAPI().report_anchors("example.com")
    assert result.success is True
    assert result.exit_code == 0
    assert result.stdout == '{"main_domain":"x"}'


def test_resume_carries_exit_code_for_checkpoint_branching():
    # checkpoint.py branches 0 / 4 / else off exit_code — it must survive.
    for rc in (0, 4, 7):
        captured = _captured('{"published_url":"u"}', "", rc)
        with mock.patch(
            "webui_app.api.pipeline_api.run_pipe_capture", return_value=captured
        ):
            result = PipelineAPI().resume("20260101T000000-abcd")
        assert result.exit_code == rc, f"exit {rc} not carried"
        # exit-4 (partial) keeps the published rows on stdout
        if rc == 4:
            assert result.stdout == '{"published_url":"u"}'
            assert result.success is False


def test_invoke_capture_flags_silent_failure_on_nonempty_stdin():
    # exit 0 + empty stdout/stderr on non-empty stdin = broken entry-point.
    captured = _captured("", "", 0)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe_capture", return_value=captured
    ):
        result = PipelineAPI().publish_seed('[{"target_url":"https://x/y"}]')
    assert result.success is False
    assert "no output" in (result.error or "")


def test_publish_seed_success():
    captured = _captured('{"published_url":"https://m/p"}', "", 0)
    with mock.patch(
        "webui_app.api.pipeline_api.run_pipe_capture", return_value=captured
    ):
        result = PipelineAPI().publish_seed('[{"target_url":"https://x/y"}]')
    assert result.success is True
    assert result.rows[0]["published_url"] == "https://m/p"


def test_plan_work_count_flag_passed_through():
    seen = {}

    def _fake_run_pipe(cmd, stdin):
        seen["cmd"] = cmd
        return {"stdout": "{}", "stderr": ""}

    with mock.patch("webui_app.api.pipeline_api.run_pipe", side_effect=_fake_run_pipe):
        PipelineAPI().plan("{}", work_count=10)
    assert seen["cmd"] == ["plan-backlinks", "--work-count", "10"]
