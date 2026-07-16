# Rook Codex-only EvalOps

Rook evaluates a stored Skill candidate with isolated Baseline, Forced Skill, and Routed Skill runs. The MVP supports the in-process Rook target and Codex CLI; Claude Code is not part of this release.

## Deterministic demo

The version-controlled demo suite contains Direct, Transfer, Regression, and Adversarial cases:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py
```

The default test path uses `FakeAgentAdapter`. It does not launch Codex, call a model API, or create model charges. The demo exercises candidate storage, paired A/B runs, ScoreCard construction, promotion history, reports, and rollback.

## CLI

Probe the local adapters without making a model call:

```powershell
rook eval doctor
```

Evaluate a CandidateStore version. Agents must be explicit. Codex additionally requires both external-call and cost acknowledgement flags:

```powershell
rook eval run `
  --skill-path .rook\skill-registry\example\candidates\1 `
  --suite evals\suites\codex-demo\suite.toml `
  --agents rook,codex `
  --allow-external `
  --allow-costs
```

Inspect reports and registry state:

```powershell
rook eval report <evaluation-id>
rook skill status <skill-name>
rook skill rollback <skill-name> --agent codex --to-version 1
rook skill export <skill-name> --agent codex --output .\staged-export
```

Export requires a promoted, non-stale target decision. Rook refuses to export directly into the real `~/.codex` tree; the output is a reviewable staging directory.

## Optional live smoke

Live Codex smoke tests remain skipped unless external execution and costs are separately authorized:

```powershell
$env:ROOK_RUN_EXTERNAL_EVALS = '1'
$env:ROOK_ALLOW_MODEL_COSTS = '1'
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo.py -k live
```

Do not set these variables in ordinary unit-test or CI jobs.
