"""Classify task traces from deterministic execution evidence."""

from __future__ import annotations

from rook_agent.agent.verification import is_successful_verification_result
from rook_agent.evolution.models import (
    EligibilityDecision,
    EvidenceItem,
    EvidenceSource,
    TaskTrace,
    TraceOutcome,
)
from rook_agent.evolution.trace import CONTROL_TOOLS
from rook_agent.tools.types import ToolResult


_MUTATION_TOOLS = frozenset({"write", "edit", "apply_patch", "delete"})
_DETERMINISTIC_STATE_READ_TOOLS = frozenset(
    {"git_diff", "git_status", "view", "grep", "glob", "tree", "read_multi", "ls"}
)
_CANCELLED_FINISH_REASONS = frozenset({"cancelled", "interrupted"})
_LIMIT_FINISH_REASONS = frozenset({"tool_round_limit", "provider_call_limit", "turn_timeout"})


class EvidenceClassifier:
    """Determine whether a trace has a trustworthy completion signal."""

    def evaluate(
        self,
        trace: TaskTrace,
        *,
        allow_soft_completion: bool = False,
    ) -> EligibilityDecision:
        terminal_reason = _latest_finish_reason(trace.evidence)
        if terminal_reason in _CANCELLED_FINISH_REASONS:
            return _decision(False, TraceOutcome.CANCELLED, "cancelled")
        if terminal_reason in _LIMIT_FINISH_REASONS:
            return _decision(False, TraceOutcome.CANCELLED, "tool_limit_reached")

        latest_todos = _latest_successful_todos(trace.evidence)
        if latest_todos is not None and any(
            item.get("status") in {"pending", "in_progress"} for item in latest_todos
        ):
            return _decision(False, TraceOutcome.UNKNOWN, "unfinished_todo")

        result_items = tuple(item for item in trace.evidence if item.tool_name is not None and item.ok is not None)
        informative_items = tuple(item for item in result_items if item.tool_name not in CONTROL_TOOLS)
        if not informative_items:
            reason_code = "control_only" if result_items else "no_informative_result"
            return _decision(False, TraceOutcome.UNKNOWN, reason_code)

        verifier_index = _last_successful_verifier_index(trace.evidence)
        if verifier_index is not None:
            recovered = any(
                item.ok is False and item.tool_name not in CONTROL_TOOLS
                for item in trace.evidence[:verifier_index]
            )
            if recovered:
                return _decision(True, TraceOutcome.RECOVERED_FAILURE, "recovered_and_verified")
            return _decision(True, TraceOutcome.VERIFIED_SUCCESS, "verified_success")

        if _has_post_mutation_state_proof(trace.evidence):
            return _decision(True, TraceOutcome.STATE_VERIFIED_SUCCESS, "state_verified_success")

        has_successful_result = any(item.ok is True for item in informative_items)
        soft_completion = trace.is_closed and bool(trace.final_answer.strip()) and has_successful_result
        if soft_completion:
            if allow_soft_completion:
                return _decision(
                    True,
                    TraceOutcome.COMPLETED_WITHOUT_VERIFIER,
                    "completed_without_verifier",
                )
            return _decision(
                False,
                TraceOutcome.COMPLETED_WITHOUT_VERIFIER,
                "soft_completion_disabled",
            )

        if any(item.ok is False for item in informative_items) and not has_successful_result:
            return _decision(False, TraceOutcome.FAILED, "failed")
        return _decision(False, TraceOutcome.UNKNOWN, "unknown")


def _latest_finish_reason(evidence: tuple[EvidenceItem, ...]) -> str | None:
    latest: str | None = None
    for item in evidence:
        if item.source != EvidenceSource.MODEL_STATEMENT or item.tool_name is not None:
            continue
        finish_reason = item.data.get("finish_reason")
        if isinstance(finish_reason, str):
            latest = finish_reason
    return latest


def _latest_successful_todos(evidence: tuple[EvidenceItem, ...]) -> list[dict[str, object]] | None:
    latest: list[dict[str, object]] | None = None
    for item in evidence:
        if item.tool_name != "todo" or item.ok is not True:
            continue
        todos = item.data.get("todos")
        if isinstance(todos, list):
            latest = [todo for todo in todos if isinstance(todo, dict)]
    return latest


def _last_successful_verifier_index(evidence: tuple[EvidenceItem, ...]) -> int | None:
    latest: int | None = None
    for index, item in enumerate(evidence):
        if item.source != EvidenceSource.LOCAL_EXECUTION or item.tool_name is None or item.ok is None:
            continue
        result = ToolResult(
            name=item.tool_name,
            ok=item.ok,
            content=item.content,
            data=item.data,
        )
        if is_successful_verification_result(item.tool_name, result):
            latest = index
    return latest


def _has_post_mutation_state_proof(evidence: tuple[EvidenceItem, ...]) -> bool:
    mutation_seen = False
    for item in evidence:
        if item.source != EvidenceSource.WORKSPACE_STATE or item.ok is not True:
            continue
        if item.tool_name in _MUTATION_TOOLS:
            mutation_seen = True
            continue
        if mutation_seen and item.tool_name in _DETERMINISTIC_STATE_READ_TOOLS:
            return True
    return False


def _decision(eligible: bool, outcome: TraceOutcome, reason_code: str) -> EligibilityDecision:
    return EligibilityDecision(eligible=eligible, outcome=outcome, reason_code=reason_code)
