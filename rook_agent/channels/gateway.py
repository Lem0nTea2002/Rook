"""Authenticated, idempotent mobile-channel orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Protocol

from rook_agent.channels.base import ChannelAdapter
from rook_agent.channels.config import ChannelConfig
from rook_agent.channels.models import ChannelKind, InboundMessage, ProjectBinding
from rook_agent.channels.state import ChannelStateStore
from rook_agent.execution.queue import SQLiteJobQueue


logger = logging.getLogger(__name__)


class ChannelRunner(Protocol):
    last_display_lines: list[str]
    last_pending_input: object | None

    async def arun_user_turn(self, content: str) -> Any: ...

    async def aresume_with_user_input(self, request_id: str, answer: str) -> Any: ...

    def cancel_current_turn(self) -> None: ...


RuntimeFactory = Callable[[ProjectBinding, str], ChannelRunner]


class ChannelGateway:
    def __init__(
        self,
        *,
        adapters: Mapping[ChannelKind, ChannelAdapter],
        state: ChannelStateStore,
        config: ChannelConfig,
        runtime_factory: RuntimeFactory,
        queue_path: str | Path,
        max_concurrency: int = 2,
    ) -> None:
        if max_concurrency < 1 or max_concurrency > 2:
            raise ValueError("channel concurrency must be within 1-2")
        self.adapters = dict(adapters)
        self.state = state
        self.config = config
        self.runtime_factory = runtime_factory
        self.queue = SQLiteJobQueue(queue_path)
        self.max_concurrency = max_concurrency
        self._global_slots = asyncio.Semaphore(max_concurrency)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._project_locks: dict[str, asyncio.Lock] = {}
        self._runtimes: dict[str, ChannelRunner] = {}
        self._closed = False

    async def accept(self, message: InboundMessage) -> None:
        adapter = self._adapter(message.channel)
        if not self.state.claim_message(message):
            return
        if message.text.strip().lower().startswith("/pair"):
            await self._pair(message, adapter)
            return
        identity = self.state.binding_for(message)
        if identity is None:
            await adapter.send(
                message.conversation_id,
                "此账号尚未配对。请先在电脑执行 `rook channel pair create`，再发送 `/pair 六位码`。",
                reply_to=message.message_id,
                context_token=message.context_token,
            )
            return
        binding = self.config.projects.get(identity.project_alias)
        if binding is None:
            await adapter.send(
                message.conversation_id,
                "当前项目已从本机白名单移除，请重新配对。",
                reply_to=message.message_id,
                context_token=message.context_token,
            )
            return
        command_handled = await self._handle_command(message, binding, adapter)
        if command_handled:
            return
        session_id = self._session_id(message, binding.alias)
        job = self.queue.enqueue(
            idempotency_key=(
                f"channel:{message.channel.value}:{message.account_id}:"
                f"{message.user_id}:{message.message_id}"
            ),
            payload={
                "channel": message.channel.value,
                "account_id": message.account_id,
                "user_id": message.user_id,
                "conversation_id": message.conversation_id,
                "message_id": message.message_id,
                "text": message.text,
                "context_token": message.context_token,
                "project_alias": binding.alias,
                "session_id": session_id,
            },
        )
        await adapter.send(
            message.conversation_id,
            f"任务已进入队列：{job.job_id}",
            reply_to=message.message_id,
            context_token=message.context_token,
        )

    async def run_pending_once(self, *, owner: str = "channel-worker") -> bool:
        job = await asyncio.to_thread(self.queue.claim, owner=owner, lease_seconds=300)
        if job is None:
            return False
        payload = dict(job.payload)
        failure_reason_code = "channel_execution_failed"
        try:
            channel = ChannelKind(payload["channel"])
            adapter = self._adapter(channel)
            binding = self.config.projects[str(payload["project_alias"])]
            session_id = str(payload["session_id"])
            async with self._global_slots:
                lock = self._session_locks.setdefault(session_id, asyncio.Lock())
                async with lock:
                    project_lock = self._project_locks.setdefault(
                        binding.alias,
                        asyncio.Lock(),
                    )
                    async with project_lock:
                        await adapter.set_typing(
                            str(payload["conversation_id"]),
                            True,
                            context_token=_optional_str(payload.get("context_token")),
                        )
                        try:
                            response = await self._run_task(payload, binding, session_id)
                        finally:
                            await adapter.set_typing(
                                str(payload["conversation_id"]),
                                False,
                                context_token=_optional_str(payload.get("context_token")),
                            )
            failure_reason_code = "channel_delivery_failed"
            if response is not None:
                await adapter.send(
                    str(payload["conversation_id"]),
                    response,
                    reply_to=str(payload["message_id"]),
                    context_token=_optional_str(payload.get("context_token")),
                )
            await asyncio.to_thread(
                self.queue.complete,
                job.job_id,
                owner=owner,
                result={"status": "sent"},
            )
        except Exception:
            await asyncio.to_thread(
                self.queue.fail,
                job.job_id,
                owner=owner,
                reason_code=failure_reason_code,
                retryable=False,
            )
            logger.error(
                "channel task failed reason_code=%s",
                failure_reason_code,
                extra={"job_id": job.job_id, "reason_code": failure_reason_code},
            )
            try:
                adapter = self._adapter(ChannelKind(payload["channel"]))
                await adapter.send(
                    str(payload["conversation_id"]),
                    "任务执行失败，已安全停止。请在电脑查看脱敏日志后重试。",
                    reply_to=str(payload["message_id"]),
                    context_token=_optional_str(payload.get("context_token")),
                )
            except Exception:
                logger.warning(
                    "failed to deliver channel task failure notice",
                    extra={"job_id": job.job_id},
                )
        return True

    async def run_workers(self, *, poll_seconds: float = 0.25) -> None:
        async def worker(index: int) -> None:
            owner = f"channel-worker-{index}"
            last_approval_recovery = 0.0
            while not self._closed:
                now = asyncio.get_running_loop().time()
                if index == 0 and now - last_approval_recovery >= 1.0:
                    await self.recover_expired_approvals()
                    last_approval_recovery = now
                worked = await self.run_pending_once(owner=owner)
                if not worked:
                    await asyncio.sleep(poll_seconds)

        await asyncio.gather(*(worker(index) for index in range(self.max_concurrency)))

    async def recover_expired_approvals(self) -> int:
        expired = await asyncio.to_thread(self.state.expire_pending_approvals)
        for approval in expired:
            binding = self.config.projects.get(approval.project_alias)
            adapter = self.adapters.get(approval.channel)
            if binding is None or adapter is None:
                continue
            runtime = self._runtimes.get(approval.session_id)
            if runtime is None:
                runtime = self.runtime_factory(binding, approval.session_id)
                self._runtimes[approval.session_id] = runtime
            try:
                async with self._global_slots:
                    session_lock = self._session_locks.setdefault(
                        approval.session_id,
                        asyncio.Lock(),
                    )
                    async with session_lock:
                        project_lock = self._project_locks.setdefault(
                            binding.alias,
                            asyncio.Lock(),
                        )
                        async with project_lock:
                            await runtime.aresume_with_user_input(
                                approval.request_id,
                                "deny",
                            )
            except Exception:
                # The durable approval remains expired. A later explicit task
                # can surface the already-safe session recovery failure.
                continue
            await adapter.send(
                approval.conversation_id,
                "审批已过期，Rook 已按拒绝处理，敏感工具没有执行。",
                context_token=approval.context_token,
            )
        return len(expired)

    async def serve(self) -> None:
        adapter_tasks = [
            asyncio.create_task(adapter.run(self.accept))
            for adapter in self.adapters.values()
        ]
        worker_task = asyncio.create_task(self.run_workers())
        try:
            await asyncio.gather(*adapter_tasks, worker_task)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(adapter.close() for adapter in self.adapters.values()))
        self.queue.close()

    async def _pair(self, message: InboundMessage, adapter: ChannelAdapter) -> None:
        parts = message.text.strip().split()
        if len(parts) != 2:
            await adapter.send(
                message.conversation_id,
                "用法：/pair 六位码",
                reply_to=message.message_id,
                context_token=message.context_token,
            )
            return
        identity = self.state.consume_pair_code(parts[1], message)
        if identity is None or identity.project_alias not in self.config.projects:
            await adapter.send(
                message.conversation_id,
                "配对码无效、已使用或已过期。",
                reply_to=message.message_id,
                context_token=message.context_token,
            )
            return
        await adapter.send(
            message.conversation_id,
            f"配对成功。当前项目：{identity.project_alias}",
            reply_to=message.message_id,
            context_token=message.context_token,
        )

    async def _handle_command(
        self,
        message: InboundMessage,
        binding: ProjectBinding,
        adapter: ChannelAdapter,
    ) -> bool:
        text = message.text.strip()
        if not text.startswith("/"):
            return False
        name, _, argument = text.partition(" ")
        command = name.lower()
        response: str | None = None
        if command == "/help":
            response = (
                "可用命令：/projects /project <alias> /new /status /diff "
                "/transcript /cancel /approve <code> /deny <code>"
            )
        elif command == "/projects":
            response = "项目白名单：" + ", ".join(sorted(self.config.projects))
        elif command == "/project":
            alias = argument.strip()
            if alias not in self.config.projects:
                response = "项目不在白名单中。"
            else:
                self.state.set_project(message, alias)
                response = f"已切换项目：{alias}"
        elif command == "/new":
            generation = self.state.new_session(message, binding.alias)
            response = f"已创建新会话（generation {generation}）。"
        elif command == "/status":
            session_id = self._session_id(message, binding.alias)
            pending = self.state.pending_approval(
                channel=message.channel,
                account_id=message.account_id,
                user_id=message.user_id,
                conversation_id=message.conversation_id,
            )
            response = (
                f"channel={message.channel.value}\nproject={binding.alias}\n"
                f"session={session_id}\npending_approval={'yes' if pending else 'no'}"
            )
        elif command == "/cancel":
            session_id = self._session_id(message, binding.alias)
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                response = "当前没有运行中的任务。"
            else:
                runtime.cancel_current_turn()
                response = "已请求取消当前任务。"
        elif command in {"/approve", "/deny"}:
            return await self._resolve_approval(
                message,
                binding,
                adapter,
                code=argument.strip(),
                allow=command == "/approve",
            )
        elif command == "/diff":
            response = await asyncio.to_thread(_safe_git_diff, binding.path)
        elif command == "/transcript":
            session_id = self._session_id(message, binding.alias)
            runtime = self._runtimes.get(session_id)
            lines = runtime.last_display_lines[-20:] if runtime is not None else []
            response = "\n".join(lines) if lines else "当前会话暂无可见输出。"
        else:
            response = "未知命令。发送 /help 查看允许的远程命令。"
        await adapter.send(
            message.conversation_id,
            _bounded_redacted(response),
            reply_to=message.message_id,
            context_token=message.context_token,
        )
        return True

    async def _run_task(
        self,
        payload: dict[str, Any],
        binding: ProjectBinding,
        session_id: str,
    ) -> str | None:
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            runtime = self.runtime_factory(binding, session_id)
            self._runtimes[session_id] = runtime
        response = await runtime.arun_user_turn(str(payload["text"]))
        pending = runtime.last_pending_input
        if pending is not None and getattr(pending, "kind", "") == "permission_confirmation":
            inbound = InboundMessage(
                channel=ChannelKind(payload["channel"]),
                account_id=str(payload["account_id"]),
                user_id=str(payload["user_id"]),
                conversation_id=str(payload["conversation_id"]),
                message_id=str(payload["message_id"]),
                text=str(payload["text"]),
                received_at=self.state.clock(),
                context_token=_optional_str(payload.get("context_token")),
            )
            details = _pending_details(pending)
            _, code = self.state.create_approval(
                message=inbound,
                project_alias=binding.alias,
                session_id=session_id,
                request_id=str(getattr(pending, "id")),
                tool_name=details["tool_name"],
                action=details["action"],
                target=details["target"],
                action_hash=details["action_hash"],
            )
            adapter = self._adapter(ChannelKind(payload["channel"]))
            approval_sender = getattr(adapter, "send_approval", None)
            if callable(approval_sender):
                await approval_sender(
                    str(payload["conversation_id"]),
                    code=code,
                    tool_name=details["tool_name"],
                    action=details["action"],
                    target=details["target"],
                )
                return None
            return (
                "Rook 已暂停敏感操作。\n"
                f"工具：{details['tool_name']}\n动作：{details['action']}\n"
                f"目标：{details['target']}\n"
                f"5 分钟内发送 `/approve {code}` 或 `/deny {code}`。仅本次有效。"
            )
        visible = "\n".join(runtime.last_display_lines).strip()
        return _bounded_redacted(visible or str(getattr(response, "content", "")))

    async def _resolve_approval(
        self,
        message: InboundMessage,
        binding: ProjectBinding,
        adapter: ChannelAdapter,
        *,
        code: str,
        allow: bool,
    ) -> bool:
        if not re.fullmatch(r"\d{6}", code):
            response = "审批码必须是 6 位数字。"
        else:
            candidate = self.state.pending_approval(
                channel=message.channel,
                account_id=message.account_id,
                user_id=message.user_id,
                conversation_id=message.conversation_id,
            )
            if candidate is None:
                response = "审批码无效、已过期或尝试次数过多。"
            elif candidate.project_alias != binding.alias:
                self.state.resolve_approval(
                    message=message,
                    code=code,
                    allow=False,
                    expected_action_hash="project_mismatch",
                )
                response = "审批上下文与当前项目不一致，已拒绝。"
            else:
                runtime = self._runtimes.get(candidate.session_id)
                if runtime is None:
                    runtime = self.runtime_factory(binding, candidate.session_id)
                    self._runtimes[candidate.session_id] = runtime
                pending = runtime.last_pending_input
                if pending is not None:
                    current = _pending_details(pending)
                    matches = (
                        str(getattr(pending, "id", "")) == candidate.request_id
                        and current["action_hash"] == candidate.action_hash
                    )
                else:
                    matches = False
                approval = self.state.resolve_approval(
                    message=message,
                    code=code,
                    allow=allow,
                    expected_action_hash=(
                        current["action_hash"] if pending is not None else "missing"
                    ),
                )
                if approval is None:
                    response = "审批码无效、已过期或尝试次数过多。"
                else:
                    answer = "allow_once" if allow and matches else "deny"
                    async with self._global_slots:
                        session_lock = self._session_locks.setdefault(
                            approval.session_id,
                            asyncio.Lock(),
                        )
                        async with session_lock:
                            project_lock = self._project_locks.setdefault(
                                binding.alias,
                                asyncio.Lock(),
                            )
                            async with project_lock:
                                resumed = await runtime.aresume_with_user_input(
                                    approval.request_id,
                                    answer,
                                )
                    visible = "\n".join(runtime.last_display_lines).strip()
                    response = _bounded_redacted(
                        visible or str(getattr(resumed, "content", "审批已处理。"))
                    )
        await adapter.send(
            message.conversation_id,
            response,
            reply_to=message.message_id,
            context_token=message.context_token,
        )
        return True

    def _session_id(self, message: InboundMessage, project_alias: str) -> str:
        generation = self.state.session_generation(message, project_alias)
        raw = (
            f"{message.channel.value}\0{message.account_id}\0{message.user_id}\0"
            f"{project_alias}\0{generation}"
        )
        return "channel_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _adapter(self, channel: ChannelKind) -> ChannelAdapter:
        try:
            return self.adapters[channel]
        except KeyError as exc:
            raise ValueError(f"channel adapter is not configured: {channel}") from exc


def _pending_details(pending: object) -> dict[str, str]:
    payload = getattr(pending, "payload", {})
    if not isinstance(payload, dict):
        payload = {}
    request = payload.get("permission_request")
    if not isinstance(request, dict):
        request = {}
    tool_name = str(payload.get("tool_name") or "unknown")
    action = str(request.get("action") or "sensitive_action")
    target = str(request.get("target") or "not disclosed")
    stable = json.dumps(
        {
            "request_id": str(getattr(pending, "id", "")),
            "tool_name": tool_name,
            "action": action,
            "target": target,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "tool_name": tool_name,
        "action": action,
        "target": _bounded_redacted(target, max_chars=500),
        "action_hash": hashlib.sha256(stable.encode("utf-8")).hexdigest(),
    }


def _safe_git_diff(project_root: Path) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "diff", "--stat", "--", "."],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        return "无法读取 Git diff。"
    return result.stdout.strip() or "工作树没有未提交差异。"


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|gh[opsu])_[A-Za-z0-9_-]{12,}\b"),
)


def _bounded_redacted(text: str, *, max_chars: int = 8_000) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if len(redacted) <= max_chars:
        return redacted
    return redacted[: max_chars - 20] + "\n…[output truncated]"


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["ChannelGateway", "ChannelRunner", "RuntimeFactory"]
