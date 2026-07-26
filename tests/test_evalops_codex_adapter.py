from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PureWindowsPath

import pytest

from tests.evalops_adapter_contract import assert_adapter_contract
from rook_agent.agent.cancellation import CancellationToken
from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters import AgentAdapter
from rook_agent.evalops.adapters.codex_cli import CodexCliAdapter, _command
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    CandidateOrigin,
    CandidateStatus,
    CaseCategory,
    EvalCase,
    EvaluatorSpec,
    NetworkPolicy,
    RunSpec,
    RunStatus,
    SkillBundle,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
    plain_data,
)
from rook_agent.evalops.process import (
    ProcessRequest,
    ProcessResult,
    ProcessStatus,
)
from rook_agent.evalops.skills import SkillMaterializer, render_skill


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "evalops" / "codex"
CODEX_HELP = """
Usage: codex exec [OPTIONS] [PROMPT]
  --json
  --ephemeral
  --ignore-user-config
  --ignore-rules
"""


def _process_result(
    *,
    status: ProcessStatus = ProcessStatus.SUCCEEDED,
    exit_code: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    cleanup_error: str | None = None,
    duration_ms: int = 25,
) -> ProcessResult:
    return ProcessResult(
        status=status,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        error_message=None,
        cleanup_error=cleanup_error,
    )


class ScriptedProcessRunner:
    def __init__(
        self,
        *,
        exec_result: ProcessResult | None = None,
        version_result: ProcessResult | None = None,
        help_result: ProcessResult | None = None,
        config_result: ProcessResult | None = None,
    ) -> None:
        self.exec_result = exec_result or _process_result(
            stdout=(FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8")
        )
        self.version_result = version_result or _process_result(
            stdout="codex-cli 0.144.1\n"
        )
        self.help_result = help_result or _process_result(stdout=CODEX_HELP)
        self.config_result = config_result or _process_result(stdout="features\n")
        self.requests: list[ProcessRequest] = []
        self.tokens: list[CancellationToken | None] = []

    def run(
        self,
        request: ProcessRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ProcessResult:
        self.requests.append(request)
        self.tokens.append(cancellation_token)
        if cancellation_token is not None and cancellation_token.is_cancelled:
            return _process_result(
                status=ProcessStatus.CANCELLED,
                exit_code=None,
            )
        if request.command[-1:] == ("--version",):
            return self.version_result
        if request.command[-2:] == ("exec", "--help"):
            return self.help_result
        if request.command[-2:] == ("features", "list"):
            return self.config_result
        return self.exec_result


def _target() -> AgentTarget:
    return AgentTarget(
        type=AgentType.CODEX,
        executable="codex",
        version="0.144.1",
        model="gpt-test",
        adapter_version="1",
    )


def _candidate() -> SkillCandidate:
    bundle = SkillBundle(
        name="safe-shell",
        description="Use safe shell verification.",
        triggers=("shell task",),
        procedure=("Inspect before editing.",),
        verification=("Run focused tests.",),
        pitfalls=("Do not hide failures.",),
        evidence_refs=(),
    )
    content_hash = hashlib.sha256(render_skill(bundle).encode("utf-8")).hexdigest()
    return SkillCandidate(
        bundle=bundle,
        version=1,
        content_hash=content_hash,
        origin=CandidateOrigin.MANUAL,
        status=CandidateStatus.CANDIDATE,
    )


def _spec(
    tmp_path: Path,
    *,
    treatment: Treatment = Treatment.BASELINE,
    treatment_family: TreatmentFamily | None = None,
    environment: Mapping[str, str] | None = None,
) -> RunSpec:
    return RunSpec(
        experiment_id="experiment-codex",
        pair_id="pair-codex",
        target=_target(),
        case=EvalCase(
            id="direct-codex",
            category=CaseCategory.DIRECT,
            task="Create result.txt and verify it.",
            fixture=tmp_path / "fixture",
            evaluator=EvaluatorSpec(kind="command", options={"command": ("verify",)}),
            timeout_seconds=30,
            network_policy=NetworkPolicy.DISABLED,
        ),
        treatment=treatment,
        workspace_snapshot_hash="snapshot-hash",
        skill=_candidate() if treatment is not Treatment.BASELINE else None,
        timeout_seconds=30,
        turn_limit=5,
        budget_limit=Decimal("1.00"),
        environment_allowlist=dict(environment or {}),
        permission_profile="isolated",
        treatment_family=treatment_family,
    )


def _adapter(
    tmp_path: Path,
    runner: ScriptedProcessRunner,
    *,
    help_path: str | None = r"C:\Tools\codex.exe",
    host_environment: Mapping[str, str] | None = None,
    platform_name: str = "win32",
) -> CodexCliAdapter:
    return CodexCliAdapter(
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        process_runner=runner,
        executable="codex",
        which=lambda _: help_path,
        platform_name=platform_name,
        host_environment=dict(
            host_environment
            or {
                "PATH": r"C:\Windows\System32",
                "SystemRoot": r"C:\Windows",
                "TEMP": str(tmp_path / "temp"),
                "UNRELATED_SECRET": "must-not-inherit",
            }
        ),
    )


def test_codex_adapter_satisfies_reusable_agent_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)

    assert isinstance(adapter, AgentAdapter)
    run = assert_adapter_contract(
        adapter,
        _spec(tmp_path),
        workspace,
        artifact_root=tmp_path / "artifacts",
        guard_root=tmp_path,
        expected_status=RunStatus.PASSED,
        expected_trace_complete=True,
    )

    assert run.final_answer == "All tests pass."
    assert run.input_tokens == 100
    assert run.output_tokens == 30
    assert run.cost_usd is None


def test_codex_probe_reports_supported_noninteractive_json_mode(tmp_path: Path) -> None:
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)

    capabilities = adapter.probe()

    assert capabilities.available is True
    assert capabilities.executable_path == r"C:\Tools\codex.exe"
    assert capabilities.version == "codex-cli 0.144.1"
    assert capabilities.non_interactive is True
    assert capabilities.structured_events is True
    assert capabilities.supports_timeout is True
    assert capabilities.supports_turn_limit is False
    assert capabilities.supports_budget_limit is False
    assert capabilities.supports_sandbox is True
    assert capabilities.supported_treatments == tuple(Treatment)
    assert capabilities.diagnostic_code is None
    assert runner.requests[0].command == (r"C:\Tools\codex.exe", "--version")
    assert runner.requests[1].command == (
        r"C:\Tools\codex.exe",
        "exec",
        "--help",
    )
    config_command = runner.requests[2].command
    assert config_command[-2:] == ("features", "list")
    assert "allow_login_shell=false" in config_command
    assert "permissions.allow_login_shell=false" not in config_command


