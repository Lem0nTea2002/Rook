"""Transparent paired aggregation for EvalOps experiment records."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
import math
import random
import statistics

from rook_agent.context.identity import stable_json_hash
from rook_agent.evalops.models import (
    AgentRun,
    AgentTarget,
    AgentType,
    EvaluatedRun,
    ExperimentRecord,
    RunStatus,
    ScoreCard,
    SkillCandidate,
    Treatment,
    TreatmentFamily,
    plain_data,
)


_Z_95 = 1.959963984540054
_BOOTSTRAP_ITERATIONS = 10_000
_VALID_CAPABILITY_STATUSES = frozenset(
    {
        RunStatus.PASSED,
        RunStatus.WRONG_RESULT,
        RunStatus.VERIFICATION_FAILED,
        RunStatus.TIMEOUT,
        RunStatus.TURN_LIMIT,
        RunStatus.BUDGET_EXHAUSTED,
        RunStatus.UNSAFE_ACTION,
    }
)


class ScoreCardBuilder:
    """Aggregate only complete and capability-comparable A/B pairs."""

    def build(
        self,
        record: ExperimentRecord,
        *,
        target: AgentTarget | None = None,
    ) -> ScoreCard:
        selected_target = _select_target(record, target)
        runs = tuple(run for run in record.runs if run.spec.target == selected_target)
        candidate = _select_candidate(runs)
        groups = _group_pairs(runs)
        complete_pairs: list[tuple[EvaluatedRun, EvaluatedRun]] = []
        incomplete_pair_count = 0
        for pair_runs in groups.values():
            paired = _comparable_pair(pair_runs)
            if paired is None:
                incomplete_pair_count += 1
            else:
                complete_pairs.append(paired)

        content_pairs = tuple(
            pair
            for pair in complete_pairs
            if pair[0].spec.treatment_family is TreatmentFamily.CONTENT
        )
        routing_pairs = tuple(
            pair
            for pair in complete_pairs
            if pair[0].spec.treatment_family is TreatmentFamily.ROUTING
        )
        metrics = _build_metrics(
            runs=runs,
            content_pairs=content_pairs,
            routing_pairs=routing_pairs,
            candidate=candidate,
            target=selected_target,
            incomplete_pair_count=incomplete_pair_count,
            bootstrap_key=(
                f"{record.plan.suite_fingerprint}:"
                f"{candidate.fingerprint}:{selected_target.fingerprint}"
            ),
        )
        per_case = _per_case(complete_pairs)
        observed = tuple(sorted(key for key, value in metrics.items() if value is not None))
        missing = tuple(sorted(key for key, value in metrics.items() if value is None))
        normalizer_fingerprint = _normalizer_fingerprint(runs)
        fingerprint = stable_json_hash(
            {
                "target": selected_target.fingerprint,
                "skill_name": candidate.bundle.name,
                "skill_version": candidate.version,
                "skill_content_hash": candidate.content_hash,
                "normalizer_fingerprint": normalizer_fingerprint,
                "suite_fingerprint": record.plan.suite_fingerprint,
                "policy_fingerprint": record.plan.policy_fingerprint,
                "metrics": plain_data(metrics),
                "per_case": plain_data(per_case),
            },
            length=32,
        )
        return ScoreCard(
            target=selected_target,
            skill_name=candidate.bundle.name,
            skill_version=candidate.version,
            suite_fingerprint=record.plan.suite_fingerprint,
            policy_fingerprint=record.plan.policy_fingerprint,
            metrics=metrics,
            per_case=per_case,
            observed_fields=observed,
            missing_fields=missing,
            sample_count=len(content_pairs),
            fingerprint=fingerprint,
            skill_content_hash=candidate.content_hash,
            normalizer_fingerprint=normalizer_fingerprint,
        )


def _select_target(record: ExperimentRecord, requested: AgentTarget | None) -> AgentTarget:
    available = tuple(dict.fromkeys(run.spec.target for run in record.runs))
    if requested is not None:
        if requested not in available:
            raise ValueError("requested target is absent from the experiment record")
        return requested
    if len(available) != 1:
        raise ValueError("ScoreCard requires exactly one target or an explicit target")
    return available[0]


def _select_candidate(runs: Sequence[EvaluatedRun]) -> SkillCandidate:
    candidates = {
        run.spec.skill.fingerprint: run.spec.skill
        for run in runs
        if run.spec.skill is not None
    }
    if len(candidates) != 1:
        raise ValueError("ScoreCard requires exactly one candidate fingerprint")
    return next(iter(candidates.values()))


def _group_pairs(runs: Sequence[EvaluatedRun]) -> Mapping[str, tuple[EvaluatedRun, ...]]:
    grouped: dict[str, list[EvaluatedRun]] = defaultdict(list)
    for run in runs:
        grouped[run.spec.pair_id].append(run)
    return {pair_id: tuple(values) for pair_id, values in grouped.items()}


def _comparable_pair(
    runs: tuple[EvaluatedRun, ...],
) -> tuple[EvaluatedRun, EvaluatedRun] | None:
    if len(runs) != 2:
        return None
    baseline = next((run for run in runs if run.spec.treatment is Treatment.BASELINE), None)
    candidate = next((run for run in runs if run.spec.treatment is not Treatment.BASELINE), None)
    if baseline is None or candidate is None:
        return None
    if baseline.spec.treatment_family is not candidate.spec.treatment_family:
        return None
    expected = (
        Treatment.FORCED_SKILL
        if baseline.spec.treatment_family is TreatmentFamily.CONTENT
        else Treatment.ROUTED_SKILL
    )
    if candidate.spec.treatment is not expected:
        return None
    if baseline.initial_workspace_hash != candidate.initial_workspace_hash:
        return None
    if baseline.cleanup_status != "cleaned" or candidate.cleanup_status != "cleaned":
        return None
    if baseline.status not in _VALID_CAPABILITY_STATUSES:
        return None
    if candidate.status not in _VALID_CAPABILITY_STATUSES:
        return None
    return baseline, candidate


def _build_metrics(
    *,
    runs: Sequence[EvaluatedRun],
    content_pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
    routing_pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
    candidate: SkillCandidate,
    target: AgentTarget,
    incomplete_pair_count: int,
    bootstrap_key: str,
) -> dict[str, object]:
    baseline_successes = sum(pair[0].status is RunStatus.PASSED for pair in content_pairs)
    candidate_successes = sum(pair[1].status is RunStatus.PASSED for pair in content_pairs)
    pair_count = len(content_pairs)
    baseline_rate = _rate(baseline_successes, pair_count)
    candidate_rate = _rate(candidate_successes, pair_count)
    paired_improvement = (
        None
        if pair_count == 0
        else sum(
            int(candidate_run.status is RunStatus.PASSED)
            - int(baseline.status is RunStatus.PASSED)
            for baseline, candidate_run in content_pairs
        )
        / pair_count
    )

    latency = _paired_telemetry(content_pairs, lambda run: run.latency_ms)
    tokens = _paired_telemetry(content_pairs, _token_total)
    cost = _paired_telemetry(
        content_pairs,
        lambda run: float(run.cost_usd) if run.cost_usd is not None else None,
    )
    tools = _paired_telemetry(content_pairs, _tool_call_count)
    latency_improvement = _efficiency_improvement(*latency)
    token_improvement = _efficiency_improvement(*tokens)
    cost_improvement = _efficiency_improvement(*cost)
    tool_improvement = _efficiency_improvement(*tools)
    efficiency_metric, efficiency_improvement = next(
        (
            (name, value)
            for name, value in (
                ("latency", latency_improvement),
                ("tokens", token_improvement),
                ("cost", cost_improvement),
                ("tool_calls", tool_improvement),
            )
            if value is not None
        ),
        (None, None),
    )

    routing = _routing_metrics(routing_pairs, candidate=candidate, target=target)
    valid_content_runs = tuple(run for pair in content_pairs for run in pair)
    trace_rate = _rate(
        sum(run.agent_run.trace_complete for run in valid_content_runs),
        len(valid_content_runs),
    )
    direct_transfer_pairs = tuple(
        pair
        for pair in content_pairs
        if pair[0].spec.case.category.value in {"direct", "transfer"}
    )
    preservation_pairs = tuple(
        pair
        for pair in content_pairs
        if pair[0].spec.case.category.value in {"regression", "adversarial"}
    )
    new_regressions = sum(
        baseline.status is RunStatus.PASSED
        and candidate_run.status is not RunStatus.PASSED
        for baseline, candidate_run in preservation_pairs
    )
    regression_failures = new_regressions
    direct_transfer_improvements = sum(
        baseline.status is not RunStatus.PASSED and candidate_run.status is RunStatus.PASSED
        for baseline, candidate_run in direct_transfer_pairs
    )
    capability_degradations = sum(
        baseline.status is RunStatus.PASSED and candidate_run.status is not RunStatus.PASSED
        for baseline, candidate_run in direct_transfer_pairs
    )
    capability_baseline_successes = sum(
        baseline.status is RunStatus.PASSED for baseline, _candidate_run in direct_transfer_pairs
    )
    capability_candidate_successes = sum(
        candidate_run.status is RunStatus.PASSED
        for _baseline, candidate_run in direct_transfer_pairs
    )
    capability_count = len(direct_transfer_pairs)
    capability_uplift = _paired_success_uplift(direct_transfer_pairs)
    capability_latency = _paired_telemetry(
        direct_transfer_pairs, lambda run: run.latency_ms
    )
    capability_tokens = _paired_telemetry(direct_transfer_pairs, _token_total)
    capability_cost = _paired_telemetry(
        direct_transfer_pairs,
        lambda run: float(run.cost_usd) if run.cost_usd is not None else None,
    )
    capability_efficiency_metric, capability_efficiency_improvement = next(
        (
            (name, value)
            for name, value in (
                ("latency", _efficiency_improvement(*capability_latency)),
                ("tokens", _efficiency_improvement(*capability_tokens)),
                ("cost", _efficiency_improvement(*capability_cost)),
            )
            if value is not None
        ),
        (None, None),
    )
    content_runs = tuple(
        run
        for run in runs
        if run.spec.treatment_family is TreatmentFamily.CONTENT
    )
    infra_exclusions = sum(
        run.status not in _VALID_CAPABILITY_STATUSES for run in content_runs
    )
    metrics: dict[str, object] = {
        "valid_content_pair_count": pair_count,
        "valid_routing_pair_count": len(routing_pairs),
        "incomplete_pair_count": incomplete_pair_count,
        "infra_error_count": sum(run.status not in _VALID_CAPABILITY_STATUSES for run in runs),
        "safety_failure_count": sum(run.status is RunStatus.UNSAFE_ACTION for run in runs),
        "secret_leak_count": sum(_has_secret_leak(run) for run in runs),
        "new_regression_count": new_regressions,
        "regression_failure_count": regression_failures,
        "trace_completeness_rate": trace_rate,
        "baseline_success_rate": baseline_rate,
        "candidate_success_rate": candidate_rate,
        "baseline_success_ci95": _wilson_interval(baseline_successes, pair_count),
        "candidate_success_ci95": _wilson_interval(candidate_successes, pair_count),
        "paired_success_improvement": paired_improvement,
        "baseline_latency_ms": _distribution(latency[0]),
        "candidate_latency_ms": _distribution(latency[1]),
        "latency_improvement": latency_improvement,
        "baseline_tokens": _distribution(tokens[0]),
        "candidate_tokens": _distribution(tokens[1]),
        "token_improvement": token_improvement,
        "baseline_cost_usd": _distribution(cost[0]),
        "candidate_cost_usd": _distribution(cost[1]),
        "cost_improvement": cost_improvement,
        "baseline_tool_calls": _distribution(tools[0]),
        "candidate_tool_calls": _distribution(tools[1]),
        "tool_call_improvement": tool_improvement,
        "efficiency_metric": efficiency_metric,
        "efficiency_improvement": efficiency_improvement,
        "direct_transfer_valid_pair_count": len(direct_transfer_pairs),
        "direct_transfer_improved_pair_count": direct_transfer_improvements,
        "capability_pair_count": capability_count,
        "capability_baseline_success_rate": _rate(
            capability_baseline_successes, capability_count
        ),
        "capability_candidate_success_rate": _rate(
            capability_candidate_successes, capability_count
        ),
        "capability_baseline_success_ci95": _wilson_interval(
            capability_baseline_successes, capability_count
        ),
        "capability_candidate_success_ci95": _wilson_interval(
            capability_candidate_successes, capability_count
        ),
        "capability_paired_success_uplift": capability_uplift,
        "capability_paired_uplift_ci95": _cluster_bootstrap_interval(
            direct_transfer_pairs,
            seed_key=bootstrap_key,
        ),
        "capability_paired_uplift_ci95_method": (
            "task_stratified_bootstrap" if capability_count else None
        ),
        "capability_paired_uplift_ci95_iterations": (
            _BOOTSTRAP_ITERATIONS if capability_count else None
        ),
        "capability_improved_pair_count": direct_transfer_improvements,
        "capability_degraded_pair_count": capability_degradations,
        "preservation_pair_count": len(preservation_pairs),
        "preservation_rate": _rate(
            sum(
                candidate_run.status is RunStatus.PASSED
                for _baseline, candidate_run in preservation_pairs
            ),
            len(preservation_pairs),
        ),
        "capability_baseline_latency_ms": _distribution(capability_latency[0]),
        "capability_candidate_latency_ms": _distribution(capability_latency[1]),
        "capability_latency_delta_ms": _median_delta(*capability_latency),
        "capability_latency_improvement": _efficiency_improvement(
            *capability_latency
        ),
        "capability_baseline_tokens": _distribution(capability_tokens[0]),
        "capability_candidate_tokens": _distribution(capability_tokens[1]),
        "capability_token_delta": _median_delta(*capability_tokens),
        "capability_token_improvement": _efficiency_improvement(
            *capability_tokens
        ),
        "capability_baseline_cost_usd": _distribution(capability_cost[0]),
        "capability_candidate_cost_usd": _distribution(capability_cost[1]),
        "capability_cost_improvement": _efficiency_improvement(*capability_cost),
        "capability_efficiency_metric": capability_efficiency_metric,
        "capability_efficiency_improvement": capability_efficiency_improvement,
        "cost_observed": (
            capability_count > 0
            and len(capability_cost[0]) == capability_count
            and len(capability_cost[1]) == capability_count
        ),
        "infra_exclusion_count": infra_exclusions,
        "infra_exclusion_rate": _rate(infra_exclusions, len(content_runs)),
        **routing,
    }
    return metrics


def _paired_success_uplift(
    pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
) -> float | None:
    if not pairs:
        return None
    return sum(
        int(candidate.status is RunStatus.PASSED)
        - int(baseline.status is RunStatus.PASSED)
        for baseline, candidate in pairs
    ) / len(pairs)


def _cluster_bootstrap_interval(
    pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
    *,
    seed_key: str,
) -> Mapping[str, float] | None:
    if not pairs:
        return None
    by_case: dict[str, list[int]] = defaultdict(list)
    for baseline, candidate in pairs:
        by_case[baseline.spec.case.id].append(
            int(candidate.status is RunStatus.PASSED)
            - int(baseline.status is RunStatus.PASSED)
        )
    case_ids = sorted(by_case)
    seed = int(stable_json_hash(seed_key, length=16), 16)
    generator = random.Random(seed)
    samples: list[float] = []
    for _ in range(_BOOTSTRAP_ITERATIONS):
        deltas: list[int] = []
        for _case_index in case_ids:
            selected = case_ids[generator.randrange(len(case_ids))]
            deltas.extend(by_case[selected])
        samples.append(sum(deltas) / len(deltas))
    samples.sort()
    return {
        "lower": _percentile(samples, 0.025),
        "upper": _percentile(samples, 0.975),
    }


def _paired_telemetry(
    pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
    getter: Callable[[AgentRun], int | float | None],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    baseline_values: list[float] = []
    candidate_values: list[float] = []
    for baseline, candidate in pairs:
        baseline_value = getter(baseline.agent_run)
        candidate_value = getter(candidate.agent_run)
        if baseline_value is None or candidate_value is None:
            continue
        baseline_values.append(float(baseline_value))
        candidate_values.append(float(candidate_value))
    return tuple(baseline_values), tuple(candidate_values)


def _token_total(run: AgentRun) -> int | None:
    if run.input_tokens is None and run.output_tokens is None:
        return None
    return (run.input_tokens or 0) + (run.output_tokens or 0)


def _tool_call_count(run: AgentRun) -> int | None:
    if run.trace is None:
        return None
    return sum(event.type == "tool_requested" for event in run.trace.events)


def _efficiency_improvement(
    baseline_values: Sequence[float], candidate_values: Sequence[float]
) -> float | None:
    if not baseline_values or not candidate_values:
        return None
    baseline_median = float(statistics.median(baseline_values))
    if baseline_median <= 0:
        return None
    candidate_median = float(statistics.median(candidate_values))
    return 1.0 - candidate_median / baseline_median


def _median_delta(
    baseline_values: Sequence[float], candidate_values: Sequence[float]
) -> float | None:
    if not baseline_values or not candidate_values:
        return None
    return float(statistics.median(candidate_values) - statistics.median(baseline_values))


def _distribution(values: Sequence[float]) -> Mapping[str, object] | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "median": float(statistics.median(ordered)),
        "q1": _percentile(ordered, 0.25),
        "q3": _percentile(ordered, 0.75),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _percentile(ordered: Sequence[float], quantile: float) -> float:
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _wilson_interval(successes: int, total: int) -> Mapping[str, float] | None:
    if total == 0:
        return None
    observed = successes / total
    denominator = 1.0 + (_Z_95**2) / total
    center = (observed + (_Z_95**2) / (2.0 * total)) / denominator
    margin = (
        _Z_95
        * math.sqrt(
            (observed * (1.0 - observed) / total)
            + (_Z_95**2) / (4.0 * total**2)
        )
        / denominator
    )
    return {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin)}


def _routing_metrics(
    pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
    *,
    candidate: SkillCandidate,
    target: AgentTarget,
) -> Mapping[str, object]:
    if target.type is not AgentType.ROOK or not pairs:
        return {
            "routing_observed": False,
            "routing_precision": None,
            "routing_recall": None,
            "routing_true_positive_count": None,
            "routing_loaded_count": None,
            "routing_relevant_count": None,
        }
    loaded_count = 0
    relevant_count = 0
    true_positive_count = 0
    for _baseline, routed in pairs:
        relevant = routed.spec.routing_relevant is True
        loaded = _candidate_loaded(routed.agent_run, candidate)
        relevant_count += int(relevant)
        loaded_count += int(loaded)
        true_positive_count += int(relevant and loaded)
    return {
        "routing_observed": True,
        "routing_precision": _rate(true_positive_count, loaded_count),
        "routing_recall": _rate(true_positive_count, relevant_count),
        "routing_true_positive_count": true_positive_count,
        "routing_loaded_count": loaded_count,
        "routing_relevant_count": relevant_count,
    }


def _candidate_loaded(run: AgentRun, candidate: SkillCandidate) -> bool:
    if run.trace is None:
        return False
    return any(
        event.type == "skill_loaded"
        and event.data.get("skill_name") == candidate.bundle.name
        and event.data.get("content_hash") == candidate.content_hash
        for event in run.trace.events
    )


def _has_secret_leak(run: EvaluatedRun) -> bool:
    evaluation = run.evaluation
    if evaluation is None:
        return False
    return evaluation.details.get("secret_leak") is True or "secret_leak" in evaluation.reason_code


def _normalizer_fingerprint(runs: Sequence[EvaluatedRun]) -> str | None:
    versions = sorted(
        {
            run.agent_run.trace.normalizer_version
            for run in runs
            if run.agent_run.trace is not None and run.agent_run.trace.normalizer_version
        }
    )
    return None if not versions else stable_json_hash(versions, length=32)


def _per_case(
    pairs: Sequence[tuple[EvaluatedRun, EvaluatedRun]],
) -> Mapping[str, object]:
    output: dict[str, dict[str, object]] = {}
    for baseline, candidate in sorted(
        pairs,
        key=lambda pair: (
            pair[0].spec.case.id,
            pair[0].spec.treatment_family.value if pair[0].spec.treatment_family else "",
            pair[0].spec.repetition,
        ),
    ):
        case = output.setdefault(
            baseline.spec.case.id,
            {
                "category": baseline.spec.case.category.value,
                "pairs": [],
                "failures": [],
            },
        )
        pair_payload = {
            "pair_id": baseline.spec.pair_id,
            "family": baseline.spec.treatment_family.value
            if baseline.spec.treatment_family
            else None,
            "repetition": baseline.spec.repetition,
            "baseline_status": baseline.status.value,
            "candidate_status": candidate.status.value,
        }
        case["pairs"].append(pair_payload)  # type: ignore[union-attr]
        if candidate.status is not RunStatus.PASSED:
            case["failures"].append(  # type: ignore[union-attr]
                {
                    "pair_id": candidate.spec.pair_id,
                    "treatment": candidate.spec.treatment.value,
                    "status": candidate.status.value,
                    "reason_code": candidate.agent_run.error_code,
                }
            )
    return output


__all__ = ["ScoreCardBuilder"]
