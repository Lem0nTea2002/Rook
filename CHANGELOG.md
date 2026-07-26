# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.3] - 2026-07-27

### Added

- Published the sealed Adapter v11 Formal evidence: 72/72 live
  `gpt-5.4-mini` calls, 36 complete Baseline/Forced pairs, 100% trace
  completeness, and zero infrastructure exclusions.
- Added a stable profile-isolation readiness suite and redacted evidence
  summaries for the v11 readiness and Formal runs.

### Changed

- Formal evidence now reports the observed 25% to 100% paired success change
  (+75 percentage points), 16.7% lower median latency, 19.5% lower median
  token use, 33.3% fewer median tool calls, and zero new regressions.
- The Codex adapter now applies a versioned no-profile PowerShell execution
  policy and audits profile, Web Search, reconnect, sandbox, and isolation
  markers fail-closed.
- Portfolio and EvalOps documentation now distinguishes the promoted automatic
  gate from the still-pending human approval and deployment steps.

## [0.2.2] - 2026-07-24

### Fixed

- Native Windows Codex workspaces now use slash-normalized `-C` arguments so
  backslash escape sequences cannot corrupt the sandbox working directory.
- Codex Windows sandbox setup and `CreateProcessAsUserW` failures now fail
  closed as infrastructure errors, including runs whose outer process exits
  successfully.
- A bounded Codex reconnect event is accepted only when the process succeeds
  and a unique terminal event follows; generic stream errors still fail closed.
- Codex EvalOps now uses a controlled HTTP/SSE-only ChatGPT provider so blocked
  WebSocket connections cannot consume the run budget before HTTPS fallback.
- Windows agents now receive a versioned recovery policy after two consecutive
  restricted-language failures: one direct fallback attempt followed by a
  stable exhaustion marker instead of silent retries to the run deadline.
- Codex JSONL normalization now audits restricted-shell threshold, recovery,
  and exhaustion states and reports a specific restricted-shell timeout code.
- Codex EvalOps Adapter v7 now prohibits model-supplied tool working directories,
  requires relative forward-slash paths, and reports escaped Windows `cwd`
  failures separately as `codex_windows_tool_cwd_escape_error`.
- Rook system prompt v14 requires direct `py -c` recovery to remain a single
  shell-safe physical line and recognizes the live Constrained Language
  `Cannot create type` failure shape.
- Windows EvalOps subprocesses now hold
  `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` so system-idle
  sleep cannot suspend the runner's deadline loop. Guard acquisition fails
  closed, and guard restoration failures are recorded as cleanup failures.
- A timeout that exceeds its configured deadline by more than five seconds now
  carries `timeout_deadline_overrun`; Codex Adapter v8 maps it to the stable
  infrastructure code `codex_timeout_deadline_overrun`.
- Restricted-shell recovery now keeps the required mutation separate from
  auxiliary verification. A real mutation failure still emits
  `ROOK_SHELL_FALLBACK_EXHAUSTED`, while a completed write followed by an
  inconclusive auxiliary check emits
  `ROOK_POST_WRITE_VERIFICATION_INCONCLUSIVE` and reaches the deterministic
  evaluator instead of becoming an infrastructure exclusion.

### Changed

- The RM-2 Formal holdout now has an explicit repository-root output contract,
  a 180-second run boundary, and a new suite/Adapter fingerprint. The first
  authorized Formal attempt was aborted and is recorded as non-resume evidence.
- The separately authorized Adapter v5 readiness smoke completed exactly two
  HTTP/SSE calls with terminal traces, zero reconnect/fallback events, and zero
  infrastructure exclusions; the one-pair result remains non-Formal evidence.
- The subsequent 72-call Formal attempt stopped fail-closed after a Forced arm
  timed out without a terminal trace. Thirty-two calls started and 40 did not;
  no partial ScoreCard or resume metric is published.
- The restricted-shell remediation advances the Rook system prompt to v13,
  Codex Adapter to v6, and Codex Normalizer to v2. A separately authorized
  two-call smoke verified terminal bounded-stop feedback in both arms, but both
  runs were infrastructure-excluded, so readiness and Formal remain blocked.
