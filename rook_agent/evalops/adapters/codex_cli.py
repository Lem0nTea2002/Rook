"""Black-box ``codex exec --json`` adapter for Agent EvalOps."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import threading
from typing import Any, Protocol

from rook_agent.agent.cancellation import CancellationToken
from rook_agent.context.identity import stable_json_hash
from rook_agent.evolution.gate import redact_sensitive_text
from rook_agent.evalops.adapters.base import AgentCapabilities, PreparedRun
from rook_agent.evalops.artifacts import ArtifactStore, redact_value
from rook_agent.evalops.models import (
    AgentRun,
    AgentType,
    NormalizedTrace,
    NetworkPolicy,
    RunSpec,
    RunStatus,
    Treatment,
    TreatmentFamily,
)
from rook_agent.evalops.normalizers.codex import (
    NORMALIZER_VERSION,
    RESTRICTED_POWERSHELL_FAILURE_DIAGNOSTIC,
    SHELL_FALLBACK_EXHAUSTED_DIAGNOSTIC,
    CodexTraceNormalizer,
)
from rook_agent.evalops.process import (
    ProcessRequest,
    ProcessResult,
    ProcessRunner,
    ProcessStatus,
)
from rook_agent.evalops.workspace import hash_workspace
from rook_agent.shell_recovery import WINDOWS_RESTRICTED_SHELL_GUIDANCE


_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_REQUIRED_EXEC_FLAGS = (
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
)
_OS_EXECUTION_ENV_KEYS = frozenset(
    {
        "allusersprofile",
        "appdata",
        "comspec",
        "home",
        "homedrive",
        "homepath",
        "localappdata",
        "path",
        "pathext",
        "programdata",
        "systemdrive",
        "systemroot",
        "temp",
        "tmp",
        "tmpdir",
        "userprofile",
        "windir",
    }
)
_EXPLICIT_CODEX_ENV_KEYS = frozenset(
    {
        "all_proxy",
        "codex_api_key",
        "codex_home",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "openai_api_key",
        "openai_base_url",
        "ssl_cert_dir",
        "ssl_cert_file",
    }
)
_AUTH_FAILURE_MARKERS = (
    "401",
    "authentication required",
    "authentication failed",
    "invalid api key",
    "not logged in",
    "unauthorized",
)
_WINDOWS_SANDBOX_FAILURE_MARKERS = (
    "windows sandbox: createprocessasuserw failed:",
    "windows sandbox: runner error:",
    "windows sandbox: runner failed during",
    "windows sandbox: setup refresh",
)
_WINDOWS_TOOL_CWD_ESCAPE_MARKERS = ("\\u{", "\x08")
_RECOVERED_RECONNECT = re.compile(
    r"\AReconnecting\.\.\. [1-9][0-9]*/[1-9][0-9]* \([^\r\n]+\)\Z"
)
_HTTP_ONLY_PROVIDER_OVERRIDES = (
    'model_provider="rook-chatgpt-http"',
    'model_providers.rook-chatgpt-http.name="Rook ChatGPT HTTP"',
    'model_providers.rook-chatgpt-http.base_url="https://chatgpt.com/backend-api/codex"',
    'model_providers.rook-chatgpt-http.wire_api="responses"',
    "model_providers.rook-chatgpt-http.requires_openai_auth=true",
    "model_providers.rook-chatgpt-http.supports_websockets=false",
)
_BASELINE_ISOLATION_MARKERS = (
    ".agents/skills/",
    ".agents\\skills\\",
    "evals/candidates/",
    "evals\\candidates\\",
    "/validators/validate_rm2.py",
    "\\validators\\validate_rm2.py",
)
_WINDOWS_SHELL_WRITE_GUIDANCE = (
    "Execution constraints for this Windows workspace:\n"
    "- Use shell commands for file changes; do not call apply_patch.\n"
    "- Treat the current directory as the complete isolated workspace; do not "
    "run git or search parent directories.\n"
    "- Do not set or override the shell tool working directory. It already "
    "starts in the isolated workspace; use relative paths with forward slashes "
    "inside tool arguments.\n"
    + WINDOWS_RESTRICTED_SHELL_GUIDANCE
    + "- Finish with a best-effort result within the task time limit.\n"
)
_CONTENT_EXPERIMENT_GUIDANCE = (
    "- Use only the task inputs and any explicitly named Skill; do not search "
    "for other repository guidance.\n"
    "- Do not search for conventions or examples beyond files named by the task; "
    "make a direct best-effort attempt when the task does not supply a rule.\n"
    "- Stop after creating and verifying the requested output.\n"
)


class ProcessRunnerLike(Protocol):
    def run(
        self,
        request: ProcessRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> ProcessResult: ...


WhichExecutable = Callable[[str], str | None]


class CodexCliAdapter:
    """Run a local Codex CLI with isolated config and normalized JSONL evidence."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        process_runner: ProcessRunnerLike | None = None,
        normalizer: CodexTraceNormalizer | None = None,
        executable: str = "codex",
        which: WhichExecutable = shutil.which,
        host_environment: Mapping[str, str] | None = None,
        platform_name: str = sys.platform,
    ) -> None:
        self._artifacts = artifact_store
        self._runner = process_runner or ProcessRunner()
        self._normalizer = normalizer or CodexTraceNormalizer()
        self._executable = executable
        self._which = which
        self._host_environment = dict(
            os.environ if host_environment is None else host_environment
        )
        self._platform_name = platform_name
        self._tokens: dict[str, CancellationToken] = {}
        self._known_run_ids: set[str] = set()
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def probe(self) -> AgentCapabilities:
        executable_path = self._which(self._executable)
        if executable_path is None:
            return self._capabilities(
                available=False,
                executable_path=None,
                version=None,
                supported=False,
                diagnostic_code=RunStatus.ADAPTER_UNAVAILABLE.value,
            )
        environment = self._safe_environment({})
        version_result = self._runner.run(
            ProcessRequest(
                command=(executable_path, "--version"),
                cwd=Path.cwd(),
                env=environment,
                timeout_seconds=10,
            )
        )
        if version_result.status is not ProcessStatus.SUCCEEDED:
            return self._capabilities(
                available=True,
                executable_path=executable_path,
                version=None,
                supported=False,
                diagnostic_code=RunStatus.INFRA_ERROR.value,
            )
        version = _first_nonempty_line(version_result.stdout)
        if version is None:
            return self._capabilities(
                available=True,
                executable_path=executable_path,
                version=None,
                supported=False,
                diagnostic_code=RunStatus.VERSION_UNSUPPORTED.value,
            )
        help_result = self._runner.run(
            ProcessRequest(
                command=(executable_path, "exec", "--help"),
                cwd=Path.cwd(),
                env=environment,
                timeout_seconds=10,
            )
        )
        help_supported = (
            help_result.status is ProcessStatus.SUCCEEDED
            and all(flag in help_result.stdout for flag in _REQUIRED_EXEC_FLAGS)
        )
        config_supported = False
        if help_supported:
            config_result = self._runner.run(
                ProcessRequest(
                    command=_config_validation_command(
                        executable_path,
                        windows_sandbox=(
                            "unelevated"
                            if self._platform_name == "win32"
                            else None
                        ),
                    ),
                    cwd=Path.cwd(),
                    env=environment,
                    timeout_seconds=10,
                )
            )
            config_supported = config_result.status is ProcessStatus.SUCCEEDED
        supported = help_supported and config_supported
        return self._capabilities(
            available=True,
            executable_path=executable_path,
            version=version,
            supported=supported,
            diagnostic_code=(
                None if supported else RunStatus.VERSION_UNSUPPORTED.value
            ),
        )

    @staticmethod
    def _capabilities(
        *,
        available: bool,
        executable_path: str | None,
        version: str | None,
        supported: bool,
        diagnostic_code: str | None,
    ) -> AgentCapabilities:
        return AgentCapabilities(
            available=available,
            executable_path=executable_path,
            version=version,
            non_interactive=supported,
            structured_events=supported,
            supports_timeout=True,
            supports_turn_limit=False,
            supports_budget_limit=False,
            supports_sandbox=supported,
            supported_treatments=tuple(Treatment) if supported else (),
            normalizer_version=NORMALIZER_VERSION if supported else None,
            event_types=(
                "run_started",
                "turn_started",
                "assistant_message",
                "tool_requested",
                "tool_completed",
                "workspace_changed",
                "run_completed",
                "run_failed",
            )
            if supported
            else (),
            diagnostic_code=diagnostic_code,
        )

    def prepare(
        self,
        spec: RunSpec,
        workspace: Path,
        *,
        staged_skill: Path | None = None,
    ) -> PreparedRun:
        if spec.target.type is not AgentType.CODEX:
            raise ValueError("CodexCliAdapter requires a Codex target")
        executable_path = self._which(self._executable)
        if executable_path is None:
            raise ValueError("Codex executable is unavailable")
        workspace_root = Path(workspace).absolute()
        hash_workspace(workspace_root)
        workspace_root = workspace_root.resolve()
        resolved_skill = self._validate_staging(
            spec,
            workspace_root=workspace_root,
            staged_skill=staged_skill,
        )
        prompt = _prompt(
            spec,
            resolved_skill,
            workspace_root,
            windows_compatibility=self._platform_name == "win32",
        )
        _reject_internal_absolute_paths(prompt, workspace_root, resolved_skill)
        environment = self._safe_environment(spec.environment_allowlist)
        command = _command(
            executable_path,
            workspace=workspace_root,
            model=spec.target.model,
            include_skill_instructions=(
                spec.treatment_family is not TreatmentFamily.CONTENT
            ),
            windows_sandbox=(
                "unelevated" if self._platform_name == "win32" else None
            ),
            network_policy=spec.case.network_policy,
        )
        run_id = "codex-" + stable_json_hash(
            {
                "experiment_id": spec.experiment_id,
                "pair_id": spec.pair_id,
                "target": spec.target.fingerprint,
                "case_id": spec.case.id,
                "treatment": spec.treatment.value,
                "workspace_snapshot_hash": spec.workspace_snapshot_hash,
                "skill": spec.skill.fingerprint if spec.skill is not None else None,
                "timeout_seconds": spec.timeout_seconds,
                "environment_keys": sorted(environment),
            },
            length=24,
        )
        with self._lock:
            if run_id in self._known_run_ids:
                raise ValueError("Codex run identity was already prepared")
            self._known_run_ids.add(run_id)
            self._tokens[run_id] = CancellationToken()
        return PreparedRun(
            run_id=run_id,
            spec=spec,
            workspace=workspace_root,
            staged_skill=resolved_skill,
            command=command,
            stdin_text=prompt,
            environment=environment,
            metadata={
                "environment_keys": tuple(sorted(environment)),
                "executable_path": executable_path,
            },
        )

    def run(self, prepared: PreparedRun) -> AgentRun:
        token = self._claim(prepared.run_id)
        try:
            result = self._runner.run(
                ProcessRequest(
                    command=prepared.command,
                    cwd=prepared.workspace,
                    stdin_text=prepared.stdin_text,
                    env=prepared.environment,
                    timeout_seconds=prepared.spec.timeout_seconds,
                ),
                cancellation_token=token,
            )
            sanitized_events = _sanitize_stdout_jsonl(
                result.stdout,
                network_policy=prepared.spec.case.network_policy,
            )
            sanitized_stderr = redact_sensitive_text(result.stderr)
            stdout_ref = self._artifacts.write_jsonl(
                Path("raw-events") / f"{prepared.run_id}.jsonl",
                sanitized_events,
            )
            stderr_ref = self._artifacts.write_text(
                Path("raw-events") / f"{prepared.run_id}.stderr.txt",
                sanitized_stderr,
            )
            process_ref = self._artifacts.write_json(
                Path("raw-events") / f"{prepared.run_id}.process.json",
                {
                    "status": result.status.value,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "cleanup_error": result.cleanup_error,
                    "command": list(prepared.command),
                    "environment_keys": sorted(prepared.environment),
                },
            )
            try:
                trace = self._normalizer.normalize(
                    sanitized_events,
                    target=prepared.spec.target,
                )
            except Exception:
                trace = NormalizedTrace(
                    trace_complete=False,
                    normalizer_version="codex-normalizer-error",
                    diagnostics=("codex_normalizer_error",),
                )
            status, error_code = _run_status(
                result,
                trace,
                cancelled=token.is_cancelled,
            )
            if (
                self._platform_name == "win32"
                and _contains_windows_tool_cwd_escape(sanitized_stderr)
            ):
                status = RunStatus.INFRA_ERROR
                error_code = "codex_windows_tool_cwd_escape_error"
            elif (
                self._platform_name == "win32"
                and _contains_windows_sandbox_failure(sanitized_stderr)
            ):
                status = RunStatus.INFRA_ERROR
                error_code = "codex_windows_sandbox_error"
            if (
                prepared.spec.treatment is Treatment.BASELINE
                and _contains_baseline_isolation_marker(sanitized_events)
            ):
                status = RunStatus.INFRA_ERROR
                error_code = "codex_baseline_isolation_leak"
            return self._result(
                prepared,
                status=status,
                trace=trace,
                raw_refs=(
                    stdout_ref.relative_path,
                    stderr_ref.relative_path,
                    process_ref.relative_path,
                ),
                latency_ms=result.duration_ms,
                error_code=error_code,
            )
        finally:
            with self._lock:
                self._running.discard(prepared.run_id)
                self._tokens.pop(prepared.run_id, None)

    def cancel(self, run_id: str) -> None:
        with self._lock:
            token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()

    def _claim(self, run_id: str) -> CancellationToken:
        with self._lock:
            token = self._tokens.get(run_id)
            if token is None:
                raise ValueError("prepared Codex run is unknown or already consumed")
            if run_id in self._running:
                raise ValueError("prepared Codex run is already running")
            self._running.add(run_id)
            return token

    def _safe_environment(
        self, configured: Mapping[str, str]
    ) -> dict[str, str]:
        environment = {
            key: value
            for key, value in self._host_environment.items()
            if key.casefold() in _OS_EXECUTION_ENV_KEYS
        }
        for key, value in configured.items():
            if key.casefold() not in _EXPLICIT_CODEX_ENV_KEYS:
                raise ValueError(f"Codex environment key is not allowed: {key}")
            environment[key] = value
        return environment

    @staticmethod
    def _validate_staging(
        spec: RunSpec,
        *,
        workspace_root: Path,
        staged_skill: Path | None,
    ) -> Path | None:
        if spec.treatment is Treatment.BASELINE:
            if spec.skill is not None or staged_skill is not None:
                raise ValueError("baseline treatment must not include a candidate Skill")
            return None
        if spec.skill is None or staged_skill is None:
            raise ValueError("Skill treatment requires a staged Skill")
        requested = Path(staged_skill).absolute()
        try:
            status = requested.lstat()
        except FileNotFoundError:
            raise ValueError("staged Skill must be an existing file") from None
        if (
            stat.S_ISLNK(status.st_mode)
            or _is_reparse_point(status)
            or not stat.S_ISREG(status.st_mode)
        ):
            raise ValueError("staged Skill must be a regular non-redirect file")
        resolved = requested.resolve()
        if workspace_root not in resolved.parents:
            raise ValueError("staged Skill must be inside the isolated workspace")
        expected = (
            workspace_root
            / ".agents"
            / "skills"
            / spec.skill.bundle.name
            / "SKILL.md"
        ).resolve()
        if resolved != expected:
            raise ValueError("staged Skill is not in Codex's project Skill layout")
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != spec.skill.content_hash:
            raise ValueError("staged Skill content does not match the candidate")
        relative = resolved.relative_to(workspace_root).as_posix()
        if relative in spec.case.task:
            raise ValueError("EvalCase task must not pre-name the candidate Skill")
        if (
            spec.treatment is Treatment.ROUTED_SKILL
            and spec.skill.bundle.name.casefold() in spec.case.task.casefold()
        ):
            raise ValueError("routed EvalCase task must not name the candidate slug")
        return resolved

    @staticmethod
    def _result(
        prepared: PreparedRun,
        *,
        status: RunStatus,
        trace: NormalizedTrace,
        raw_refs: tuple[str, ...],
        latency_ms: int,
        error_code: str | None,
    ) -> AgentRun:
        error_message = _error_message(error_code)
        try:
            workspace_result_hash = hash_workspace(prepared.workspace)
        except Exception:
            workspace_result_hash = None
            status = RunStatus.INFRA_ERROR
            error_code = "codex_workspace_hash_error"
            error_message = "Codex EvalOps could not verify the final workspace."
        return AgentRun(
            run_id=prepared.run_id,
            experiment_id=prepared.spec.experiment_id,
            pair_id=prepared.spec.pair_id,
            target=prepared.spec.target,
            case_id=prepared.spec.case.id,
            treatment=prepared.spec.treatment,
            status=status,
            trace=trace,
            raw_event_refs=raw_refs,
            workspace_result_hash=workspace_result_hash,
            final_answer=trace.final_answer,
            input_tokens=trace.usage.input_tokens,
            output_tokens=trace.usage.output_tokens,
            cost_usd=trace.cost_usd,
            latency_ms=latency_ms,
            trace_complete=trace.trace_complete,
            error_code=error_code,
            error_message=error_message,
        )


