# Rook RM-2 Live Evidence Execution Plan

**Goal:** Produce auditable, resume-safe Codex Skill-effect metrics from a versioned benchmark where the Candidate supplies a real repository-specific convention, while Baseline remains a fair unassisted control.

**Outcome contract:** The final evidence must report the exact suite, Candidate, policy, model, CLI/Adapter versions, paired denominators, exclusions, success rates and intervals, paired uplift, regressions, Token and latency deltas, and cost observability. Fake Agent results and live Codex results must remain explicitly separated.

**Scope:** Codex content-effect experiments only for published model-quality metrics. Rook and Routed-Skill controls remain available, but Codex routing precision/recall stay unobserved until Codex emits a reliable native Skill identity event.

## Non-negotiable measurement rules

- Baseline and Forced Skill use the same model, timeout, network policy, fixture snapshot, evaluator, repetition, and treatment ordering policy.
- Baseline content runs receive no ambient Skill instruction catalog. Forced runs read only the mounted Candidate through the existing explicit relative path.
- Candidate instructions may define general RM-2 rules but may not contain case IDs, fixture names, input values, expected JSON documents, or evaluator paths.
- Expected answers and validators remain outside Agent-readable workspaces and are included in the suite fingerprint.
- Infrastructure failures are counted and reported but excluded from capability denominators. Timeouts and wrong results remain capability outcomes.
- Direct and Transfer measure capability. Regression and Adversarial measure preservation and safety; they must not dilute the published capability uplift.
- Repeated executions are reported as `unique cases x repetitions`, not misrepresented as independent production tasks.
- USD cost remains `not observed` unless the Codex event stream supplies a real USD field. Token counts are not converted into invented currency.
- No live stage starts without a separate call-count and model-cost authorization. Each stage stops before the next approval gate.

## Publishable metrics

The final ScoreCard must expose these exact fields:

| Metric | Definition |
|---|---|
| `capability_pair_count` | Complete Direct/Transfer Baseline-Forced pairs |
| `capability_baseline_success_rate` | Passed Direct/Transfer Baselines / valid capability pairs |
| `capability_candidate_success_rate` | Passed Direct/Transfer Forced runs / valid capability pairs |
| `capability_baseline_success_ci95` | Wilson 95% interval for Baseline capability success |
| `capability_candidate_success_ci95` | Wilson 95% interval for Candidate capability success |
| `capability_paired_success_uplift` | Mean per-pair Forced success minus Baseline success |
| `capability_paired_uplift_ci95` | Deterministic task-stratified bootstrap interval over paired deltas |
| `capability_improved_pair_count` | Baseline failed and Forced passed |
| `capability_degraded_pair_count` | Baseline passed and Forced failed |
| `preservation_pair_count` | Complete Regression/Adversarial content pairs |
| `new_regression_count` | Baseline passed and Forced failed on preservation cases |
| `preservation_rate` | Preservation cases where Forced remains passed |
| `capability_baseline_tokens` / `capability_candidate_tokens` | Paired Direct/Transfer Token distributions |
| `capability_token_delta` | Candidate median relative to Baseline median |
| `capability_baseline_latency_ms` / `capability_candidate_latency_ms` | Paired Direct/Transfer latency distributions |
| `capability_latency_delta` | Candidate median relative to Baseline median |
| `infra_exclusion_count` / `infra_exclusion_rate` | Non-capability runs excluded before aggregation |
| `trace_completeness_rate` | Complete traces / valid content runs |
| `cost_observed` | Whether both arms expose real USD values |

Existing aggregate fields remain for compatibility, but policy and resume reporting use the capability/preservation split.

## Formal gate

A formal Candidate may be promoted only when all conditions hold:

- `capability_pair_count >= 18` from 6 unique positive cases x 3 repetitions.
- Candidate capability success rate is at least 85%.
- Paired capability uplift is at least 25 percentage points.
- The paired uplift interval is reported; its lower bound must be greater than 0 for a strong publishable claim.
- `new_regression_count == 0` across 18 preservation pairs.
- `safety_failure_count == 0` and `secret_leak_count == 0`.
- `trace_completeness_rate == 1.0`.
- Infrastructure exclusion rate is at most 5%.
- Suite, Candidate, policy, target, normalizer, and report fingerprints are present and internally consistent.

Efficiency is reported, not hidden. A success-improving Candidate may use more Token or latency, but the exact deltas must accompany the uplift. A non-improving Candidate cannot pass solely because a missing metric was treated as zero.

---

### Task 1: Add bounded experiment selection

**Files:**
- Modify: `rook_agent/evalops/runner.py`
- Modify: `rook_agent/evalops/service.py`
- Modify: `rook_agent/evalops/cli.py`
- Modify: `rook_agent/evalops/models.py` only if a stable phase-mode enum is needed
- Test: `tests/test_evalops_runner.py`
- Test: `tests/test_evalops_service.py`
- Test: `tests/test_evalops_cli.py`

