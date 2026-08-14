from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from rook_agent.evalops.bundles import load_skill_bundle
from rook_agent.evalops.models import CaseCategory, NetworkPolicy
from rook_agent.evalops.skills import render_skill
from rook_agent.evalops.suites import load_eval_suite


_ROOT = Path(__file__).parents[1]
_CI_ROOT = _ROOT / "evals" / "suites" / "github-actions-ci-guard-holdout"
_RAG_ROOT = _ROOT / "evals" / "suites" / "rag-evidence-reporter-holdout"
_RM2_EXTERNAL_ROOT = (
    _ROOT / "evals" / "suites" / "release-manifest-v2-real-repo-holdout"
)


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - Git identity.


def _candidate_hash(path: Path) -> str:
    bundle = load_skill_bundle(path)
    return hashlib.sha256(render_skill(bundle).encode("utf-8")).hexdigest()


def test_real_repo_holdouts_are_candidate_locked_and_network_disabled() -> None:
    pairs = (
        (
            _CI_ROOT,
            _ROOT / "evals" / "candidates" / "github-actions-ci-guard" / "effective.toml",
            "github-actions-ci-guard-real-repo-holdout",
        ),
        (
            _RAG_ROOT,
            _ROOT / "evals" / "candidates" / "rag-evidence-reporter" / "effective.toml",
            "rag-evidence-reporter-real-repo-holdout",
        ),
    )
    for suite_root, candidate_path, suite_id in pairs:
        suite = load_eval_suite(suite_root / "suite.toml")
        assert suite.id == suite_id
        assert suite.candidate_content_hash == _candidate_hash(candidate_path)
        assert len(suite.cases) == 2
        assert {case.category for case in suite.cases} <= {
            CaseCategory.DIRECT,
            CaseCategory.REGRESSION,
            CaseCategory.ADVERSARIAL,
        }
        assert all(case.network_policy == NetworkPolicy.DISABLED for case in suite.cases)