def _config_validation_command(
    executable_path: str,
    *,
    windows_sandbox: str | None,
) -> tuple[str, ...]:
    """Load every immutable EvalOps config override without starting a model."""
    command = [executable_path]
    overrides = [
        *_HTTP_ONLY_PROVIDER_OVERRIDES,
        'web_search="disabled"',
        "sandbox_workspace_write.network_access=false",
        "allow_login_shell=false",
        "skills.include_instructions=false",
        'approval_policy="never"',
    ]
    if windows_sandbox is not None:
        overrides.extend(
            (
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                f'windows.sandbox="{windows_sandbox}"',
            )
        )
    for override in overrides:
        command.extend(("-c", override))
    command.extend(("features", "list"))
    return tuple(command)


def _command(
    executable_path: str,
    *,
    workspace: Path,
    model: str | None,
    include_skill_instructions: bool,
    windows_sandbox: str | None,
    network_policy: NetworkPolicy,
) -> tuple[str, ...]:
    # Codex forwards ``-C`` through its sandbox configuration.  On Windows a
    # native path segment such as ``\baseline`` can otherwise be decoded as
    # the JSON escape ``\b`` and turn into an invalid working directory.
    workspace_argument = (
        workspace.as_posix() if windows_sandbox is not None else str(workspace)
    )
    command = [
        executable_path,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--disable",
        "plugins",
        "--disable",
        "memories",
    ]
    if network_policy is not NetworkPolicy.DISABLED:
        raise ValueError(
            "Codex EvalOps currently supports only disabled Agent network access"
        )
    # The built-in ChatGPT provider prefers WebSockets and waits through its
    # complete reconnect budget before falling back to HTTPS. EvalOps needs a
    # deterministic transport boundary, so use the same authenticated ChatGPT
    # endpoint through a controlled provider that starts with HTTP/SSE.
    for override in _HTTP_ONLY_PROVIDER_OVERRIDES:
        command.extend(("-c", override))
    command.extend(
        (
            "-c",
            'web_search="disabled"',
            "-c",
            "sandbox_workspace_write.network_access=false",
            "-c",
            "allow_login_shell=false",
        )
    )
    if not include_skill_instructions:
        command.extend(("-c", "skills.include_instructions=false"))
    if windows_sandbox is not None:
        command.extend(
            (
                "-c",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            )
        )
    command.extend(
        (
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "-C",
            workspace_argument,
        )
    )
    if windows_sandbox is not None:
        command.extend(("-c", f'windows.sandbox="{windows_sandbox}"'))
    command.extend(("-c", 'approval_policy="never"'))
    if model is not None:
        command.extend(("--model", model))
    command.append("-")
    return tuple(command)


