# Contributing to Rook

Rook welcomes focused fixes, tests, documentation, and small features that keep the coding-agent runtime and Rook Forge understandable.

## Development setup

Python 3.11 or newer is required.

PowerShell 7 on Windows:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
```

Linux or macOS:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Before opening a pull request

1. Add or update tests for the changed boundary.
2. Run the focused tests first, then the offline suite.
3. Keep external evaluation disabled unless a maintainer gave explicit call and cost authorization.
4. Update the relevant design or runbook when runtime behavior changes.
5. Verify `git diff --check` and review the exact staged diff.

Windows commands:

```powershell
$env:ROOK_RUN_EXTERNAL_EVALS = '0'
$env:ROOK_ALLOW_MODEL_COSTS = '0'
& '.\.venv\Scripts\python.exe' -m pytest -q --ignore=tests/test_evalplus_benchmark.py
git diff --check
```

The deterministic product smoke is safe to run without a provider:

```powershell
rook eval demo
```

## Change guidelines

- Preserve strict parsing, path containment, immutable history, and explicit approval boundaries.
- Do not make a Fake Agent result look like real model-quality evidence.
- Do not weaken a safety gate or add a live external call to default tests.
- Keep Candidate generation on the same examination and approval path; no bypass deployment path.
- Use narrow, reviewable commits and explain observable behavior in the pull request.

For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