def test_real_repo_provenance_is_pinned_and_fixture_hashes_match() -> None:
    ci = json.loads((_CI_ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert ci["repository"] == "https://github.com/ZHUMUJUN/Rook"
    assert len(ci["commit"]) == 40
    assert ci["license"] == "MIT"
    for item in ci["files"]:
        fixture = _CI_ROOT / item["fixture"]
        assert item["transformation"] == "none"
        assert _git_blob_sha1(fixture.read_bytes()) == item["git_blob_sha1"]

    rag = json.loads((_RAG_ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
    assert rag["repository"].endswith(
        "/Multimodal-LLM-Agent-for-Scientific-Document-RAG"
    )
    assert len(rag["commit"]) == 40
    assert rag["license"] == "MIT"
    for item in rag["files"]:
        fixture = _RAG_ROOT / item["fixture"]
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == item["fixture_sha256"]
        assert len(item["git_blob_sha1"]) == 40
        assert item["transformation"].startswith("selected top-level")


def test_ci_guard_reference_change_and_preservation_pass_hidden_validator(
    tmp_path: Path,
) -> None:
    validator = _module(
        _CI_ROOT / "validators" / "validate_ci_guard.py", "ci_guard_validator"
    )
    harden = tmp_path / "harden"
    shutil.copytree(_CI_ROOT / "fixtures" / "harden-rook-ci", harden)
    workflow = harden / ".github" / "workflows" / "offline-tests.yml"
    text = workflow.read_text(encoding="utf-8")
    text = text.replace(
        "    runs-on: ${{ matrix.os }}\n",
        "    runs-on: ${{ matrix.os }}\n    timeout-minutes: 20\n",
    ).replace(
        "    runs-on: ubuntu-latest\n",
        "    runs-on: ubuntu-latest\n    timeout-minutes: 10\n",
    )
    text = text.replace(
        "        uses: actions/checkout@v7\n",
        "        uses: actions/checkout@v7\n"
        "        with:\n"
        "          persist-credentials: false\n",
    )
    workflow.write_text(text, encoding="utf-8")
    assert validator.validate_harden_rook_ci(harden) is None

    preserve = tmp_path / "preserve"
    shutil.copytree(_CI_ROOT / "fixtures" / "preserve-dependabot", preserve)
    assert validator.validate_preserve_dependabot(preserve) is None


def test_rag_evidence_reference_outputs_pass_hidden_validator(tmp_path: Path) -> None:
    validator = _module(
        _RAG_ROOT / "validators" / "validate_rag_evidence.py",
        "rag_evidence_validator",
    )
    compare = tmp_path / "compare"
    shutil.copytree(_RAG_ROOT / "fixtures" / "compare-retrieval", compare)
    summary = {
        "schema": "rook.rag-eval-summary/v1",
        "dataset_path": (
            "/home/suity/worksapce/PycharmProjects/FindJob/"
            "agentic-rag-for-dummies-main/project/evaluation/datasets/"
            "quick_eval_5.jsonl"
        ),
        "case_count": 5,
        "modes": [
            {
                "mode": "baseline_hybrid",
                "source_hit_rate": 1.0,
                "mrr": 1.0,
                "avg_latency_ms": 14.92,
                "p95_latency_ms": 18.12,
            },
            {
                "mode": "hybrid_rerank",
                "source_hit_rate": 1.0,
                "mrr": 1.0,
                "avg_latency_ms": 6991.2,
                "p95_latency_ms": 34872.52,
            },
        ],
        "comparison": {
            "quality_winner": "tie",
            "latency_winner": "baseline_hybrid",
            "p95_latency_delta_ms": 34854.4,
        },
        "caveats": ["Five-case dataset; quality metrics tie; latency differs."],
    }
    (compare / "evaluation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert validator.validate_compare(compare) is None

    skipped = tmp_path / "skipped"
    shutil.copytree(_RAG_ROOT / "fixtures" / "skipped-ragas", skipped)
    skipped_summary = {
        "schema": "rook.rag-eval-summary/v1",
        "dataset_path": (
            "/home/suity/worksapce/PycharmProjects/FindJob/"
            "agentic-rag-for-dummies-main/project/evaluation/datasets/"
            "sample_eval.jsonl"
        ),
        "case_count": 2,
        "ragas": {
            "status": "not_observed",
            "evaluated_cases": 0,
            "scores": {},
            "reason": "No cases with non-empty ground_truth were found.",
        },
    }
    (skipped / "evaluation-summary.json").write_text(
        json.dumps(skipped_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert validator.validate_skipped(skipped) is None


def test_rm2_external_holdout_locks_candidate_and_two_real_repositories() -> None:
    suite = load_eval_suite(_RM2_EXTERNAL_ROOT / "suite.toml")
    candidate_path = (
        _ROOT
        / "evals"
        / "candidates"
        / "release-manifest-v2"
        / "effective-v5.toml"
    )

    assert suite.id == "release-manifest-v2-two-repo-holdout-v1"
    assert suite.candidate_content_hash == _candidate_hash(candidate_path)
    assert len(suite.cases) == 6
    assert {case.category for case in suite.cases} == {
        CaseCategory.DIRECT,
        CaseCategory.TRANSFER,
        CaseCategory.REGRESSION,
    }
    assert all(case.network_policy == NetworkPolicy.DISABLED for case in suite.cases)

    provenance = json.loads(
        (_RM2_EXTERNAL_ROOT / "PROVENANCE.json").read_text(encoding="utf-8")
    )
    repositories = provenance["repositories"]
    assert [item["repository"] for item in repositories] == [
        "https://github.com/ZHUMUJUN/Rook",
        (
            "https://github.com/ZHUMUJUN/"
            "Multimodal-LLM-Agent-for-Scientific-Document-RAG"
        ),
    ]
    assert all(len(item["commit"]) == 40 for item in repositories)
    assert all(item["license"] == "MIT" for item in repositories)
    for item in provenance["fixture_files"]:
        fixture = _RM2_EXTERNAL_ROOT / item["fixture"]
        assert hashlib.sha256(fixture.read_bytes()).hexdigest() == item["sha256"]


def test_rm2_external_reference_outputs_pass_hidden_validator(tmp_path: Path) -> None:
    validator = _module(
        _RM2_EXTERNAL_ROOT / "validators" / "validate_external_rm2.py",
        "external_rm2_validator",
    )
    for case_id in validator.case_ids():
        workspace = tmp_path / case_id
        shutil.copytree(_RM2_EXTERNAL_ROOT / "fixtures" / case_id, workspace)
        if validator.requires_output(case_id):
            payload = validator.reference_payload(workspace, case_id)
            (workspace / "release.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        assert validator.validate_workspace(workspace, case_id) is None


def test_rm2_external_validator_rejects_source_mutation_and_extra_output(
    tmp_path: Path,
) -> None:
    validator = _module(
        _RM2_EXTERNAL_ROOT / "validators" / "validate_external_rm2.py",
        "external_rm2_validator_failures",
    )
    case_id = "rook-release-direct"
    workspace = tmp_path / case_id
    shutil.copytree(_RM2_EXTERNAL_ROOT / "fixtures" / case_id, workspace)
    source = workspace / validator.source_ref(case_id)
    source.write_text(source.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    assert validator.validate_workspace(workspace, case_id) == "source_modified"

    shutil.rmtree(workspace)
    shutil.copytree(_RM2_EXTERNAL_ROOT / "fixtures" / case_id, workspace)
    (workspace / "debug.txt").write_text("unexpected\n", encoding="utf-8")
    assert validator.validate_workspace(workspace, case_id) == "forbidden_output"