def _prompt(
    spec: RunSpec,
    staged_skill: Path | None,
    workspace: Path,
    *,
    windows_compatibility: bool,
) -> str:
    guidance = ""
    if windows_compatibility:
        guidance += _WINDOWS_SHELL_WRITE_GUIDANCE
    if spec.treatment_family is TreatmentFamily.CONTENT:
        guidance += _CONTENT_EXPERIMENT_GUIDANCE
    if guidance:
        guidance += "\n"
    if spec.treatment is Treatment.FORCED_SKILL:
        if staged_skill is None:
            raise ValueError("forced Skill treatment requires a staged Skill")
        relative_skill = staged_skill.relative_to(workspace).as_posix()
        return f"{guidance}Read and follow `{relative_skill}`.\n\n{spec.case.task}"
    return f"{guidance}{spec.case.task}"


def _reject_internal_absolute_paths(
    prompt: str,
    workspace: Path,
    staged_skill: Path | None,
) -> None:
    sensitive_paths = [str(workspace), workspace.as_posix()]
    if staged_skill is not None:
        sensitive_paths.extend((str(staged_skill), staged_skill.as_posix()))
    if any(path and path in prompt for path in sensitive_paths):
        raise ValueError("EvalCase prompt exposes an internal absolute path")


def _run_status(
    result: ProcessResult,
    trace: NormalizedTrace,
    *,
    cancelled: bool,
) -> tuple[RunStatus, str | None]:
    if cancelled or result.status is ProcessStatus.CANCELLED:
        return RunStatus.USER_CANCELLED, "codex_cancelled"
    if result.status is ProcessStatus.TIMEOUT:
        if _contains_cleanup_diagnostic(
            result.cleanup_error,
            "timeout_deadline_overrun",
        ):
            return RunStatus.INFRA_ERROR, "codex_timeout_deadline_overrun"
        if RESTRICTED_POWERSHELL_FAILURE_DIAGNOSTIC in trace.diagnostics:
            return RunStatus.TIMEOUT, "codex_restricted_shell_timeout"
        return RunStatus.TIMEOUT, "codex_timeout"
    if result.status is ProcessStatus.SPAWN_ERROR:
        return RunStatus.INFRA_ERROR, "codex_spawn_error"
    if result.cleanup_error is not None:
        return RunStatus.INFRA_ERROR, "codex_cleanup_error"
    if result.status is ProcessStatus.FAILED:
        if _looks_like_auth_failure(result.stdout, result.stderr):
            return RunStatus.AUTH_FAILED, "codex_auth_failed"
        return RunStatus.INFRA_ERROR, "codex_process_failed"
    if "codex_normalizer_error" in trace.diagnostics:
        return RunStatus.ADAPTER_ERROR, "codex_normalizer_error"
    if "codex_web_search_policy_violation" in trace.diagnostics:
        return RunStatus.UNSAFE_ACTION, "codex_web_search_policy_violation"
    if SHELL_FALLBACK_EXHAUSTED_DIAGNOSTIC in trace.diagnostics:
        return RunStatus.ADAPTER_ERROR, "codex_shell_fallback_exhausted"
    if not trace.trace_complete:
        return RunStatus.ADAPTER_ERROR, "codex_trace_incomplete"
    terminal_types = tuple(
        event.type
        for event in trace.events
        if event.type in {"run_completed", "run_failed"}
    )
    if terminal_types == ("run_failed",):
        if _looks_like_auth_failure(result.stdout, result.stderr):
            return RunStatus.AUTH_FAILED, "codex_auth_failed"
        return RunStatus.INFRA_ERROR, "codex_turn_failed"
    if terminal_types != ("run_completed",):
        return RunStatus.ADAPTER_ERROR, "codex_terminal_invalid"
    stream_errors = tuple(event for event in trace.events if event.type == "run_error")
    if stream_errors and not all(
        _is_recovered_reconnect(event.data.get("message"))
        for event in stream_errors
    ):
        return RunStatus.INFRA_ERROR, "codex_stream_error"
    # Codex emits top-level ``error`` events for transient reconnect attempts.
    # A later, unique ``turn.completed`` plus a successful process exit proves
    # that a strictly shaped reconnect recovered; the normalizer retains the
    # diagnostic without converting it into an infrastructure exclusion.
    return RunStatus.PASSED, None


