from __future__ import annotations

import json
from pathlib import Path

from rook_agent.benchmarks.native_analysis import analyze_native_failures


def _write_transcript(path: Path, *, finish: str, user_messages: int = 1) -> None:
    events: list[dict[str, object]] = []
    for index in range(user_messages):
        events.append(
            {
                "type": "user_message",
                "payload": {"message_id": f"user-{index}", "parts": []},
            }
        )
    events.append(
        {
            "type": "assistant_message",
            "payload": {
                "message_id": "assistant-final",
                "metadata": {"finish_reason": finish},
                "parts": [],
            },
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def _run(
    root: Path,
    *,
    task_id: str,
    status: str,
    clean: bool,
    finish: str,
    patch: str,
    permissions: int = 0,
    user_messages: int = 1,
) -> dict[str, object]:
    transcript = root / f"{task_id}.jsonl"
    patch_path = root / f"{task_id}.patch"
    validation = root / f"{task_id}-validation.json"
    _write_transcript(
        transcript,
        finish=finish,
        user_messages=user_messages,
    )
    patch_path.write_text(patch, encoding="utf-8")
    validation.write_text(
        json.dumps({"hidden": "DO_NOT_READ_THIS_SENTINEL"}),
        encoding="utf-8",
    )
    return {
        "task_id": task_id,
        "repository": "https://github.com/example/repo",
        "category": "bug",
        "assistance": "unassisted",
        "status": status,
        "reason_code": "execution_nonzero_exit",
        "clean_termination": clean,
        "provider_requests": 12,
        "tool_calls": 8,
        "permission_interruptions": permissions,
        "blocked_high_risk_requests": permissions,
        "artifact_refs": {
            "transcript": str(transcript),
            "patch": str(patch_path),
            "validation": str(validation),
        },
    }


def test_native_failure_analysis_uses_public_trace_without_hidden_output(
    tmp_path: Path,
) -> None:
    patch = "diff --git a/src/x.py b/src/x.py\n+new\n"
    runs = [
        _run(
            tmp_path,
            task_id="permission-stop",
            status="validation_failed",
            clean=False,
            finish="tool_calls",
            patch="",
            permissions=1,
        ),
        _run(
            tmp_path,
            task_id="budget-empty",
            status="validation_failed",
            clean=True,
            finish="provider_call_limit",
            patch="",
        ),
        _run(
            tmp_path,
            task_id="regression",
            status="regression",
            clean=True,
            finish="provider_call_limit",
            patch=patch,
        ),
        _run(
            tmp_path,
            task_id="contaminated",
            status="validation_failed",
            clean=True,
            finish="provider_call_limit",
            patch=patch,
            user_messages=2,
        ),
        _run(
            tmp_path,
            task_id="passed",
            status="passed",
            clean=True,
            finish="stop",
            patch=patch,
        ),
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "experiment_id": "diagnostic-formal",
                "phase": "formal",
                "final_runs": runs,
            }
        ),
        encoding="utf-8",
    )

    analysis = analyze_native_failures(manifest)
    payload = analysis.to_dict()

    assert analysis.failed_task_count == 4
    assert analysis.formal_evidence_usable is False
    assert analysis.classification_counts == {
        "budget_exhausted_without_patch": 1,
        "evidence_contaminated": 1,
        "noninteractive_permission_pause": 1,
        "patch_validation_miss": 1,
        "regression_introduced": 1,
    }
    assert "DO_NOT_READ_THIS_SENTINEL" not in json.dumps(payload)
    assert payload["findings"][0]["patch_bytes"] == 0
