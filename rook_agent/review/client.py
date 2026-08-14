"""EvoAgent HTTP 客户端与稳定报告格式。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from rook_agent.config.credentials import read_secret
from rook_agent.config.settings import AppConfig


_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_REVIEWERS = frozenset({"local", "ocr"})


class EvoAgentClientError(RuntimeError):
    def __init__(self, code: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class EvoAgentConfig:
    url: str
    project: str
    reviewers: tuple[str, ...] = ("local",)
    username: str = "admin"
    tenant_id: str = ""
    password: str | None = field(default=None, repr=False)
    timeout_seconds: int = 15

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("EvoAgent URL must be an absolute HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote EvoAgent URLs must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("EvoAgent URL must not contain credentials, query or fragment")
        if not _ALIAS.fullmatch(self.project):
            raise ValueError("project alias must contain only letters, digits, '_' or '-'")
        if not self.reviewers or len(set(self.reviewers)) != len(self.reviewers):
            raise ValueError("reviewers must be a non-empty unique list")
        unknown = sorted(set(self.reviewers) - _REVIEWERS)
        if unknown:
            raise ValueError("unknown reviewer: %s" % ", ".join(unknown))
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        object.__setattr__(self, "url", self.url.rstrip("/"))

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "EvoAgentConfig":
        env = config.env
        url = str(
            env.get("ROOK_EVOAGENT_URL")
            or config.get_section_value("review", "url", default="http://127.0.0.1:8080")
        )
        project = str(
            env.get("ROOK_EVOAGENT_PROJECT")
            or config.get_section_value("review", "project", default="rook")
        )
        raw_reviewers = config.get_section_value("review", "reviewers", default=["local"])
        if isinstance(raw_reviewers, str):
            reviewers = tuple(part.strip() for part in raw_reviewers.split(",") if part.strip())
        elif isinstance(raw_reviewers, list) and all(isinstance(item, str) for item in raw_reviewers):
            reviewers = tuple(item.strip() for item in raw_reviewers if item.strip())
        else:
            raise ValueError("review.reviewers must be a string list")
        username = str(env.get("ROOK_EVOAGENT_USERNAME") or "admin")
        tenant_id = str(env.get("ROOK_EVOAGENT_TENANT_ID") or "")
        secret_name = credential_name(url, username)
        password = env.get("ROOK_EVOAGENT_PASSWORD") or read_secret(secret_name)
        return cls(
            url=url,
            project=project,
            reviewers=reviewers,
            username=username,
            tenant_id=tenant_id,
            password=password,
        )


def credential_name(url: str, username: str) -> str:
    parsed = urlparse(url)
    authority = parsed.netloc.lower()
    return f"evoagent:{authority}:{username}"


class EvoAgentClient:
    def __init__(self, config: EvoAgentConfig) -> None:
        self.config = config
        self._token: str | None = None

    def doctor(self) -> dict[str, Any]:
        health = self._request("GET", "/health", authenticated=False)
        result: dict[str, Any] = {
            "service": str(health.get("status", "unknown")),
            "provider": str(health.get("llm_provider", "unknown")),
            "project_configured": self.config.project in health.get("workspace_projects", []),
            "project": self.config.project,
            "reviewers": list(self.config.reviewers),
        }
        try:
            result["ocr"] = self._request("GET", "/v1/reviewers/ocr/status")
            result["authentication"] = "ok"
        except EvoAgentClientError as exc:
            if exc.status not in {401, 403}:
                raise
            result["authentication"] = exc.code
            result["ocr"] = health.get("ocr", {})
        return result

    def submit(
        self,
        *,
        target: str,
        from_ref: str | None = None,
        to_ref: str | None = None,
        commit: str | None = None,
        reviewers: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if target not in {"workspace", "range", "commit"}:
            raise ValueError("target must be workspace, range or commit")
        if target == "range" and not (from_ref and to_ref):
            raise ValueError("range requires from_ref and to_ref")
        if target == "commit" and not commit:
            raise ValueError("commit target requires commit")
        selected = reviewers or self.config.reviewers
        payload = {
            "project": self.config.project,
            "target": target,
            "from_ref": from_ref,
            "to_ref": to_ref,
            "commit": commit,
            "reviewers": list(selected),
            "background": True,
        }
        return self._request("POST", "/v1/reviews/workspace", payload)

    def task(self, task_id: str) -> dict[str, Any]:
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("invalid EvoAgent task id")
        return self._request("GET", f"/v1/tasks/{task_id}")

    def _login(self) -> None:
        if not self.config.password:
            raise EvoAgentClientError(
                "evoagent_credentials_missing",
                "EvoAgent credentials are missing; run `rook review login --username <name>`.",
                status=401,
            )
        value = self._request(
            "POST",
            "/v1/auth/login",
            {
                "username": self.config.username,
                "password": self.config.password,
                "tenant_id": self.config.tenant_id,
            },
            authenticated=False,
        )
        token = value.get("access_token") or value.get("token")
        if not isinstance(token, str) or not token:
            raise EvoAgentClientError("evoagent_invalid_auth_response", "login response lacks a token")
        self._token = token

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if authenticated and self._token is None and self.config.password:
            self._login()
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(self.config.url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            code, detail = _error_payload(raw, fallback=f"HTTP {exc.code}")
            raise EvoAgentClientError(code, detail, status=exc.code) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise EvoAgentClientError("evoagent_unavailable", str(exc)) from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvoAgentClientError(
                "evoagent_invalid_json", "EvoAgent returned invalid UTF-8 JSON"
            ) from exc
        if not isinstance(value, dict):
            raise EvoAgentClientError("evoagent_invalid_schema", "EvoAgent response must be an object")
        return value


def _error_payload(raw: bytes, *, fallback: str) -> tuple[str, str]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "evoagent_http_error", fallback
    if not isinstance(value, dict):
        return "evoagent_http_error", fallback
    code = str(value.get("error") or "evoagent_http_error")
    detail = str(value.get("detail") or value.get("error") or fallback)
    return code, detail


def render_review_task(task: dict[str, Any]) -> str:
    task_id = str(task.get("id") or task.get("task_id") or "-")
    state = str(task.get("state") or "UNKNOWN")
    lines = [f"# REVIEW // {task_id}", "", f"State: `{state}`"]
    report = task.get("report")
    if not isinstance(report, dict):
        return "\n".join(lines)
    lines.extend((f"Risk: `{report.get('risk', 'unknown')}`", ""))
    findings = report.get("findings")
    if not isinstance(findings, list) or not findings:
        lines.append("No findings.")
        return "\n".join(lines)
    for index, raw in enumerate(findings, start=1):
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "-")
        start = raw.get("start_line") or raw.get("line") or "?"
        end = raw.get("end_line") or start
        location = str(start) if start == end else f"{start}-{end}"
        severity = str(raw.get("severity") or "medium").upper()
        source = str(raw.get("source") or "unknown")
        message = str(raw.get("message") or raw.get("content") or "")
        lines.extend(
            (
                f"## {index}. {severity} · `{path}:{location}`",
                "",
                f"Source: `{source}`",
                "",
                message,
                "",
            )
        )
    lines.append(f"Use `/review-report {task_id} --fix <n>` to create an explicit repair task.")
    return "\n".join(lines)


def finding_fix_prompt(task: dict[str, Any], index: int) -> str:
    report = task.get("report")
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list) or index < 1 or index > len(findings):
        raise ValueError("finding index is out of range")
    finding = findings[index - 1]
    if not isinstance(finding, dict):
        raise ValueError("finding is invalid")
    path = str(finding.get("path") or "")
    message = str(finding.get("message") or finding.get("content") or "")
    source = str(finding.get("source") or "unknown")
    line = finding.get("start_line") or finding.get("line") or "?"
    return (
        f"审阅 EvoAgent Finding：{path}:{line}，来源 {source}。问题：{message}\n"
        "先读取相关代码并验证问题；确认成立后仅修改必要文件并运行直接相关测试。"
    )


__all__ = [
    "EvoAgentClient",
    "EvoAgentClientError",
    "EvoAgentConfig",
    "credential_name",
    "finding_fix_prompt",
    "render_review_task",
]
