"""项目记忆与恢复机会的显式用户审阅命令。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from rook_agent.app.command_actions import SwitchPageAction
from rook_agent.app.commands import CommandResult, ContentFormat
from rook_agent.evolution.learning import RecoveryReviewError, RecoveryReviewService
from rook_agent.evolution.memory import ProjectMemoryStore
from rook_agent.evolution.models import (
    LearningDestination,
    LearningSuggestion,
    RecoveryOpportunity,
    RecoveryOpportunityStatus,
    TaskTrace,
)
from rook_agent.evolution.recovery import RecoveryOpportunityStore
from rook_agent.evolution.trace import TaskTraceBuilder
from rook_agent.providers.base import ChatProvider


class CurrentLearningSession(Protocol):
    session: object


CandidateProposer = Callable[[TaskTrace], tuple[object, ...]]
_RUNTIME_ONLY_ERROR_CODES = frozenset(
    {
        "invalid_tool_arguments",
        "repeated_tool_failure",
        "unknown_tool",
    }
)


@dataclass(slots=True)
class LearningCommandHandler:
    """只有明确的 `/learn last|save|forge` 才会触发提炼或落盘。"""

    current_session: CurrentLearningSession
    opportunities: RecoveryOpportunityStore
    memory: ProjectMemoryStore
    provider_getter: Callable[[], ChatProvider]
    candidate_proposer: CandidateProposer
    reviews: dict[str, LearningSuggestion] = field(default_factory=dict)
    last_opportunity_id: str | None = None

    def handle(self, text: str) -> CommandResult:
        command = " ".join(text.strip().split())
        if command == "/memory":
            return CommandResult(handled=True, output=self._memory_status())
        if command.startswith("/remember"):
            _, _, rule = command.partition(" ")
            return CommandResult(handled=True, output=self._remember(rule))
        if command == "/learn":
            return CommandResult(handled=True, output=self._opportunity_status())
        if command == "/learn last":
            return self._review_last()
        if command.startswith("/learn save"):
            _, _, edited = command.partition("/learn save")
            return CommandResult(handled=True, output=self._save_last(edited.strip()))
        if command == "/learn forge":
            return CommandResult(handled=True, output=self._forge_last())
        if command == "/learn dismiss":
            return CommandResult(
                handled=True,
                output=self._transition_last(RecoveryOpportunityStatus.DISMISSED),
            )
        if command == "/learn defect":
            return CommandResult(
                handled=True,
                output=self._transition_last(RecoveryOpportunityStatus.RUNTIME_DEFECT),
            )
        if command.startswith("/learn"):
            return CommandResult(
                handled=True,
                output=(
                    "用法：/learn [last|save [edited rule]|forge|dismiss|defect]"
                ),
            )
        return CommandResult(handled=False)

    def _opportunity_status(self) -> str:
        opportunities = self.opportunities.list()
        if not opportunities:
            return "LEARN\n没有检测到已验证的恢复机会。"
        lines = ["LEARN · 恢复机会"]
        for item in opportunities[-20:]:
            lines.append(
                f"- {item.id} · {item.trigger_kind.value} · {item.status.value}"
            )
        lines.append("使用 /learn last 审阅最近机会；检测本身不会调用模型。")
        return "\n".join(lines)

    def _review_last(self) -> CommandResult:
        opportunity = self._latest_open_opportunity()
        if opportunity is None:
            return CommandResult(handled=True, output="没有可审阅的恢复机会。")
        existing = self.reviews.get(opportunity.id)
        if existing is not None:
            return _review_result(existing)
        trace = self._trace_for(opportunity)
        if trace is None:
            return CommandResult(
                handled=True,
                output="恢复机会的原始证据不可用，不能生成经验。",
            )
        try:
            suggestion = RecoveryReviewService(self.provider_getter()).review(
                opportunity,
                trace,
            )
        except RecoveryReviewError as error:
            return CommandResult(
                handled=True,
                output=f"经验审阅失败：{error.reason_code}；机会仍为 detected，可重试。",
            )
        self.reviews[opportunity.id] = suggestion
        self.last_opportunity_id = opportunity.id
        if opportunity.status is RecoveryOpportunityStatus.DETECTED:
            self.opportunities.transition(
                opportunity.id,
                RecoveryOpportunityStatus.REVIEWED,
            )
        return _review_result(suggestion)

    def _save_last(self, edited_rule: str) -> str:
        suggestion = self._last_review()
        if suggestion is None:
            return "请先使用 /learn last 审阅最近机会。"
        rule = edited_rule or "；".join(suggestion.recommended_action)
        opportunity = self.opportunities.get(suggestion.opportunity_id)
        if self._contains_runtime_only_failure(opportunity):
            return "工具协议错误不能保存为项目记忆；请使用 /learn defect。"
        try:
            record = self.memory.save_confirmed(
                rule=rule,
                triggers=suggestion.trigger_conditions,
                evidence_refs=tuple(
                    dict.fromkeys(
                        (*opportunity.evidence_refs, *opportunity.verification_refs)
                    )
                ),
            )
        except ValueError as error:
            return f"项目记忆未保存：{error}"
        self.opportunities.transition(
            opportunity.id,
            RecoveryOpportunityStatus.SAVED,
        )
        session = getattr(self.current_session, "session", None)
        if session is not None:
            session.project_memory_context = self.memory.render_context()
        return f"项目记忆已保存：{record.id} · v{record.version}"

    def _forge_last(self) -> str:
        suggestion = self._last_review()
        if suggestion is None:
            return "请先使用 /learn last 审阅最近机会。"
        if suggestion.destination is not LearningDestination.SKILL_CANDIDATE:
            return (
                f"该经验建议目标为 {suggestion.destination.value}，"
                "不能伪装成跨项目 Skill。"
            )
        opportunity = self.opportunities.get(suggestion.opportunity_id)
        if self._contains_runtime_only_failure(opportunity):
            return "工具协议错误不能生成 Skill Candidate；请使用 /learn defect。"
        trace = self._trace_for(opportunity)
        if trace is None:
            return "恢复机会的原始证据不可用，不能生成 Candidate。"
        candidates = self.candidate_proposer(trace)
        if not candidates:
            return "Rook Forge 未生成 Candidate；请查看 Forge 审计 reason code。"
        self.opportunities.transition(
            opportunity.id,
            RecoveryOpportunityStatus.SAVED,
        )
        descriptions = [
            f"{getattr(item, 'name', '-')}@v{getattr(item, 'version', '-')}"
            f":{getattr(getattr(item, 'status', None), 'value', '-')}"
            for item in candidates
        ]
        return "已送入 Rook Forge：" + ", ".join(descriptions)

    def _remember(self, rule: str) -> str:
        normalized = rule.strip()
        if not normalized:
            return "用法：/remember <rule>"
        try:
            record = self.memory.save_confirmed(
                rule=normalized,
                triggers=(normalized[:120],),
                evidence_refs=(),
            )
        except ValueError as error:
            return f"项目记忆未保存：{error}"
        session = getattr(self.current_session, "session", None)
        if session is not None:
            session.project_memory_context = self.memory.render_context()
        return f"项目记忆已保存：{record.id} · v{record.version}"

    def _memory_status(self) -> str:
        records = self.memory.list()
        if not records:
            return "PROJECT MEMORY\n没有已确认的项目记忆。"
        lines = ["PROJECT MEMORY"]
        for record in records[-20:]:
            lines.append(
                f"- {record.id} · v{record.version} · {record.status.value} · "
                f"{record.rule}"
            )
        return "\n".join(lines)

    def _transition_last(self, status: RecoveryOpportunityStatus) -> str:
        opportunity = self._latest_open_opportunity()
        if opportunity is None:
            return "没有可处理的恢复机会。"
        self.opportunities.transition(opportunity.id, status)
        self.last_opportunity_id = opportunity.id
        return f"恢复机会已标记为：{status.value}"

    def _latest_open_opportunity(self) -> RecoveryOpportunity | None:
        items = [
            item
            for item in self.opportunities.list()
            if item.status
            in {
                RecoveryOpportunityStatus.DETECTED,
                RecoveryOpportunityStatus.REVIEWED,
            }
        ]
        if not items:
            return None
        item = items[-1]
        self.last_opportunity_id = item.id
        return item

    def _last_review(self) -> LearningSuggestion | None:
        if self.last_opportunity_id is None:
            return None
        return self.reviews.get(self.last_opportunity_id)

    def _trace_for(self, opportunity: RecoveryOpportunity) -> TaskTrace | None:
        session = getattr(self.current_session, "session", None)
        store = getattr(session, "store", None)
        session_id = getattr(session, "session_id", None)
        if store is None or not isinstance(session_id, str):
            return None
        batch = TaskTraceBuilder().build(
            store.list_events(session_id),
            close_current=True,
        )
        required_event_ids = {
            ref.event_id
            for ref in (*opportunity.evidence_refs, *opportunity.verification_refs)
        }
        for trace in batch.completed:
            if required_event_ids.issubset(trace.event_ids):
                return trace
        return None

    def _contains_runtime_only_failure(
        self,
        opportunity: RecoveryOpportunity,
    ) -> bool:
        trace = self._trace_for(opportunity)
        if trace is None:
            return True
        evidence_refs = set(opportunity.evidence_refs)
        return any(
            item.ref in evidence_refs
            and str(item.data.get("error_code") or "") in _RUNTIME_ONLY_ERROR_CODES
            for item in trace.evidence
        )


def _render_suggestion(suggestion: LearningSuggestion) -> str:
    lines = [
        "# LEARN · RECOVERED FAILURE",
        "",
        f"**问题**：{suggestion.problem}",
        "",
        f"**触发条件**：{'; '.join(suggestion.trigger_conditions)}",
        "",
        "## 建议操作",
        "",
        *(f"- {item}" for item in suggestion.recommended_action),
        "",
        "## 验证方式",
        "",
        *(f"- {item}" for item in suggestion.verification),
        "",
        "## 风险与边界",
        "",
        *(f"- {item}" for item in suggestion.pitfalls),
        "",
        f"**建议去向**：`{suggestion.destination.value}`",
        "",
        "确认保存：`/learn save [edited rule]`  ",
        "送入 Forge：`/learn forge`  ",
        "忽略：`/learn dismiss`  ",
        "标记运行时缺陷：`/learn defect`",
    ]
    return "\n".join(lines)


def _review_result(suggestion: LearningSuggestion) -> CommandResult:
    content = _render_suggestion(suggestion)
    return CommandResult(
        handled=True,
        output=content,
        output_format=ContentFormat.MARKDOWN,
        action=SwitchPageAction(page="learn-review", content=content),
    )


__all__ = ["LearningCommandHandler"]
