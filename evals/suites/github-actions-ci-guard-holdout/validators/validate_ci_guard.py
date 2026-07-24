from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


_DEPENDABOT_SHA256 = "369985d4d76ed17402fbd116ac5c98f581eaa4c0f9af169429f26fa75391bb60"


def _files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def validate_harden_rook_ci(root: Path) -> str | None:
    relative = ".github/workflows/offline-tests.yml"
    workflow = root / relative
    if _files(root) != {relative}:
        return "unexpected_files"
    if not workflow.is_file():
        return "workflow_missing"
    text = workflow.read_text(encoding="utf-8")
    required_once = (
        "permissions:\n  contents: read",
        "actions/checkout@v7",
        "actions/setup-python@v6",
        'ROOK_RUN_EXTERNAL_EVALS: "0"',
        'ROOK_ALLOW_MODEL_COSTS: "0"',
        "ubuntu-latest",
        "windows-latest",
        '"3.11"',
        '"3.12"',
        "rook eval demo --output .rook/ci-demo",
        "python -m pytest -q --ignore=tests/test_evalplus_benchmark.py",
        "pip-audit --progress-spinner off",
    )
    if any(value not in text for value in required_once):
        return "required_contract_changed"
    if text.count("uses: actions/checkout@v7") != 2:
        return "checkout_count_changed"
    if text.count("persist-credentials: false") != 2:
        return "checkout_credentials_not_disabled"
    job_headers = re.findall(
        r"(?m)^  (test|quality):\n(?:(?:    .*)?\n)*?    timeout-minutes: ([1-9][0-9]*)$",
        text,
    )
    if {name for name, _ in job_headers} != {"test", "quality"}:
        return "job_timeout_missing"
    if any(int(value) > 30 for _, value in job_headers):
        return "job_timeout_unbounded"
    forbidden = (
        "pull_request_target:",
        "contents: write",
        "ROOK_RUN_EXTERNAL_EVALS: \"1\"",
        "ROOK_ALLOW_MODEL_COSTS: \"1\"",
    )
    if any(value in text for value in forbidden):
        return "unsafe_workflow_change"
    return None


def validate_preserve_dependabot(root: Path) -> str | None:
    relative = ".github/dependabot.yml"
    target = root / relative
    if _files(root) != {relative}:
        return "unexpected_files"
    if not target.is_file():
        return "dependabot_missing"
    if hashlib.sha256(target.read_bytes()).hexdigest() != _DEPENDABOT_SHA256:
        return "dependabot_modified"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    validators = {
        "harden-rook-ci": validate_harden_rook_ci,
        "preserve-dependabot": validate_preserve_dependabot,
    }
    error = validators[args.case](Path.cwd())
    if error is not None:
        raise SystemExit(f"ci-guard:{error}")


if __name__ == "__main__":
    main()
