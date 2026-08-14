from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from rook_agent.app.review_commands import ReviewCommandHandler
from rook_agent.cli import build_parser, main
from rook_agent.config.settings import AppConfig
from rook_agent.permissions.manager import PermissionManager
from rook_agent.permissions.policy import DefaultPermissionPolicy
from rook_agent.permissions.types import PermissionMode
from rook_agent.review.client import EvoAgentClient, EvoAgentConfig


class _FakeEvoAgentHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def _body(self) -> dict[str, object] | None:
        size = int(self.headers.get("Content-Length", "0"))
        if not size:
            return None
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.calls.append(("GET", self.path, None))
        if self.path == "/health":
            self._send(
                200,
                {
                    "status": "ok",
                    "llm_provider": "local",
                    "workspace_projects": ["rook"],
                    "ocr": {"configured": True, "expected_version": "1.8.5"},
                },
            )
            return
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send(401, {"error": "invalid bearer token"})
            return
        if self.path == "/v1/reviewers/ocr/status":
            self._send(200, {"configured": True, "ready": True, "actual_version": "1.8.5"})
            return
        if self.path == "/v1/tasks/task-1":
            self._send(
                200,
                {
                    "id": "task-1",
                    "state": "SUCCESS",
                    "report": {
                        "risk": "medium",
                        "findings": [
                            {
                                "path": "app.py",
                                "line": 4,
                                "start_line": 4,
                                "end_line": 5,
                                "severity": "medium",
                                "rule_id": "OCR-001",
                                "message": "Validate user input.",
                                "source": "open-code-review",
                                "provenance": {"reviewer": "open-code-review"},
                            }
                        ],
                    },
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        body = self._body()
        self.calls.append(("POST", self.path, body))
        if self.path == "/v1/auth/login":
            self._send(200, {"token": "test-token"})
            return
        if self.headers.get("Authorization") != "Bearer test-token":
            self._send(401, {"error": "invalid bearer token"})
            return
        if self.path == "/v1/reviews/workspace":
            self._send(202, {"task_id": "task-1", "state": "PENDING"})
            return
        self._send(404, {"error": "not found"})


@pytest.fixture
def evoagent_server():
    _FakeEvoAgentHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEvoAgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _FakeEvoAgentHandler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(url: str) -> EvoAgentConfig:
    return EvoAgentConfig(
        url=url,
        project="rook",
        reviewers=("local", "ocr"),
        username="admin",
        password="secret",
    )


def test_client_auth_submit_report_and_doctor(evoagent_server) -> None:
    url, calls = evoagent_server
    client = EvoAgentClient(_config(url))

    doctor = client.doctor()
    submitted = client.submit(target="workspace")
    task = client.task("task-1")

    assert doctor["service"] == "ok"
    assert doctor["provider"] == "local"
    assert doctor["ocr"]["actual_version"] == "1.8.5"
    assert submitted == {"task_id": "task-1", "state": "PENDING"}
    assert task["report"]["findings"][0]["source"] == "open-code-review"
    assert sum(path == "/v1/auth/login" for _, path, _ in calls) == 1
    review_payload = next(body for method, path, body in calls if path == "/v1/reviews/workspace")
    assert review_payload == {
        "project": "rook",
        "target": "workspace",
        "from_ref": None,
        "to_ref": None,
        "commit": None,
        "reviewers": ["local", "ocr"],
        "background": True,
    }


def test_client_rejects_absolute_project_alias() -> None:
    with pytest.raises(ValueError, match="project alias"):
        EvoAgentConfig(url="http://127.0.0.1:8080", project="C:/repo")


@dataclass
class _Session:
    permission_manager: PermissionManager


def test_tui_review_waits_for_network_permission(evoagent_server) -> None:
    url, calls = evoagent_server
    manager = PermissionManager(
        policy=DefaultPermissionPolicy(Path.cwd()),
        mode=PermissionMode.ASK,
    )
    handler = ReviewCommandHandler(
        client=EvoAgentClient(_config(url)),
        session=_Session(manager),
    )

    pending = handler.handle("/review workspace")
    assert pending.action is not None
    assert calls == []

    token = handler.pending_token
    approved = handler.handle(f"/review-authorize allow_once {token}")
    assert approved.output == "Review submitted: task-1 (PENDING)"
    assert any(path == "/v1/reviews/workspace" for _, path, _ in calls)


def test_review_config_reads_only_declared_fields() -> None:
    config = AppConfig(
        provider_name="fake",
        env={},
        project_config={
            "review": {
                "url": "http://127.0.0.1:8080",
                "project": "rook",
                "reviewers": ["local", "ocr"],
            }
        },
    )
    resolved = EvoAgentConfig.from_app_config(config)
    assert resolved.url == "http://127.0.0.1:8080"
    assert resolved.project == "rook"
    assert resolved.reviewers == ("local", "ocr")


def test_review_cli_parser_and_dispatch() -> None:
    args = build_parser().parse_args(
        ["review", "run", "--target", "range", "--from", "main", "--to", "HEAD"]
    )
    assert args.command == "review"
    assert args.from_ref == "main"
    seen: list[object] = []

    def run_review(value) -> int:
        seen.append(value)
        return 0

    assert main(["review", "doctor"], review_runner=run_review) == 0
    assert seen[0].review_command == "doctor"