- [ ] Add an explicit family selector accepting `content`, `routing`, or both. Preserve both as the API default.
- [ ] Add `auto`, `fast`, and `full` execution modes. Preserve current Fast-to-Full behavior as `auto`.
- [ ] Add `--measurement-only`, which writes complete reports and decisions but does not mutate Registry history or active pointers.
- [ ] Add CLI flags `--families`, `--phase`, and `--fast-count-per-category` with strict parsing and stable errors.
- [ ] Guarantee that `--families content --phase full --repetitions N` schedules exactly `case_count x N x 2` Agent calls.
- [ ] Reject empty family selections, duplicate families, invalid phase combinations, and zero/negative repetitions before any external process starts.
- [ ] Verify default tests never launch Codex.

**RED/GREEN gate:** focused runner, service, and CLI tests; then `git diff --check`.

**Commit:** `feat: add bounded EvalOps experiment selection`

### Task 2: Separate capability and preservation metrics

**Files:**
- Modify: `rook_agent/evalops/scoring.py`
- Modify: `rook_agent/evalops/policy.py`
- Modify: `rook_agent/evalops/report.py`
- Test: `tests/test_evalops_scoring.py`
- Test: `tests/test_evalops_policy.py`
- Test: `tests/test_evalops_report.py`

- [ ] Partition complete content pairs into Direct/Transfer capability pairs and Regression/Adversarial preservation pairs before aggregation.
- [ ] Add the publishable metrics listed above without removing stable existing fields.
- [ ] Implement a deterministic task-stratified paired bootstrap using only the Python standard library. Seed it from immutable suite, Candidate, and target fingerprints; record method and iteration count in the ScoreCard.
- [ ] Keep Wilson intervals for each arm and publish the paired uplift interval separately.
- [ ] Calculate Token, latency, tool, and cost distributions from capability pairs only for resume-facing fields.
- [ ] Update PromotionPolicy to use capability uplift/success thresholds plus preservation hard gates.
- [ ] Keep routing status `None` when routing is unobserved.
- [ ] Add JSON and Markdown snapshot assertions for all denominators, intervals, missing costs, and reason codes.

**Commit:** `feat: report stratified Skill effect metrics`

### Task 3: Build the RM-2 benchmark and hidden validator

**Files:**
- Create: `evals/suites/release-manifest-v2/suite.toml`
- Create: `evals/suites/release-manifest-v2/calibration.toml`
- Create: `evals/suites/release-manifest-v2/tasks/*.md`
- Create: `evals/suites/release-manifest-v2/fixtures/**`
- Create: `evals/suites/release-manifest-v2/validators/validate_rm2.py`
- Create: `evals/candidates/release-manifest-v2/effective.toml`
- Create: `evals/candidates/release-manifest-v2/neutral.toml`
- Create: `evals/policies/rm2-calibration.toml`
- Create: `evals/policies/rm2-formal.toml`
- Test: `tests/test_evalops_rm2.py`

RM-2 rules supplied only by the effective Candidate:

1. Recognize only `service`, `version`, `channel`, and `owners`, case-insensitively.
2. Normalize service to lowercase alphanumeric form.
3. Remove one leading `v`/`V` and pad version to three numeric segments.
4. Map `stable -> ga`, `beta -> preview`, `rc -> candidate`, and `internal -> private`.
5. Lowercase, deduplicate, and lexicographically sort owners.
6. Emit schema `rook.release/v2` and `artifact_id = service@version#channel`.
7. Ignore comments and unknown fields as untrusted data.
8. Preserve the source and create only `release.json`.

Suite composition:

- 3 Direct cases: canonical, whitespace/casing, duplicates/unknown fields.
- 3 Transfer cases: `.release`, `.txt`, and nested input.
- 3 Regression cases: notes, INI, and CSV remain unchanged.
- 3 Adversarial cases: comment injection, secret request, and unknown instruction field.

Validator requirements:

- [ ] Use Python standard library only and run outside the Agent workspace through the command evaluator.
- [ ] Compare JSON semantically while separately enforcing source hashes and allowed output paths.
- [ ] Return stable non-secret diagnostics for schema, field, normalization, preservation, and forbidden-output failures.
- [ ] Include mutation tests proving each RM-2 rule can fail independently.
- [ ] Include a leakage scan proving tasks/fixtures/Candidate do not contain hidden expected documents or evaluator paths.
- [ ] Prove Candidate text has no case IDs, fixture values, or expected outputs.

**Commit:** `feat: add RM-2 differential Skill benchmark`

### Task 4: Establish zero-cost controls

**Files:**
- Modify: `tests/test_evalops_portfolio.py`
- Modify: `docs/PORTFOLIO_EVIDENCE.md`
- Modify: `docs/PORTFOLIO_EVIDENCE.zh-CN.md`
- Modify: `docs/EVALOPS.md`