def test_codex_probe_fails_closed_when_eval_config_does_not_load(
    tmp_path: Path,
) -> None:
    runner = ScriptedProcessRunner(
        config_result=_process_result(
            status=ProcessStatus.FAILED,
            exit_code=1,
            stderr="Error loading config.toml",
        )
    )
    adapter = _adapter(tmp_path, runner)

    capabilities = adapter.probe()

    assert capabilities.available is True
    assert capabilities.non_interactive is False
    assert capabilities.structured_events is False
    assert capabilities.supported_treatments == ()
    assert capabilities.diagnostic_code == RunStatus.VERSION_UNSUPPORTED.value


def test_codex_probe_fails_closed_for_missing_executable(tmp_path: Path) -> None:
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner, help_path=None)

    capabilities = adapter.probe()

    assert capabilities.available is False
    assert capabilities.diagnostic_code == RunStatus.ADAPTER_UNAVAILABLE.value
    assert runner.requests == []


def test_codex_probe_marks_required_flag_gap_version_unsupported(
    tmp_path: Path,
) -> None:
    runner = ScriptedProcessRunner(
        help_result=_process_result(
            stdout="--json --ephemeral --ignore-user-config"
        )
    )
    adapter = _adapter(tmp_path, runner)

    capabilities = adapter.probe()

    assert capabilities.available is True
    assert capabilities.non_interactive is False
    assert capabilities.structured_events is False
    assert capabilities.supported_treatments == ()
    assert capabilities.diagnostic_code == RunStatus.VERSION_UNSUPPORTED.value