def _error_message(error_code: str | None) -> str | None:
    if error_code is None:
        return None
    if error_code == "codex_restricted_shell_timeout":
        return (
            "Codex reached the restricted PowerShell recovery limit before the run "
            "completed."
        )
    if error_code == "codex_shell_fallback_exhausted":
        return "Codex could not recover from the restricted PowerShell environment."
    if error_code == "codex_windows_tool_cwd_escape_error":
        return (
            "Codex supplied an escaped Windows tool working directory that the "
            "sandbox could not start."
        )
    if error_code == "codex_timeout_deadline_overrun":
        return (
            "The host was suspended or the scheduler could not enforce the Codex "
            "deadline."
        )
    return "Codex EvalOps run did not produce an admissible result."


def _looks_like_auth_failure(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".casefold()
    return any(marker in combined for marker in _AUTH_FAILURE_MARKERS)


def _contains_windows_sandbox_failure(stderr: str) -> bool:
    folded = stderr.casefold()
    return any(marker in folded for marker in _WINDOWS_SANDBOX_FAILURE_MARKERS)


def _contains_windows_tool_cwd_escape(stderr: str) -> bool:
    folded = stderr.casefold()
    return (
        "windows sandbox: createprocessasuserw failed: 267" in folded
        and "cwd=" in folded
        and any(marker in stderr for marker in _WINDOWS_TOOL_CWD_ESCAPE_MARKERS)
    )


def _contains_cleanup_diagnostic(value: str | None, diagnostic: str) -> bool:
    return isinstance(value, str) and diagnostic in value.split(";")


def _is_recovered_reconnect(message: object) -> bool:
    return isinstance(message, str) and _RECOVERED_RECONNECT.fullmatch(message) is not None


def _contains_baseline_isolation_marker(events: object) -> bool:
    if isinstance(events, str):
        text = events.casefold()
        return any(marker in text for marker in _BASELINE_ISOLATION_MARKERS)
    if isinstance(events, Mapping):
        return any(
            _contains_baseline_isolation_marker(value)
            for value in events.values()
        )
    if isinstance(events, tuple | list):
        return any(_contains_baseline_isolation_marker(value) for value in events)
    return False


def _first_nonempty_line(value: str) -> str | None:
    return next((line.strip() for line in value.splitlines() if line.strip()), None)


def _sanitize_stdout_jsonl(
    text: str,
    *,
    network_policy: NetworkPolicy,
) -> tuple[dict[str, object], ...]:
    sanitized: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines()):
        if (
            network_policy is NetworkPolicy.DISABLED
            and '"type":"web_search"' in line.replace(" ", "")
        ):
            sanitized.append(
                {
                    "type": "rook.codex.policy_violation",
                    "line_number": line_number,
                    "policy": "network_disabled",
                    "violation": "web_search",
                }
            )
            continue
        try:
            if not line.strip():
                raise ValueError("blank JSONL line")
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(value, dict):
                raise ValueError("JSONL event is not an object")
            redacted = redact_value(value)
            if not isinstance(redacted, dict):
                raise ValueError("redacted JSONL event is not an object")
            sanitized.append(redacted)
        except (TypeError, ValueError, json.JSONDecodeError):
            sanitized.append(
                {
                    "type": "rook.codex.parse_error",
                    "line_number": line_number,
                    "line": redact_sensitive_text(line),
                }
            )
    return tuple(sanitized)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Codex JSONL contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def _is_reparse_point(status: object) -> bool:
    return bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
    )


__all__ = ["CodexCliAdapter"]
