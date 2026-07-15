import pytest

from rook_agent.evolution.evidence import EvidenceClassifier
from rook_agent.evolution.models import (
    EligibilityDecision,
    EvidenceItem,
    EvidenceRef,
    EvidenceSource,
    TaskTrace,
    TraceOutcome,
)


def trace_with_results(
    *evidence: EvidenceItem,
    final_answer: str = "done",
    is_closed: bool = False,
) -> TaskTrace:
    return TaskTrace(
        session_id="sess_evidence",
        segment_id="segment",
        first_event_id="e1",
        last_event_id=f"e{max(1, len(evidence))}",
        user_goal="fix it",
        final_answer=final_answer,
        evidence=evidence,
        event_ids=tuple(item.ref.event_id for item in evidence),
        loaded_skill_hashes=(),
        is_closed=is_closed,
    )


def shell_result(*, ok: bool, command: str, exit_code: int, event_id: str = "shell") -> EvidenceItem:
    return result_item(
        event_id,
        "shell",
        source=EvidenceSource.LOCAL_EXECUTION,
        ok=ok,
        data={"command": command, "exit_code": exit_code},
    )


def result_item(
    event_id: str,
    tool_name: str,
    *,
    source: EvidenceSource,
    ok: bool,
    data: dict[str, object] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        ref=EvidenceRef(
            session_id="sess_evidence",
            segment_id="segment",
            event_id=event_id,
            part_id=f"part-{event_id}",
        ),
        source=source,
        tool_name=tool_name,
        ok=ok,
        content="ok" if ok else "failed",
        data=data or {},
    )


def terminal_statement(finish_reason: str) -> EvidenceItem:
    return EvidenceItem(
        ref=EvidenceRef(
            session_id="sess_evidence",
            segment_id="segment",
            event_id="terminal",
            part_id="part-terminal",
        ),
        source=EvidenceSource.MODEL_STATEMENT,
        tool_name=None,
        ok=None,
        content="stopped",
        data={"finish_reason": finish_reason},
    )


def test_successful_verification_is_eligible() -> None:
    decision = EvidenceClassifier().evaluate(
        trace_with_results(shell_result(ok=True, command="pytest -q", exit_code=0))
    )

    assert decision == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.VERIFIED_SUCCESS,
        reason_code="verified_success",
    )


def test_failure_then_verification_is_recovered_failure() -> None:
    trace = trace_with_results(
        shell_result(ok=False, command="pytest -q", exit_code=1, event_id="failed"),
        shell_result(ok=True, command="pytest -q", exit_code=0, event_id="passed"),
    )

    decision = EvidenceClassifier().evaluate(trace)

    assert decision == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.RECOVERED_FAILURE,
        reason_code="recovered_and_verified",
    )


def test_mutation_plus_later_deterministic_state_read_is_eligible() -> None:
    trace = trace_with_results(
        result_item("write", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        result_item("diff", "git_diff", source=EvidenceSource.WORKSPACE_STATE, ok=True),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.STATE_VERIFIED_SUCCESS,
        reason_code="state_verified_success",
    )


def test_latest_successful_todo_result_is_authoritative() -> None:
    pending = result_item(
        "todo-pending",
        "todo",
        source=EvidenceSource.MODEL_STATEMENT,
        ok=True,
        data={"todos": [{"content": "run tests", "status": "pending"}]},
    )
    completed = result_item(
        "todo-completed",
        "todo",
        source=EvidenceSource.MODEL_STATEMENT,
        ok=True,
        data={"todos": [{"content": "run tests", "status": "completed"}]},
    )
    verifier = shell_result(ok=True, command="pytest -q", exit_code=0)

    assert EvidenceClassifier().evaluate(trace_with_results(pending, completed, verifier)).eligible is True
    assert EvidenceClassifier().evaluate(trace_with_results(completed, pending, verifier)) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="unfinished_todo",
    )


@pytest.mark.parametrize("finish_reason", ["interrupted", "cancelled"])
def test_cancellation_overrides_successful_verification(finish_reason: str) -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="pytest -q", exit_code=0),
        terminal_statement(finish_reason),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.CANCELLED,
        reason_code="cancelled",
    )