def test_codex_prepare_builds_safe_exact_exec_command_and_stdin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)

    prepared = adapter.prepare(_spec(tmp_path), workspace)

    assert prepared.command == (
        r"C:\Tools\codex.exe",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "plugins",
        "--disable",
        "memories",
        "-c",
        'model_provider="rook-chatgpt-http"',
        "-c",
        'model_providers.rook-chatgpt-http.name="Rook ChatGPT HTTP"',
        "-c",
        'model_providers.rook-chatgpt-http.base_url="https://chatgpt.com/backend-api/codex"',
        "-c",
        'model_providers.rook-chatgpt-http.wire_api="responses"',
        "-c",
        "model_providers.rook-chatgpt-http.requires_openai_auth=true",
        "-c",
        "model_providers.rook-chatgpt-http.supports_websockets=false",
        "-c",
        'web_search="disabled"',
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        "allow_login_shell=false",
        "-c",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "-C",
        workspace.resolve().as_posix(),
        "-c",
        'windows.sandbox="unelevated"',
        "-c",
        'approval_policy="never"',
        "--model",
        "gpt-test",
        "-",
    )
    assert prepared.stdin_text == (
        "Execution constraints for this Windows workspace:\n"
        "- Use shell commands for file changes; do not call apply_patch.\n"
        "- Treat the current directory as the complete isolated workspace; do not "
        "run git or search parent directories.\n"
        "- Do not set or override the shell tool working directory. It already "
        "starts in the isolated workspace; use relative paths with forward slashes "
        "inside tool arguments.\n"
        "- Treat language-mode, profile-loading, and method-invocation errors as "
        "restricted PowerShell failures.\n"
        "- After 2 consecutive restricted PowerShell failures, do not try another "
        "PowerShell variant; switch once to cmd.exe /d /s /c, a direct executable "
        "such as py, or a dedicated non-shell tool when available.\n"
        "- Use the fallback to perform the task directly, not for capability probes "
        "or checks of outputs that were never created.\n"
        "- Keep the required mutation separate from auxiliary verification. Do not "
        "append readback assertions, source-equality checks, file inventories, tests, "
        "or other verification to the fallback mutation command.\n"
        "- A direct py -c fallback must be one physical line with shell-safe "
        "statements. Do not pass multiline source or escaped newline sequences to "
        "py -c, and do not use a PowerShell here-string to feed it.\n"
        "- Report ROOK_SHELL_FALLBACK_EXHAUSTED only when the fallback fails before "
        "completing the required mutation.\n"
        "- If the requested output was written but an auxiliary verification fails, "
        "stop issuing shell commands and report "
        "ROOK_POST_WRITE_VERIFICATION_INCONCLUSIVE: <short reason>. The external "
        "evaluator will determine correctness.\n"
        "- Finish with a best-effort result within the task time limit.\n\n"
        "Create result.txt and verify it."
    )
    assert "--dangerously-bypass-approvals-and-sandbox" not in prepared.command
    assert prepared.metadata["environment_keys"] == tuple(sorted(prepared.environment))
    assert "must-not-inherit" not in repr(prepared.metadata)


