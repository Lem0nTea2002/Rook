"""`rook review` 命令。"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
from typing import Callable

from rook_agent.agent.session import create_project_permission_manager
from rook_agent.config.credentials import write_secret
from rook_agent.config.settings import AppConfig, load_config
from rook_agent.permissions.grants import FilePermissionGrantStore
from rook_agent.permissions.types import (
    PermissionAction,
    PermissionDecisionKind,
    PermissionMode,
    PermissionRequest,
)
from rook_agent.review.client import (
    EvoAgentClient,
    EvoAgentClientError,
    EvoAgentConfig,
    credential_name,
    render_review_task,
)


def run_review_command(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str] = input,
    password_fn: Callable[[str], str] = getpass.getpass,
) -> int:
    project_root = Path(args.project).resolve()
    app_config = load_config(project_root=project_root)
    config = EvoAgentConfig.from_app_config(app_config)
    if args.review_command == "login":
        username = str(args.username)
        password = password_fn(f"EvoAgent password for {username}: ")
        if not password:
            raise ValueError("EvoAgent password must not be empty")
        write_secret(credential_name(config.url, username), password)
        print(f"Stored EvoAgent credential for {username} in the system keyring.")
        return 0

    _require_network_permission(project_root, app_config, config.url, input_fn=input_fn)
    client = EvoAgentClient(config)
    if args.review_command == "doctor":
        result = client.doctor()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("service") == "ok" else 1
    if args.review_command == "run":
        result = client.submit(
            target=args.target,
            from_ref=args.from_ref,
            to_ref=args.to_ref,
            commit=args.commit,
            reviewers=_reviewers(args.reviewers) if args.reviewers else None,
        )
        print(f"Review submitted: {result.get('task_id', '-')} ({result.get('state', 'UNKNOWN')})")
        return 0
    if args.review_command == "report":
        task = client.task(args.task_id)
        print(render_review_task(task))
        return 0 if task.get("state") == "SUCCESS" else 1
    raise ValueError("unknown review command")


def _require_network_permission(
    project_root: Path,
    app_config: AppConfig,
    url: str,
    *,
    input_fn: Callable[[str], str],
) -> None:
    raw_mode = str(app_config.get_section_value("permissions", "mode", default="ask"))
    mode = PermissionMode(raw_mode)
    if mode is PermissionMode.FULL:
        mode = PermissionMode.ASK
    manager = create_project_permission_manager(
        project_root,
        grants=FilePermissionGrantStore(project_root / ".rook" / "permissions.json"),
        mode=mode,
    )
    request = PermissionRequest(
        id="review_cli_network",
        action=PermissionAction.NETWORK_REQUEST,
        target=url,
        reason="向 EvoAgent 提交只读代码审阅或读取审阅报告。",
    )
    decision = manager.preflight(request)
    if decision.kind is PermissionDecisionKind.ALLOW:
        return
    if decision.kind is PermissionDecisionKind.DENY:
        raise PermissionError(decision.reason)
    answer = input_fn(f"Allow network request to {url}? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        raise PermissionError("用户拒绝了 EvoAgent 网络请求。")
    resolved = manager.resolve_confirmation(request, "allow_once")
    if resolved.kind is not PermissionDecisionKind.ALLOW:
        raise PermissionError(resolved.reason)


def _reviewers(value: str) -> tuple[str, ...]:
    reviewers = tuple(item.strip() for item in value.split(",") if item.strip())
    if not reviewers:
        raise ValueError("reviewers must not be empty")
    return reviewers


def main_review_command(args: argparse.Namespace) -> int:
    try:
        return run_review_command(args)
    except (EvoAgentClientError, ValueError, PermissionError) as exc:
        code = getattr(exc, "code", "review_error")
        print(f"error [{code}]: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1


__all__ = ["main_review_command", "run_review_command"]