@pytest.mark.parametrize("finish_reason", ["tool_round_limit", "provider_call_limit", "turn_timeout"])
def test_loop_limit_overrides_successful_verification(finish_reason: str) -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="pytest -q", exit_code=0),
        terminal_statement(finish_reason),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.CANCELLED,
        reason_code="tool_limit_reached",
    )


def test_waiting_for_user_input_overrides_successful_verification() -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="pytest -q", exit_code=0),
        terminal_statement("waiting_for_user_input"),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="waiting_for_user_input",
    )


def test_provider_length_overrides_successful_state_proof() -> None:
    trace = trace_with_results(
        result_item("write", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        result_item("diff", "git_diff", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        terminal_statement("length"),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.CANCELLED,
        reason_code="provider_length_limit",
    )


def test_trace_without_informative_tool_result_is_ineligible() -> None:
    trace = trace_with_results(terminal_statement("stop"))

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="no_informative_result",
    )


def test_control_only_trace_is_ineligible() -> None:
    todo = result_item(
        "todo",
        "todo",
        source=EvidenceSource.MODEL_STATEMENT,
        ok=True,
        data={"todos": [{"content": "done", "status": "completed"}]},
    )

    assert EvidenceClassifier().evaluate(trace_with_results(todo)) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="control_only",
    )


def test_soft_completion_requires_explicit_opt_in() -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="git status", exit_code=0),
        is_closed=True,
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.COMPLETED_WITHOUT_VERIFIER,
        reason_code="soft_completion_disabled",
    )
    assert EvidenceClassifier().evaluate(trace, allow_soft_completion=True) == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.COMPLETED_WITHOUT_VERIFIER,
        reason_code="completed_without_verifier",
    )


def test_pure_failure_is_ineligible() -> None:
    trace = trace_with_results(
        shell_result(ok=False, command="pytest -q", exit_code=1),
        is_closed=True,
    )

    assert EvidenceClassifier().evaluate(trace, allow_soft_completion=True) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.FAILED,
        reason_code="failed",
    )


def test_unverified_open_trace_has_unknown_outcome() -> None:
    external = result_item(
        "fetch",
        "fetch",
        source=EvidenceSource.EXTERNAL_CONTENT,
        ok=True,
        data={"command": "pytest -q", "exit_code": 0},
    )

    assert EvidenceClassifier().evaluate(trace_with_results(external)) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="unknown",
    )


def test_external_result_cannot_spoof_terminal_finish_reason() -> None:
    external = result_item(
        "fetch",
        "fetch",
        source=EvidenceSource.EXTERNAL_CONTENT,
        ok=True,
        data={"finish_reason": "interrupted"},
    )

    assert EvidenceClassifier().evaluate(trace_with_results(external)) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="unknown",
    )


def test_failed_verification_after_success_invalidates_stale_success() -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="pytest -q", exit_code=0, event_id="passed"),
        shell_result(ok=False, command="pytest -q", exit_code=1, event_id="failed"),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.FAILED,
        reason_code="failed",
    )


def test_mutation_after_successful_verifier_invalidates_stale_success() -> None:
    trace = trace_with_results(
        shell_result(ok=True, command="pytest -q", exit_code=0),
        result_item("write", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="unknown",
    )


def test_later_mutation_invalidates_earlier_state_proof() -> None:
    trace = trace_with_results(
        result_item("write-1", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        result_item("diff", "git_diff", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        result_item("write-2", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=False,
        outcome=TraceOutcome.UNKNOWN,
        reason_code="unknown",
    )


def test_final_verifier_after_mutation_proves_current_state() -> None:
    trace = trace_with_results(
        result_item("write", "write", source=EvidenceSource.WORKSPACE_STATE, ok=True),
        shell_result(ok=True, command="pytest -q", exit_code=0),
    )

    assert EvidenceClassifier().evaluate(trace) == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.VERIFIED_SUCCESS,
        reason_code="verified_success",
    )