def test_codex_command_disables_login_shell_profiles_for_every_platform(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-no-login-profile"
    workspace.mkdir()

    windows = _adapter(
        tmp_path,
        ScriptedProcessRunner(),
        platform_name="win32",
    ).prepare(_spec(tmp_path), workspace)
    posix = _adapter(
        tmp_path,
        ScriptedProcessRunner(),
        platform_name="linux",
    ).prepare(_spec(tmp_path), workspace)

    assert "allow_login_shell=false" in windows.command
    assert "allow_login_shell=false" in posix.command
    assert "permissions.allow_login_shell=false" not in windows.command
    assert "permissions.allow_login_shell=false" not in posix.command


def test_codex_command_forces_http_transport_without_changing_auth_endpoint(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-http-only"
    workspace.mkdir()
    prepared = _adapter(tmp_path, ScriptedProcessRunner()).prepare(
        _spec(tmp_path), workspace
    )

    assert 'model_provider="rook-chatgpt-http"' in prepared.command
    assert (
        'model_providers.rook-chatgpt-http.base_url="https://chatgpt.com/backend-api/codex"'
        in prepared.command
    )
    assert (
        "model_providers.rook-chatgpt-http.requires_openai_auth=true"
        in prepared.command
    )
    assert (
        "model_providers.rook-chatgpt-http.supports_websockets=false"
        in prepared.command
    )


def test_codex_windows_command_uses_slash_normalized_workspace_argument() -> None:
    workspace = PureWindowsPath(
        r"C:\Users\runner\AppData\Local\Temp\rook-evalops\base\baseline"
    )

    command = _command(
        r"C:\Tools\codex.exe",
        workspace=workspace,
        model="gpt-test",
        include_skill_instructions=False,
        windows_sandbox="unelevated",
        network_policy=NetworkPolicy.DISABLED,
    )

    cwd_index = command.index("-C") + 1
    assert command[cwd_index] == workspace.as_posix()
    assert "\\b" not in command[cwd_index]


def test_codex_prepare_windows_keeps_os_temp_without_synthesizing_nested_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-temp"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(
        tmp_path,
        runner,
        host_environment={
            "PATH": r"C:\\Windows\\System32",
            "SystemRoot": r"C:\\Windows",
            "TEMP": r"C:\\host-temp",
            "tmp": r"C:\\host-tmp",
            "TMPDIR": r"C:\\host-tmpdir",
        },
    )

    prepared = adapter.prepare(_spec(tmp_path), workspace)

    assert prepared.environment["TEMP"] == r"C:\\host-temp"
    assert prepared.environment["tmp"] == r"C:\\host-tmp"
    assert prepared.environment["TMPDIR"] == r"C:\\host-tmpdir"
    assert "TMP" not in prepared.environment
    assert not (workspace / ".rook" / "evalops-temp").exists()


def test_codex_prepare_windows_does_not_add_missing_temp_aliases(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-one-temp"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(),
        host_environment={
            "PATH": r"C:\\Windows\\System32",
            "SystemRoot": r"C:\\Windows",
            "TEMP": r"C:\\host-temp",
        },
    )

    prepared = adapter.prepare(_spec(tmp_path), workspace)

    assert prepared.environment["TEMP"] == r"C:\\host-temp"
    assert "TMP" not in prepared.environment
    assert "TMPDIR" not in prepared.environment


def test_codex_prepare_keeps_host_temp_on_non_windows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-linux-temp"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(
        tmp_path,
        runner,
        platform_name="linux",
        host_environment={
            "PATH": "/usr/bin",
            "TEMP": "/host-temp",
            "TMP": "/host-tmp",
            "TMPDIR": "/host-tmpdir",
        },
    )

    prepared = adapter.prepare(_spec(tmp_path), workspace)

    assert prepared.environment["TEMP"] == "/host-temp"
    assert prepared.environment["TMP"] == "/host-tmp"
    assert prepared.environment["TMPDIR"] == "/host-tmpdir"
    assert not (workspace / ".rook" / "evalops-temp").exists()


