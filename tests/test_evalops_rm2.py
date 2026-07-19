from __future__ import annotations

from dataclasses import replace
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from rook_agent.evalops.bundles import load_skill_bundle
from rook_agent.evalops.evaluators import EvaluatorFactory
from rook_agent.evalops.models import (
    CaseCategory,
    EvaluationStatus,
    NormalizedTrace,
    TreatmentFamily,
)
from rook_agent.evalops.runner import build_experiment_plan
from rook_agent.evalops.skills import render_skill
from rook_agent.evalops.suites import load_eval_suite
from tests.test_evalops_runner import _candidate, _target


_ROOT = Path(__file__).parents[1]
_SUITE_ROOT = _ROOT / "evals" / "suites" / "release-manifest-v2"
_CANDIDATE_ROOT = _ROOT / "evals" / "candidates" / "release-manifest-v2"


def _validator_module():
    path = _SUITE_ROOT / "validators" / "validate_rm2.py"
    spec = importlib.util.spec_from_file_location("rook_rm2_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _holdout_validator_module():
    path = _SUITE_ROOT / "holdout" / "validators" / "validate_rm2_holdout.py"
    spec = importlib.util.spec_from_file_location("rook_rm2_holdout_validator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rm2_manifests_are_strict_versioned_and_bounded() -> None:
    full = load_eval_suite(_SUITE_ROOT / "suite.toml")
    pilot = load_eval_suite(_SUITE_ROOT / "pilot.toml")
    calibration = load_eval_suite(_SUITE_ROOT / "calibration.toml")

    counts = {
        category: sum(case.category is category for case in full.cases)
        for category in CaseCategory
    }
    assert counts == {category: 3 for category in CaseCategory}
    assert len(calibration.cases) == 6
    assert len(pilot.cases) == len(full.cases) == 12
    assert calibration.id == "release-manifest-v2-calibration"
    assert pilot.id == "release-manifest-v2-pilot"
    assert full.id == "release-manifest-v2-formal-holdout"
    assert len({calibration.fingerprint, pilot.fingerprint, full.fingerprint}) == 3
    assert set(case.id for case in pilot.cases).isdisjoint(
        case.id for case in full.cases
    )
    assert tuple(case.category for case in pilot.cases) == tuple(
        case.category for case in full.cases
    )
    assert all(case.evaluator.kind == "command" for case in full.cases)
    assert all(case.timeout_seconds == 120 for case in full.cases)
    assert all(case.network_policy.value == "disabled" for case in full.cases)
    assert pilot.policy.data["min_capability_pairs"] == 6
    assert pilot.policy.data["require_positive_capability_uplift_ci"] is True
    assert pilot.policy.version == "rm2-pilot-1"
    assert full.policy.data["min_capability_pairs"] == 18
    assert full.policy.data["require_positive_capability_uplift_ci"] is True
    assert full.policy.data["require_success_uplift"] is True
    effective = load_skill_bundle(_CANDIDATE_ROOT / "effective.toml")
    assert full.candidate_content_hash == hashlib.sha256(
        render_skill(effective).encode("utf-8")
    ).hexdigest()
    assert calibration.policy.data["require_success_uplift"] is True


def test_rm2_content_only_call_counts_are_exact() -> None:
    full = load_eval_suite(_SUITE_ROOT / "suite.toml")
    pilot = load_eval_suite(_SUITE_ROOT / "pilot.toml")
    calibration = load_eval_suite(_SUITE_ROOT / "calibration.toml")
    assert full.candidate_content_hash is not None
    formal_candidate = replace(
        _candidate(),
        content_hash=full.candidate_content_hash,
    )

    plans = (
        build_experiment_plan(
            calibration,
            targets=(_target(),),
            candidate=_candidate(),
            repetitions=1,
            families=(TreatmentFamily.CONTENT,),
        ),
        build_experiment_plan(
            pilot,
            targets=(_target(),),
            candidate=_candidate(),
            repetitions=1,
            families=(TreatmentFamily.CONTENT,),
        ),
        build_experiment_plan(
            full,
            targets=(_target(),),
            candidate=formal_candidate,
            repetitions=3,
            families=(TreatmentFamily.CONTENT,),
        ),
    )

    assert [len(plan.runs) for plan in plans] == [12, 24, 72]


@pytest.mark.parametrize(
    "case_id",
    (
        "direct-canonical",
        "direct-casing",
        "direct-untrusted",
        "transfer-release",
        "transfer-text",
        "transfer-nested",
        "adversarial-comment",
        "adversarial-secret",
        "adversarial-instruction",
    ),
)
def test_hidden_validator_accepts_reference_semantics(
    tmp_path: Path,
    case_id: str,
) -> None:
    validator = _validator_module()
    fixture = _SUITE_ROOT / "fixtures" / case_id
    workspace = tmp_path / case_id
    shutil.copytree(fixture, workspace)
    payload = validator.reference_payload(workspace, case_id)
    (workspace / "release.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert validator.validate_workspace(workspace, case_id) is None


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ({"schema": "rook.release/v1"}, "schema"),
        ({"service": "Payments-API"}, "service"),
        ({"version": "2.4"}, "version"),
        ({"channel": "stable"}, "channel"),
        ({"owners": ["bob", "alice"]}, "owners"),
        ({"artifact_id": "wrong"}, "artifact_id"),
    ),
)
def test_hidden_validator_mutations_fail_independently(
    tmp_path: Path,
    mutation: dict[str, object],
    reason: str,
) -> None:
    validator = _validator_module()
    case_id = "direct-canonical"
    workspace = tmp_path / reason
    shutil.copytree(_SUITE_ROOT / "fixtures" / case_id, workspace)
    payload = validator.reference_payload(workspace, case_id)
    payload.update(mutation)
    (workspace / "release.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    assert validator.validate_workspace(workspace, case_id) == f"field_{reason}"


def test_hidden_validator_rejects_source_mutation_and_extra_outputs(tmp_path: Path) -> None:
    validator = _validator_module()
    case_id = "direct-canonical"
    workspace = tmp_path / "source"
    shutil.copytree(_SUITE_ROOT / "fixtures" / case_id, workspace)
    source = workspace / "manifest.rkm"
    source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    assert validator.validate_workspace(workspace, case_id) == "source_modified"

    shutil.rmtree(workspace)
    shutil.copytree(_SUITE_ROOT / "fixtures" / case_id, workspace)
    payload = validator.reference_payload(workspace, case_id)
    (workspace / "release.json").write_text(json.dumps(payload), encoding="utf-8")
    (workspace / "danger.txt").write_text("bad", encoding="utf-8")
    assert validator.validate_workspace(workspace, case_id) == "forbidden_output"


def test_rm2_suite_executes_hidden_validator_with_current_python(tmp_path: Path) -> None:
    suite = load_eval_suite(_SUITE_ROOT / "pilot.toml")
    case = next(case for case in suite.cases if case.id == "direct-canonical")
    workspace = tmp_path / "workspace"
    shutil.copytree(case.fixture, workspace)
    validator = _validator_module()
    (workspace / "release.json").write_text(
        json.dumps(validator.reference_payload(workspace, case.id), indent=2) + "\n",
        encoding="utf-8",
    )

    result = EvaluatorFactory().create(case.evaluator).evaluate(
        task=case.task,
        initial_workspace=case.fixture,
        final_workspace=workspace,
        trace=NormalizedTrace(events=(), trace_complete=True, normalizer_version="test"),
    )

    assert result.status is EvaluationStatus.PASSED
    assert result.reason_code == "command_passed"


@pytest.mark.parametrize(
    "case_id",
    (
        "holdout-catalog",
        "holdout-application",
        "holdout-package",
        "holdout-chart",
        "holdout-mobile",
        "holdout-ml-service",
        "holdout-comment",
        "holdout-secret",
        "holdout-instruction",
    ),
)
def test_holdout_validator_accepts_reference_semantics(
    tmp_path: Path,
    case_id: str,
) -> None:
    validator = _holdout_validator_module()
    fixture = _SUITE_ROOT / "holdout" / "fixtures" / case_id
    workspace = tmp_path / case_id
    shutil.copytree(fixture, workspace)
    payload = validator.reference_payload(workspace, case_id)
    (workspace / "release.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert validator.validate_workspace(workspace, case_id) is None


def test_formal_holdout_is_disjoint_from_pilot_content() -> None:
    pilot = load_eval_suite(_SUITE_ROOT / "pilot.toml")
    formal = load_eval_suite(_SUITE_ROOT / "suite.toml")
    pilot_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for case in pilot.cases
        for path in case.fixture.rglob("*")
        if path.is_file()
    }
    formal_hashes = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for case in formal.cases
        for path in case.fixture.rglob("*")
        if path.is_file()
    }

    assert pilot_hashes.isdisjoint(formal_hashes)


def test_rm2_candidate_and_tasks_do_not_leak_hidden_answers() -> None:
    effective_path = _CANDIDATE_ROOT / "effective.toml"
    neutral_path = _CANDIDATE_ROOT / "neutral.toml"
    effective = load_skill_bundle(effective_path)
    load_skill_bundle(neutral_path)
    candidate_text = effective_path.read_text(encoding="utf-8").casefold()
    task_text = "\n".join(
        path.read_text(encoding="utf-8") for path in (_SUITE_ROOT / "tasks").glob("*.md")
    ).casefold()
    holdout_task_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (_SUITE_ROOT / "holdout" / "tasks").glob("*.md")
    ).casefold()

    assert effective.name == "release-manifest-v2-normalizer"
    assert "validators/" not in candidate_text + task_text + holdout_task_text
    assert '"schema"' not in candidate_text + task_text + holdout_task_text
    assert "payments-api" not in candidate_text
    for case_id in (
        "direct-canonical",
        "direct-casing",
        "transfer-release",
        "adversarial-secret",
    ):
        assert case_id not in candidate_text
    for case in load_eval_suite(_SUITE_ROOT / "suite.toml").cases:
        assert case.id not in candidate_text
    assert not tuple((_SUITE_ROOT / "fixtures").rglob("release.json"))
    assert not tuple((_SUITE_ROOT / "holdout" / "fixtures").rglob("release.json"))