- Follow-up remediation advances the system prompt to v14 and Codex Adapter to
  v7. It is offline-verified against the redacted v6 smoke failure shapes and
  was then verified by a separately authorized 2-call readiness smoke with zero
  infrastructure exclusions and complete terminal traces.
- The subsequent v7 Formal attempt stopped fail-closed after 30 calls started.
  Windows entered system-idle sleep during one subprocess, invalidating the
  wall-clock boundary; 29 process artifacts and 28 evaluated-run records were
  retained, 42 calls were not started, and no partial Formal metric is
  published. The host-sleep remediation advances the Adapter to v8 and requires
  a new readiness authorization before any fresh Formal run.
- A separately authorized Adapter v8 readiness smoke completed exactly two
  calls with complete terminal traces, zero infrastructure exclusions, and no
  deadline-overrun marker. The one-pair result remains non-Formal evidence.
- The subsequent v8 Formal stopped fail-closed after 13 calls started. Twelve
  terminal artifacts were retained, one in-flight call was stopped, and 59
  calls were not started after a Forced arm emitted the stable shell-fallback
  exhaustion marker. No partial ScoreCard or resume metric is published.
- The post-write recovery remediation advances the Rook system prompt to v15,
  Codex Normalizer to v3, and Codex Adapter identity to v9. It is offline
  verified only; v9 requires a separately authorized two-call readiness smoke
  before any new Formal authorization.

## [0.2.1] - 2026-07-19

### Added

- A sealed 12-case RM-2 Formal holdout whose case IDs and fixture contents are disjoint from Pilot, with a fail-closed Candidate content-hash lock.
- `rook eval trends` for redacted ScoreCard history, comparable-version deltas, fingerprint boundaries, SLO breaches, and governance counts.
- Ruff, incremental mypy, 85% EvalOps coverage, pip-audit, Python 3.11/3.12, and Dependabot quality gates.
- A version-controlled redacted Pilot evidence summary and honest dogfooding/incident ledger.

### Fixed

- Native Windows Codex workspace writes no longer create a split nested temporary writable root; both A/B arms use the same shell-write compatibility boundary.
- The 24-call Pilot now has a dedicated policy and cannot be evaluated against the 72-call Formal capability-pair threshold.

### Changed

- GitHub is the supported `pipx` installation source until a separately verified PyPI publication exists.
- The Formal protocol uses the sealed holdout with three repetitions for exactly 72 calls.

### Security

- Holdout execution rejects a changed Candidate before starting an Agent or model call.
- Dependency audit and weekly pip/GitHub Actions update checks are part of CI.

## [0.2.0] - 2026-07-18

### Added

- Rook Forge Skill Candidate quarantine, isolated Baseline/Forced/Routed exams, deterministic evaluators, ScoreCards, and target-specific promotion decisions.
- Immutable human approvals, independent Rook/Codex project deployments, stale and drift detection, transactional release journals, and atomic rollback.
- `rook eval`, `rook skill`, read-only `/forge`, strict Codex JSONL normalization, and opt-in live-evaluation boundaries.
- `rook eval demo`, a packaged zero-cost Fake Agent lifecycle that produces machine-readable and Markdown evidence without launching Codex.

### Changed

- Automatic `promoted` decisions now mean eligible for human approval; evaluation never activates a Skill as a side effect.
- Offline CI validates the installed CLI and complete Forge demo on Windows and Linux.
- GitHub-hosted workflows use current Node 24 action majors for checkout and Python setup.

### Security

- Codex evaluation disables Web Search and command networking, rejects duplicate JSON keys, and treats forbidden search events as policy violations.
- Candidate, artifact, deployment, and rollback paths reject traversal and symbolic-link escapes; unmanaged Codex Skill directories are never overwritten.
- Default tests and CI keep real Codex execution and model costs disabled.

[Unreleased]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ZHUMUJUN/Rook/tree/v0.2.0
