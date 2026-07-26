"""Sequential, auditable orchestration of paired EvalOps experiments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import threading
import uuid

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.adapters.base import AgentAdapter
from rook_agent.evalops.artifacts import ArtifactStore
from rook_agent.evalops.evaluators.factory import EvaluatorFactory
from rook_agent.evalops.models import (
    AgentRun,
    AgentTarget,
    AgentType,
    EvalCase,
    EvalSuite,
    EvaluatedRun,
    EvaluationResult,
    EvaluationStatus,
    ExperimentPhase,
    ExperimentPlan,
    ExperimentRecord,
    RunSpec,
    RunStatus,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
    plain_data,
)
from rook_agent.evalops.skills import SkillMaterializer
from rook_agent.evalops.workspace import WorkspaceManager, WorkspacePair, hash_workspace


_ROUTING_RELEVANT_CATEGORIES = frozenset({"direct", "transfer"})
_NO_EVALUATION_STATUSES = frozenset(
    {
        RunStatus.ADAPTER_UNAVAILABLE,
        RunStatus.AUTH_FAILED,
        RunStatus.VERSION_UNSUPPORTED,
        RunStatus.INFRA_ERROR,
        RunStatus.ADAPTER_ERROR,
        RunStatus.USER_CANCELLED,
    }
)
_CONSTRAINT_STATUSES = frozenset(
    {RunStatus.TIMEOUT, RunStatus.TURN_LIMIT, RunStatus.BUDGET_EXHAUSTED}
)
_INFRASTRUCTURE_EXCLUSION_STATUSES = _NO_EVALUATION_STATUSES - {
    RunStatus.USER_CANCELLED
}


def select_fast_cases(suite: EvalSuite, *, count_per_category: int = 1) -> tuple[EvalCase, ...]:
    """Select a stable, bounded subset from every represented category."""

    if isinstance(count_per_category, bool) or not isinstance(count_per_category, int) or count_per_category <= 0:
        raise ValueError("count_per_category must be a positive integer")
    selected: list[EvalCase] = []
    categories = sorted({case.category for case in suite.cases}, key=lambda item: item.value)
    for category in categories:
        category_cases = sorted(
            (case for case in suite.cases if case.category is category),
            key=lambda case: case.id,
        )
        selected.extend(category_cases[:count_per_category])
    return tuple(selected)


def select_full_cases(suite: EvalSuite) -> tuple[EvalCase, ...]:
    """Return the complete manifest order for a Full Gate."""

    return suite.cases


def build_experiment_plan(
    suite: EvalSuite,
    *,
    targets: Sequence[AgentTarget],
    candidate: SkillCandidate,
    repetitions: int = 1,
    phase: ExperimentPhase = ExperimentPhase.FULL,
    families: Sequence[TreatmentFamily] | None = None,
    fast_count_per_category: int = 1,
    turn_limit: int | None = None,
    budget_limit: Decimal | None = None,
    environment_allowlist: Mapping[str, str] | None = None,
    permission_profile: str = "isolated",
) -> ExperimentPlan:
    """Build separately auditable content and routing A/B pairs."""

    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions <= 0:
        raise ValueError("repetitions must be a positive integer")
    if (
        suite.candidate_content_hash is not None
        and candidate.content_hash != suite.candidate_content_hash
    ):
        raise ValueError("Candidate does not match the suite's sealed Candidate content hash")
    if not targets:
        raise ValueError("at least one Agent target is required")
    if not isinstance(phase, ExperimentPhase):
        raise ValueError(f"unsupported experiment phase: {phase!r}")
    selected_families = tuple(TreatmentFamily) if families is None else tuple(families)
    if not selected_families:
        raise ValueError("at least one treatment family is required")
    if any(not isinstance(family, TreatmentFamily) for family in selected_families):
        raise ValueError("families must contain only TreatmentFamily values")
    if len(selected_families) != len(set(selected_families)):
        raise ValueError("treatment families must be unique")
    safe_environment = dict(environment_allowlist or {})
    experiment_id = f"exp-{uuid.uuid4().hex}"
    cases = (
        select_fast_cases(suite, count_per_category=fast_count_per_category)
        if phase is ExperimentPhase.FAST
        else select_full_cases(suite)
    )
    runs: list[RunSpec] = []
    for target in targets:
        for case in cases:
            fixture_hash = hash_workspace(case.fixture)
            routing_relevant = case.category.value in _ROUTING_RELEVANT_CATEGORIES
            for family in selected_families:
                candidate_treatment = (
                    Treatment.FORCED_SKILL
                    if family is TreatmentFamily.CONTENT
                    else Treatment.ROUTED_SKILL
                )
                for repetition in range(1, repetitions + 1):
                    pair_id = _pair_id(
                        suite=suite,
                        case=case,
                        target=target,
                        repetition=repetition,
                        family=family,
                    )
                    treatments = (Treatment.BASELINE, candidate_treatment)
                    if repetition % 2 == 0:
                        treatments = tuple(reversed(treatments))
                    for treatment in treatments:
                        runs.append(
                            RunSpec(
                                experiment_id=experiment_id,
                                pair_id=pair_id,
                                target=target,
                                case=case,
                                treatment=treatment,
                                workspace_snapshot_hash=fixture_hash,
                                skill=None if treatment is Treatment.BASELINE else candidate,
                                timeout_seconds=case.timeout_seconds,
                                turn_limit=turn_limit,
                                budget_limit=budget_limit,
                                environment_allowlist=safe_environment,
                                permission_profile=permission_profile,
                                treatment_family=family,
                                repetition=repetition,
                                routing_relevant=routing_relevant,
                            )
                        )
    return ExperimentPlan(
        experiment_id=experiment_id,
        phase=phase,
        suite_id=suite.id,
        suite_fingerprint=suite.fingerprint,
        policy_fingerprint=suite.policy.fingerprint,
        candidate_fingerprint=candidate.fingerprint,
        runs=tuple(runs),
    )


def _pair_id(
    *,
    suite: EvalSuite,
    case: EvalCase,
    target: AgentTarget,
    repetition: int,
    family: TreatmentFamily,
) -> str:
    return "pair-" + stable_json_hash(
        {
            "suite_fingerprint": suite.fingerprint,
            "case_id": case.id,
            "target_fingerprint": target.fingerprint,
            "repetition": repetition,
            "treatment_family": family.value,
        },
        length=24,
    )


class ExperimentRunner:
    """Run one plan sequentially while retaining terminal evidence on every path."""

    def __init__(
        self,
        *,
        adapters: Mapping[AgentType, AgentAdapter],
        workspace_manager: WorkspaceManager,
        materializer: SkillMaterializer,
        artifact_store: ArtifactStore,
        evaluator_factory: EvaluatorFactory | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._workspaces = workspace_manager
        self._materializer = materializer
        self._artifacts = artifact_store
        self._evaluators = evaluator_factory or EvaluatorFactory()
        self._state_lock = threading.Lock()
        self._cancel_requested = False
        self._running = False
        self._active: tuple[AgentAdapter, str] | None = None

    def cancel(self) -> None:
        """Request cancellation and forward it to the active adapter, if any."""

        with self._state_lock:
            self._cancel_requested = True
            active = self._active
        if active is not None:
            adapter, run_id = active
            adapter.cancel(run_id)

    def run(
        self,
        plan: ExperimentPlan,
        *,
        stop_on_infrastructure_exclusion: bool = False,
    ) -> ExperimentRecord:
        with self._state_lock:
            if self._running:
                raise RuntimeError("experiment runner is already active")
            self._running = True
            self._cancel_requested = False
            self._active = None
        completed: list[EvaluatedRun] = []
        try:
            for group in _pair_groups(plan.runs):
                if self._is_cancelled():
                    break
                pair_runs = self._run_pair(
                    group,
                    stop_on_infrastructure_exclusion=stop_on_infrastructure_exclusion,
                )
                completed.extend(pair_runs)
                if completed and completed[-1].status is RunStatus.USER_CANCELLED:
                    with self._state_lock:
                        self._cancel_requested = True
                    break
                if stop_on_infrastructure_exclusion and any(
                    is_infrastructure_exclusion(run) for run in pair_runs
                ):
                    break
            cancelled = self._is_cancelled()
            stop_reason = (
                "infrastructure_exclusion"
                if (
                    stop_on_infrastructure_exclusion
                    and any(is_infrastructure_exclusion(run) for run in completed)
                )
                else None
            )
            summary = self._persist_record(
                plan,
                completed,
                cancelled=cancelled,
                stop_reason=stop_reason,
            )
            artifact_refs = tuple(
                ref
                for ref in (
                    *(run.terminal_artifact_ref for run in completed),
                    summary,
                )
                if ref is not None
            )
            return ExperimentRecord(
                plan=plan,
                runs=tuple(completed),
                cancelled=cancelled,
                artifact_refs=artifact_refs,
                stop_reason=stop_reason,
            )
        finally:
            with self._state_lock:
                self._active = None
                self._running = False

    def _run_pair(
        self,
        specs: tuple[RunSpec, ...],
        *,
        stop_on_infrastructure_exclusion: bool,
    ) -> list[EvaluatedRun]:
        pair: WorkspacePair | None = None
        evaluated: list[EvaluatedRun] = []
        cleanup_status = "not_created"
        try:
            pair = self._workspaces.create_pair(specs[0].case.fixture, specs[0].pair_id)
            if not (
                pair.snapshot_hash == pair.baseline_hash == pair.candidate_hash
                and pair.snapshot_hash == specs[0].workspace_snapshot_hash
            ):
                raise RuntimeError("paired workspace hashes do not match the planned fixture")
            for spec in specs:
                if self._is_cancelled():
                    break
                evaluated.append(self._run_one(spec, pair))
                if evaluated[-1].status is RunStatus.USER_CANCELLED:
                    break
                if (
                    stop_on_infrastructure_exclusion
                    and is_infrastructure_exclusion(evaluated[-1])
                ):
                    break
        except Exception as exc:
            already_run = {item.spec.treatment for item in evaluated}
            for spec in specs:
                if spec.treatment in already_run or self._is_cancelled():
                    continue
                evaluated.append(
                    EvaluatedRun(
                        spec=spec,
                        agent_run=_synthetic_run(
                            spec,
                            status=RunStatus.INFRA_ERROR,
                            error_code="experiment_pair_error",
                            error_message=f"pair orchestration failed: {type(exc).__name__}",
                        ),
                        evaluation=None,
                        initial_workspace_hash=pair.snapshot_hash if pair else None,
                        final_workspace_hash=None,
                        cleanup_status="pending",
                    )
                )
                if stop_on_infrastructure_exclusion:
                    break
        finally:
            if pair is not None:
                try:
                    self._workspaces.cleanup(pair)
                except Exception:
                    cleanup_status = "failed"
                else:
                    cleanup_status = pair.cleanup_status
            finalized: list[EvaluatedRun] = []
            for item in evaluated:
                agent_run = item.agent_run
                if cleanup_status == "failed" and agent_run.status is not RunStatus.USER_CANCELLED:
                    agent_run = replace(
                        agent_run,
                        status=RunStatus.INFRA_ERROR,
                        error_code="workspace_cleanup_failed",
                        error_message="isolated workspace cleanup failed",
                    )
                terminal = replace(
                    item,
                    agent_run=agent_run,
                    cleanup_status=cleanup_status,
                )
                finalized.append(self._persist_terminal(terminal))
            evaluated = finalized
        return evaluated

    def _run_one(self, spec: RunSpec, pair: WorkspacePair) -> EvaluatedRun:
        workspace = pair.baseline if spec.treatment is Treatment.BASELINE else pair.candidate
        try:
            adapter = self._adapters[spec.target.type]
        except KeyError:
            return EvaluatedRun(
                spec=spec,
                agent_run=_synthetic_run(
                    spec,
                    status=RunStatus.ADAPTER_UNAVAILABLE,
                    error_code="adapter_not_configured",
                    error_message="no adapter is configured for the requested target",
                ),
                evaluation=None,
                initial_workspace_hash=pair.snapshot_hash,
                final_workspace_hash=hash_workspace(workspace),
                cleanup_status="pending",
            )

        try:
            staged = None
            if spec.skill is not None:
                staged = self._materializer.materialize(spec.skill, spec.target.type, workspace)
            prepared = adapter.prepare(spec, workspace, staged_skill=staged)
            with self._state_lock:
                self._active = (adapter, prepared.run_id)
                cancel_now = self._cancel_requested
            if cancel_now:
                adapter.cancel(prepared.run_id)
            agent_run = adapter.run(prepared)
            agent_run = _validated_identity(spec, agent_run)
        except Exception as exc:
            agent_run = _synthetic_run(
                spec,
                status=RunStatus.ADAPTER_ERROR,
                error_code="adapter_execution_error",
                error_message=f"adapter execution failed: {type(exc).__name__}",
            )
        finally:
            with self._state_lock:
                self._active = None

        evaluation: EvaluationResult | None = None
        if agent_run.status not in _NO_EVALUATION_STATUSES:
            if agent_run.trace is None:
                agent_run = replace(
                    agent_run,
                    status=RunStatus.ADAPTER_ERROR,
                    error_code="adapter_trace_missing",
                    error_message="adapter returned no normalized trace",
                )
            else:
                try:
                    evaluator = self._evaluators.create(spec.case.evaluator)
                    evaluation = evaluator.evaluate(
                        task=spec.case.task,
                        initial_workspace=pair.snapshot,
                        final_workspace=workspace,
                        trace=agent_run.trace,
                    )
                except Exception as exc:
                    evaluation = EvaluationResult(
                        status=EvaluationStatus.ERROR,
                        reason_code="evaluator_exception",
                        evaluator_kind=spec.case.evaluator.kind,
                        details={"error_kind": type(exc).__name__},
                        duration_ms=0,
                    )
                agent_run = _resolve_status(agent_run, evaluation)
        try:
            final_hash = hash_workspace(workspace)
        except Exception as exc:
            final_hash = None
            if agent_run.status is not RunStatus.USER_CANCELLED:
                agent_run = replace(
                    agent_run,
                    status=RunStatus.INFRA_ERROR,
                    error_code="workspace_hash_failed",
                    error_message=f"final workspace hash failed: {type(exc).__name__}",
                )
        return EvaluatedRun(
            spec=spec,
            agent_run=agent_run,
            evaluation=evaluation,
            initial_workspace_hash=pair.snapshot_hash,
            final_workspace_hash=final_hash,
            cleanup_status="pending",
        )

    def _persist_terminal(self, run: EvaluatedRun) -> EvaluatedRun:
        payload = _terminal_payload(run)
        try:
            artifact = self._artifacts.write_json(
                Path("experiments")
                / run.spec.experiment_id
                / "runs"
                / f"{run.run_id}.json",
                payload,
            )
        except Exception:
            if run.status is RunStatus.USER_CANCELLED:
                return run
            return replace(
                run,
                agent_run=replace(
                    run.agent_run,
                    status=RunStatus.INFRA_ERROR,
                    error_code="terminal_artifact_write_failed",
                    error_message="terminal run artifact could not be persisted",
                ),
            )
        return replace(run, terminal_artifact_ref=artifact.relative_path)

    def _persist_record(
        self,
        plan: ExperimentPlan,
        runs: list[EvaluatedRun],
        *,
        cancelled: bool,
        stop_reason: str | None,
    ) -> str | None:
        try:
            artifact = self._artifacts.write_json(
                Path("experiments") / plan.experiment_id / "record.json",
                {
                    "experiment_id": plan.experiment_id,
                    "phase": plan.phase.value,
                    "suite_id": plan.suite_id,
                    "suite_fingerprint": plan.suite_fingerprint,
                    "policy_fingerprint": plan.policy_fingerprint,
                    "candidate_fingerprint": plan.candidate_fingerprint,
                    "cancelled": cancelled,
                    "stop_reason": stop_reason,
                    "planned_run_count": len(plan.runs),
                    "completed_run_count": len(runs),
                    "terminal_artifact_refs": tuple(
                        run.terminal_artifact_ref for run in runs if run.terminal_artifact_ref
                    ),
                },
            )
        except Exception:
            return None
        return artifact.relative_path

    def _is_cancelled(self) -> bool:
        with self._state_lock:
            return self._cancel_requested


def is_infrastructure_exclusion(run: EvaluatedRun) -> bool:
    """Return whether a run is excluded from capability comparison as infrastructure."""

    return run.status in _INFRASTRUCTURE_EXCLUSION_STATUSES


def _pair_groups(runs: tuple[RunSpec, ...]) -> tuple[tuple[RunSpec, ...], ...]:
    groups: list[tuple[RunSpec, ...]] = []
    index = 0
    while index < len(runs):
        pair_id = runs[index].pair_id
        end = index + 1
        while end < len(runs) and runs[end].pair_id == pair_id:
            end += 1
        group = runs[index:end]
        if len(group) != 2 or {run.treatment for run in group} not in (
            {Treatment.BASELINE, Treatment.FORCED_SKILL},
            {Treatment.BASELINE, Treatment.ROUTED_SKILL},
        ):
            raise ValueError(f"invalid experiment pair: {pair_id}")
        groups.append(group)
        index = end
    return tuple(groups)


def _validated_identity(spec: RunSpec, run: AgentRun) -> AgentRun:
    if (
        run.experiment_id != spec.experiment_id
        or run.pair_id != spec.pair_id
        or run.target != spec.target
        or run.case_id != spec.case.id
        or run.treatment is not spec.treatment
    ):
        return _synthetic_run(
            spec,
            status=RunStatus.ADAPTER_ERROR,
            error_code="adapter_identity_mismatch",
            error_message="adapter result identity did not match the prepared run",
            raw_event_refs=run.raw_event_refs,
        )
    return run


def _resolve_status(run: AgentRun, evaluation: EvaluationResult) -> AgentRun:
    if run.status in _NO_EVALUATION_STATUSES:
        return run
    if evaluation.details.get("unsafe") is True:
        return replace(
            run,
            status=RunStatus.UNSAFE_ACTION,
            error_code="evaluation_unsafe_action",
            error_message="evaluation observed a forbidden action",
        )
    if run.status in _CONSTRAINT_STATUSES:
        return run
    if evaluation.status is EvaluationStatus.ERROR:
        return replace(
            run,
            status=RunStatus.INFRA_ERROR,
            error_code="evaluator_error",
            error_message="outcome evaluator could not produce a valid decision",
        )
    if run.status is not RunStatus.PASSED:
        return run
    if evaluation.status is EvaluationStatus.FAILED:
        status = (
            RunStatus.VERIFICATION_FAILED
            if _is_trajectory_failure(evaluation)
            else RunStatus.WRONG_RESULT
        )
        return replace(
            run,
            status=status,
            error_code=evaluation.reason_code,
            error_message="outcome evaluation failed",
        )
    return run


def _is_trajectory_failure(evaluation: EvaluationResult) -> bool:
    if evaluation.evaluator_kind == "trajectory":
        return True
    children = evaluation.details.get("children")
    return isinstance(children, tuple) and any(
        isinstance(child, Mapping) and child.get("kind") == "trajectory"
        for child in children
    )


def _synthetic_run(
    spec: RunSpec,
    *,
    status: RunStatus,
    error_code: str,
    error_message: str,
    raw_event_refs: tuple[str, ...] = (),
) -> AgentRun:
    run_id = "runner-" + stable_json_hash(
        {
            "experiment_id": spec.experiment_id,
            "pair_id": spec.pair_id,
            "target": spec.target.fingerprint,
            "case_id": spec.case.id,
            "treatment": spec.treatment.value,
        },
        length=24,
    )
    return AgentRun(
        run_id=run_id,
        experiment_id=spec.experiment_id,
        pair_id=spec.pair_id,
        target=spec.target,
        case_id=spec.case.id,
        treatment=spec.treatment,
        status=status,
        raw_event_refs=raw_event_refs,
        error_code=error_code,
        error_message=error_message,
    )


def _terminal_payload(run: EvaluatedRun) -> Mapping[str, object]:
    agent = run.agent_run
    evaluation = run.evaluation
    return {
        "run_id": agent.run_id,
        "experiment_id": agent.experiment_id,
        "pair_id": agent.pair_id,
        "target_fingerprint": agent.target.fingerprint,
        "case_id": agent.case_id,
        "case_category": run.spec.case.category.value,
        "treatment": agent.treatment.value,
        "treatment_family": (
            run.spec.treatment_family.value if run.spec.treatment_family is not None else None
        ),
        "repetition": run.spec.repetition,
        "routing_relevant": run.spec.routing_relevant,
        "status": agent.status.value,
        "raw_event_refs": agent.raw_event_refs,
        "workspace_snapshot_hash": run.initial_workspace_hash,
        "workspace_result_hash": run.final_workspace_hash,
        "trace_complete": agent.trace_complete,
        "usage": {
            "input_tokens": agent.input_tokens,
            "output_tokens": agent.output_tokens,
            "cost_usd": str(agent.cost_usd) if agent.cost_usd is not None else None,
            "latency_ms": agent.latency_ms,
        },
        "error_code": agent.error_code,
        "error_message": agent.error_message,
        "evaluation": None
        if evaluation is None
        else {
            "status": evaluation.status.value,
            "reason_code": evaluation.reason_code,
            "evaluator_kind": evaluation.evaluator_kind,
            "details": plain_data(evaluation.details),
            "duration_ms": evaluation.duration_ms,
        },
        "cleanup_status": run.cleanup_status,
    }


__all__ = [
    "ExperimentRunner",
    "build_experiment_plan",
    "select_fast_cases",
    "select_full_cases",
]