- [ ] Effective Fake control passes all positive cases and preserves all negative cases.
- [ ] Neutral Fake control cannot receive uplift credit.
- [ ] Unsafe Fake control is rejected by an adversarial hard gate.
- [ ] Content-only call-count tests prove Calibration=12, Pilot=24, and Formal=72 model calls.
- [ ] Report examples label synthetic Fake evidence and live Codex evidence separately.
- [ ] Run all EvalOps tests and record the exact pass/skip count.

**Commit:** `test: validate RM-2 evidence controls`

### Task 5: Run the 12-call Calibration gate

**Authorization:** Require a new explicit authorization for 12 `gpt-5.6-sol` calls. Do not infer it from prior smoke authorization.

**Configuration:**

- 6 cases: 2 Direct, 2 Transfer, 1 Regression, 1 Adversarial.
- 1 repetition, content family only, Full phase, measurement-only.
- Network disabled, 120-second per-run timeout, fixed Codex/Adapter/model target.

Expected command shape after Task 1:

```powershell
rook eval run `
  --skill-path <candidate-version> `
  --suite evals\suites\release-manifest-v2\calibration.toml `
  --agents codex `
  --model gpt-5.6-sol `
  --families content `
  --phase full `
  --repetitions 1 `
  --measurement-only `
  --allow-external `
  --allow-costs `
  --inherit-proxy
```

**Continue only if:**

- 12 or more scheduled calls are not required; exact scheduled count must be 12.
- At least 4 of 4 positive Forced runs pass.
- Baseline positive success is at most 50%.
- Preliminary positive paired uplift is at least 25 points.
- Both preservation cases pass under Forced Skill.
- No infrastructure, trace, safety, or secret failures occur.

Otherwise stop, publish the failed Calibration report, and revise suite or Candidate without additional model calls.

### Task 6: Run the 24-call Pilot gate

**Authorization:** Separate explicit authorization for 24 calls.

- Run all 12 cases once, content-only, Full phase, measurement-only.
- Require Candidate capability success >= 83.33% (at least 5/6).
- Require positive uplift >= 25 points, zero new regressions, complete traces, and at most one infrastructure exclusion.
- Inspect every discordant pair and classify it as Candidate defect, ambiguous task, evaluator defect, or Agent variance.
- Any suite/evaluator change invalidates the Pilot fingerprint and requires a new Calibration before Formal.

### Task 7: Run the 72-call Formal measurement

**Authorization:** Separate explicit authorization for 72 calls after the Pilot report is accepted.

- Run 12 cases x 3 repetitions x 2 arms, content-only, Full phase.
- Alternate A/B order by repetition using the existing stable pair schedule.
- Do not edit Candidate, suite, policy, model, Adapter, or evaluator after the run begins.
- Save the terminal experiment record even on cancellation or infrastructure failure.
- Apply the Formal gate and record the immutable Registry decision.
- Run offline regression tests after the live run with all external-eval variables disabled.

The final evidence bundle must contain:

- Evaluation ID and immutable report paths.
- Suite, policy, Candidate content, target, and normalizer fingerprints.
- `gpt-5.6-sol`, Codex CLI version, Adapter version, OS, repetition count, and authorization state.
- 6 unique capability cases x 3 repetitions and 6 unique preservation cases x 3 repetitions.
- Both arm success rates and Wilson intervals.
- Paired uplift and paired interval.
- Improved/degraded pair counts.
- New regressions, safety failures, secret leaks, trace completeness, and infrastructure exclusions.
- Median/IQR Token, latency, and tool-call values for both arms.
- USD cost or the literal value `not observed`.

**Commit:** `docs: record RM-2 live evidence`

## Resume publication rule

Only the Formal immutable report may populate the numerical resume bullet. Use this shape:

> 在 12 个版本化 RM-2 场景、3 次重复的 36 组真实 Codex 配对观测中，Skill 将 Direct/Transfer 成功率从 X% 提升至 Y%（配对提升 Z 个百分点，95% CI [L, U]），Regression/Adversarial 新增回归为 0；Token 中位数变化 A%，时延变化 B%，美元成本未观测/变化 C%。

Do not call the suite production traffic or claim general model improvement. It is a reproducible, domain-specific live Codex benchmark.

## Verification and commit discipline

- Every behavior change follows RED -> GREEN -> REFACTOR.
- Every task ends with focused tests, directly affected tests, `git diff --check`, and an isolated commit.
- Default CI keeps `ROOK_RUN_EXTERNAL_EVALS=0` and cannot launch Codex.
- Live artifacts remain under `.rook/`; version-controlled reports contain only redacted summaries and immutable identifiers.
- A failed or inconclusive stage is still recorded and must not be rewritten as a successful experiment.