def test_codex_disabled_network_web_search_is_a_policy_violation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-policy"
    workspace.mkdir()
    lines = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").splitlines()
    lines.insert(
        -1,
        '{"type":"item.started","item":{"id":"item_6","type":"web_search",'
        '"id":"ws_duplicate","query":"unexpected"}}',
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(stdout="\n".join(lines) + "\n")
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.UNSAFE_ACTION
    assert run.error_code == "codex_web_search_policy_violation"
    assert run.trace_complete is False
    assert run.trace is not None
    assert "codex_web_search_policy_violation" in run.trace.diagnostics
    persisted = (tmp_path / "artifacts" / run.raw_event_refs[0]).read_text(
        encoding="utf-8"
    )
    assert "unexpected" not in persisted


def test_codex_windows_sandbox_error_fails_closed_after_successful_process(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-sandbox-error"
    workspace.mkdir()
    stderr = (
        "windows sandbox: CreateProcessAsUserW failed: 267 "
        "(directory name is invalid)"
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(exec_result=_process_result(stderr=stderr)),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_windows_sandbox_error"


def test_codex_windows_tool_cwd_escape_has_specific_error_code(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-cwd-escape"
    workspace.mkdir()
    stderr = (
        r"windows sandbox: CreateProcessAsUserW failed: 267 "
        r"(directory name is invalid) | cwd=C:\work\pair\u{8}aseline"
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(exec_result=_process_result(stderr=stderr)),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_windows_tool_cwd_escape_error"
    assert run.error_message == (
        "Codex supplied an escaped Windows tool working directory that the sandbox "
        "could not start."
    )


def test_codex_timeout_reports_restricted_shell_retry_exhaustion(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-restricted-shell-timeout"
    workspace.mkdir()
    events = (
        {"type": "thread.started", "thread_id": "thread-shell-timeout"},
        {"type": "turn.started"},
        {
            "type": "item.started",
            "item": {
                "id": "item-shell-1",
                "type": "command_execution",
                "command": "pwsh -Command first",
                "status": "in_progress",
                "aggregated_output": "",
                "exit_code": None,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item-shell-1",
                "type": "command_execution",
                "command": "pwsh -Command first",
                "status": "failed",
                "aggregated_output": (
                    "Cannot dot-source this command because it was defined in a "
                    "different language mode."
                ),
                "exit_code": 1,
            },
        },
        {
            "type": "item.started",
            "item": {
                "id": "item-shell-2",
                "type": "command_execution",
                "command": "pwsh -Command second",
                "status": "in_progress",
                "aggregated_output": "",
                "exit_code": None,
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "item-shell-2",
                "type": "command_execution",
                "command": "pwsh -Command second",
                "status": "failed",
                "aggregated_output": (
                    "Method invocation is supported only on core types in this "
                    "language mode."
                ),
                "exit_code": 1,
            },
        },
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(
                status=ProcessStatus.TIMEOUT,
                exit_code=1,
                stdout="\n".join(json.dumps(event) for event in events) + "\n",
            )
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.TIMEOUT
    assert run.error_code == "codex_restricted_shell_timeout"
    assert run.error_message == (
        "Codex reached the restricted PowerShell recovery limit before the run "
        "completed."
    )
    assert run.trace is not None
    assert "codex_restricted_shell_failure_limit_reached" in run.trace.diagnostics


def test_codex_timeout_deadline_overrun_is_infrastructure_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-timeout-overrun"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(
                status=ProcessStatus.TIMEOUT,
                exit_code=1,
                cleanup_error="timeout_deadline_overrun",
                duration_ms=18_983_156,
            )
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_timeout_deadline_overrun"
    assert run.error_message == (
        "The host was suspended or the scheduler could not enforce the Codex "
        "deadline."
    )


def test_codex_completed_fallback_exhaustion_is_an_adapter_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-shell-fallback-exhausted"
    workspace.mkdir()
    lines = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        event = json.loads(line)
        if (
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ):
            event["item"]["text"] = (
                "ROOK_SHELL_FALLBACK_EXHAUSTED: restricted shell unavailable"
            )
            lines[index] = json.dumps(event)
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(stdout="\n".join(lines) + "\n")
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.ADAPTER_ERROR
    assert run.error_code == "codex_shell_fallback_exhausted"
    assert run.error_message == (
        "Codex could not recover from the restricted PowerShell environment."
    )


def test_codex_post_write_verification_inconclusive_reaches_evaluator_boundary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-post-write-verification"
    workspace.mkdir()
    lines = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        event = json.loads(line)
        if (
            event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ):
            event["item"]["text"] = (
                "ROOK_POST_WRITE_VERIFICATION_INCONCLUSIVE: result.txt was written "
                "before an auxiliary assertion failed"
            )
            lines[index] = json.dumps(event)
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(stdout="\n".join(lines) + "\n")
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.PASSED
    assert run.error_code is None
    assert run.trace is not None
    assert "codex_post_write_verification_inconclusive" in run.trace.diagnostics
    assert "codex_shell_fallback_exhausted" not in run.trace.diagnostics


def test_codex_recovered_stream_error_keeps_successful_terminal_result(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace-recovered-stream"
    workspace.mkdir()
    lines = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").splitlines()
    lines.insert(-1, '{"type":"error","message":"Reconnecting... 2/5 (request timed out)"}')
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(stdout="\n".join(lines) + "\n")
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.PASSED
    assert run.error_code is None
    assert run.trace is not None
    assert run.trace.trace_complete is True
    assert "codex_stream_error" in run.trace.diagnostics


def test_codex_prepare_does_not_set_windows_backend_on_linux(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-linux"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(),
        platform_name="linux",
    )

    prepared = adapter.prepare(_spec(tmp_path), workspace)

    assert 'windows.sandbox="unelevated"' not in prepared.command
    assert (
        "sandbox_workspace_write.exclude_tmpdir_env_var=true"
        not in prepared.command
    )
    assert prepared.stdin_text == "Create result.txt and verify it."


def test_codex_content_pair_hides_unrelated_skill_catalog(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-content"
    workspace.mkdir()
    adapter = _adapter(tmp_path, ScriptedProcessRunner())
    spec = _spec(
        tmp_path,
        treatment=Treatment.FORCED_SKILL,
        treatment_family=TreatmentFamily.CONTENT,
    )
    staged = SkillMaterializer().materialize(spec.skill, AgentType.CODEX, workspace)

    prepared = adapter.prepare(spec, workspace, staged_skill=staged)

    assert "skills.include_instructions=false" in prepared.command
    assert "do not search for other repository guidance" in prepared.stdin_text
    assert "make a direct best-effort attempt" in prepared.stdin_text
    assert "Stop after creating and verifying" in prepared.stdin_text


def test_codex_content_guidance_is_platform_independent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-content-linux"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(),
        platform_name="linux",
    )
    spec = _spec(tmp_path, treatment_family=TreatmentFamily.CONTENT)

    prepared = adapter.prepare(spec, workspace)

    assert "do not search for other repository guidance" in prepared.stdin_text
    assert "make a direct best-effort attempt" in prepared.stdin_text
    assert "Stop after creating and verifying" in prepared.stdin_text


def test_codex_routing_pair_keeps_skill_discovery_enabled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace-routing"
    workspace.mkdir()
    adapter = _adapter(tmp_path, ScriptedProcessRunner())
    spec = _spec(
        tmp_path,
        treatment=Treatment.ROUTED_SKILL,
        treatment_family=TreatmentFamily.ROUTING,
    )
    staged = SkillMaterializer().materialize(spec.skill, AgentType.CODEX, workspace)

    prepared = adapter.prepare(spec, workspace, staged_skill=staged)

    assert "skills.include_instructions=false" not in prepared.command
    assert "do not search for other repository guidance" not in prepared.stdin_text


def test_codex_adapter_isolates_baseline_forced_and_routed_prompts(
    tmp_path: Path,
) -> None:
    materializer = SkillMaterializer()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)
    prepared_runs = []

    for treatment in Treatment:
        workspace = tmp_path / treatment.value
        workspace.mkdir()
        spec = _spec(tmp_path, treatment=treatment)
        staged = None
        if treatment is not Treatment.BASELINE:
            assert spec.skill is not None
            staged = materializer.materialize(spec.skill, AgentType.CODEX, workspace)
        prepared_runs.append(adapter.prepare(spec, workspace, staged_skill=staged))

    baseline, forced, routed = prepared_runs
    relative_skill = ".agents/skills/safe-shell/SKILL.md"
    assert relative_skill not in baseline.stdin_text
    assert not (baseline.workspace / relative_skill).exists()
    assert forced.stdin_text.count(relative_skill) == 1
    assert (forced.workspace / relative_skill).is_file()
    assert relative_skill not in routed.stdin_text
    assert (routed.workspace / relative_skill).is_file()
    for prepared in prepared_runs:
        assert str(prepared.workspace) not in prepared.stdin_text


def test_codex_prepare_rejects_invalid_skill_staging(tmp_path: Path) -> None:
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)
    baseline_workspace = tmp_path / "baseline"
    forced_workspace = tmp_path / "forced"
    outside = tmp_path / "outside" / "SKILL.md"
    for path in (baseline_workspace, forced_workspace, outside.parent):
        path.mkdir()
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ValueError, match="baseline"):
        adapter.prepare(_spec(tmp_path), baseline_workspace, staged_skill=outside)
    with pytest.raises(ValueError, match="staged Skill"):
        adapter.prepare(
            _spec(tmp_path, treatment=Treatment.FORCED_SKILL), forced_workspace
        )
    with pytest.raises(ValueError, match="inside"):
        adapter.prepare(
            _spec(tmp_path, treatment=Treatment.FORCED_SKILL),
            forced_workspace,
            staged_skill=outside,
        )


def test_codex_prepare_inherits_only_execution_and_explicit_auth_environment(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)
    secret = "sk-explicit-secret-must-not-persist"

    prepared = adapter.prepare(
        _spec(
            tmp_path,
            environment={
                "OPENAI_API_KEY": secret,
                "HTTPS_PROXY": "http://127.0.0.1:10808",
            },
        ),
        workspace,
    )

    assert prepared.environment["PATH"] == r"C:\Windows\System32"
    assert prepared.environment["SystemRoot"] == r"C:\Windows"
    assert prepared.environment["OPENAI_API_KEY"] == secret
    assert prepared.environment["HTTPS_PROXY"] == "http://127.0.0.1:10808"
    assert "UNRELATED_SECRET" not in prepared.environment
    assert secret not in repr(prepared.metadata)
    assert prepared.metadata["environment_keys"] == (
        "HTTPS_PROXY",
        "OPENAI_API_KEY",
        "PATH",
        "SystemRoot",
        "TEMP",
    )


def test_codex_prepare_rejects_non_auth_environment_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(tmp_path, ScriptedProcessRunner())

    with pytest.raises(ValueError, match="environment"):
        adapter.prepare(
            _spec(tmp_path, environment={"UNRELATED_SECRET": "not-allowed"}),
            workspace,
        )


def test_codex_run_passes_prepared_request_and_same_token(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)
    prepared = adapter.prepare(_spec(tmp_path), workspace)

    run = adapter.run(prepared)

    request = runner.requests[-1]
    assert request.command == prepared.command
    assert request.cwd == workspace.resolve()
    assert request.stdin_text == prepared.stdin_text
    assert dict(request.env) == dict(prepared.environment)
    assert request.timeout_seconds == 30
    assert runner.tokens[-1] is not None
    assert run.status is RunStatus.PASSED


def test_codex_run_persists_exit_code_args_and_environment_names_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "sk-process-record-secret-123456789"
    adapter = _adapter(tmp_path, ScriptedProcessRunner())
    prepared = adapter.prepare(
        _spec(tmp_path, environment={"OPENAI_API_KEY": secret}), workspace
    )

    run = adapter.run(prepared)

    process_ref = next(ref for ref in run.raw_event_refs if ref.endswith(".process.json"))
    process_text = (tmp_path / "artifacts" / process_ref).read_text(encoding="utf-8")
    process_record = json.loads(process_text)
    assert process_record["status"] == ProcessStatus.SUCCEEDED.value
    assert process_record["exit_code"] == 0
    assert process_record["duration_ms"] == 25
    assert process_record["command"] == list(prepared.command)
    assert process_record["environment_keys"] == sorted(prepared.environment)
    assert "environment" not in process_record
    assert secret not in process_text


def test_codex_run_redacts_stdout_and_stderr_before_normalization(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "sk-super-secret-value-123456789"
    stdout = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").replace(
        "1 passed", secret
    )
    runner = ScriptedProcessRunner(
        exec_result=_process_result(
            stdout=stdout,
            stderr=f"OPENAI_API_KEY={secret}\n",
        )
    )
    adapter = _adapter(tmp_path, runner)

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    persisted = [
        (tmp_path / "artifacts" / ref).read_text(encoding="utf-8")
        for ref in run.raw_event_refs
    ]
    assert all(secret not in content for content in persisted)
    assert secret not in repr(run.trace)
    assert any("[REDACTED]" in content for content in persisted)
    assert run.trace is not None
    stdout_events = tuple(
        json.loads(line) for line in persisted[0].splitlines() if line.strip()
    )
    assert [event.raw_hash for event in run.trace.events] == [
        stable_json_hash(plain_data(stdout_events[event.raw_offset]), length=32)
        for event in run.trace.events
    ]


def test_codex_success_message_containing_401_is_not_an_auth_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stdout = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").replace(
        "All tests pass.", "Fixed the expected HTTP 401 response."
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(exec_result=_process_result(stdout=stdout)),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is RunStatus.PASSED
    assert run.error_code is None


def test_codex_top_level_stream_error_blocks_apparent_completion(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lines = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").splitlines()
    lines.insert(-1, '{"type":"error","message":"event stream failed"}')
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(stdout="\n".join(lines) + "\n")
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace_complete is True
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_stream_error"


@pytest.mark.parametrize(
    "command",
    (
        "git show HEAD:evals/candidates/release-manifest-v2/effective.toml",
        "git show HEAD:evals/suites/release-manifest-v2/validators/validate_rm2.py",
    ),
)
def test_codex_baseline_hidden_read_is_an_isolation_error(
    tmp_path: Path,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stdout = (FIXTURE_ROOT / "success.jsonl").read_text(encoding="utf-8").replace(
        "python -m pytest -q",
        command,
    )
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(exec_result=_process_result(stdout=stdout)),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace_complete is True
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_baseline_isolation_leak"


def test_codex_parsed_turn_failure_is_not_an_adapter_schema_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path,
        ScriptedProcessRunner(
            exec_result=_process_result(
                stdout=(FIXTURE_ROOT / "failure.jsonl").read_text(encoding="utf-8")
            )
        ),
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.trace_complete is True
    assert run.status is RunStatus.INFRA_ERROR
    assert run.error_code == "codex_turn_failed"


@pytest.mark.parametrize(
    ("process_result", "expected_status", "error_code"),
    [
        (
            _process_result(status=ProcessStatus.TIMEOUT, exit_code=1),
            RunStatus.TIMEOUT,
            "codex_timeout",
        ),
        (
            _process_result(status=ProcessStatus.CANCELLED, exit_code=1),
            RunStatus.USER_CANCELLED,
            "codex_cancelled",
        ),
        (
            _process_result(status=ProcessStatus.SPAWN_ERROR, exit_code=None),
            RunStatus.INFRA_ERROR,
            "codex_spawn_error",
        ),
        (
            _process_result(
                status=ProcessStatus.FAILED,
                exit_code=1,
                stderr="401 Unauthorized: authentication required",
            ),
            RunStatus.AUTH_FAILED,
            "codex_auth_failed",
        ),
        (
            _process_result(
                status=ProcessStatus.FAILED,
                exit_code=1,
                stdout=(FIXTURE_ROOT / "failure.jsonl").read_text(encoding="utf-8"),
            ),
            RunStatus.INFRA_ERROR,
            "codex_process_failed",
        ),
        (
            _process_result(
                status=ProcessStatus.SUCCEEDED,
                exit_code=0,
                stdout='{"type":"thread.started"',
            ),
            RunStatus.ADAPTER_ERROR,
            "codex_trace_incomplete",
        ),
    ],
)
def test_codex_run_maps_process_and_trace_failures(
    tmp_path: Path,
    process_result: ProcessResult,
    expected_status: RunStatus,
    error_code: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(
        tmp_path, ScriptedProcessRunner(exec_result=process_result)
    )

    run = adapter.run(adapter.prepare(_spec(tmp_path), workspace))

    assert run.status is expected_status
    assert run.error_code == error_code
    assert run.raw_event_refs
    assert run.error_message is not None


def test_codex_cancel_before_run_does_not_submit_cli_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = ScriptedProcessRunner()
    adapter = _adapter(tmp_path, runner)
    prepared = adapter.prepare(_spec(tmp_path), workspace)

    adapter.cancel(prepared.run_id)
    run = adapter.run(prepared)

    assert run.status is RunStatus.USER_CANCELLED
    assert run.error_code == "codex_cancelled"
    assert runner.requests[-1].command == prepared.command
    assert runner.tokens[-1] is not None and runner.tokens[-1].is_cancelled


def test_codex_run_is_one_shot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = _adapter(tmp_path, ScriptedProcessRunner())
    prepared = adapter.prepare(_spec(tmp_path), workspace)
    adapter.run(prepared)

    with pytest.raises(ValueError, match="unknown|consumed"):
        adapter.run(prepared)


def test_codex_prepare_rejects_routed_task_that_names_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = _spec(tmp_path, treatment=Treatment.ROUTED_SKILL)
    spec = replace(
        spec,
        case=replace(spec.case, task="Use safe-shell to solve this task."),
    )
    assert spec.skill is not None
    staged = SkillMaterializer().materialize(spec.skill, AgentType.CODEX, workspace)

    with pytest.raises(ValueError, match="candidate"):
        _adapter(tmp_path, ScriptedProcessRunner()).prepare(
            spec, workspace, staged_skill=staged
        )
