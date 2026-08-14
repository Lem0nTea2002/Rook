# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-07-30

### Added

- Added explicit `ASK`, `AUTO`, and local-only `FULL` permission modes with
  legacy configuration migration, session-scoped grants, and an explicit FULL
  risk acknowledgement that remote and isolated runtimes cannot inherit.
- Added deterministic recovery detection for verified tool, validation,
  alternative-strategy, and user-correction recoveries without model calls.
- Added user-reviewed project memory under `.rook/memory/`, schema-staleness
  checks, bounded prompt loading, and explicit routing of cross-project lessons
  into quarantined Rook Forge Candidates.
- Added a structured learning review viewer plus TUI states for permission
  modes, grouped failures, recovery opportunities, Candidate quarantine, and
  long-trajectory scrolling.

### Changed

- Tool schemas now reject additional properties by default and validate
  required fields, basic types, enum values, and path shape before permission
  preflight or execution.
- Repeated identical tool failures execute at most once: the second attempt
  requires a changed strategy and the third ends the turn.
- Tool start/result events now share one card; successful cards collapse by
  default, repeated failures aggregate, and raw payloads remain in the
  transcript viewer.

### Fixed

- Made the documented pipx install deterministic with `--backend pip`, avoiding
  failures when an older unrelated `uv` executable is present on `PATH`.
- Restored visible scrollbars and stopped automatic follow mode while the user
  reads history, with a visible new-message indicator.
- Kept permission requests to one updatable card instead of duplicating the
  runtime event and pending-input prompt.

## [0.4.2] - 2026-07-30

### Added

- Added in-session steering with `Enter`, FIFO follow-up tasks with `Alt+Enter`,
  queue recall, paused failure states, and transcript-visible queue cards.
- Added a reproducible three-minute interview demo covering a live Coding Task
  and the deterministic Forge exam, gate, approval, deployment, drift, and
  rollback lifecycle.

### Changed

- Kept `/help` in the local TUI transcript as grouped Markdown without writing
  it to the Agent session or model context.
- Permission requests now open a keyboard picker automatically. `Allow once`
  is highlighted, but execution still requires an explicit Enter confirmation.
- Raised the development test baseline to pytest 9 and removed the unused
  third-party snapshot plugin; Rook's repository-owned SVG comparator remains.

### Fixed

- Preserved queued-task isolation across permission waits, cancellation, late
  steering, Provider failures, and session changes.
- Kept explicit typed permission answers authoritative even while the picker is
  open, preventing a typed `deny` from accepting the highlighted option.

## [0.4.1] - 2026-07-29

### Added

- Added the `rook-pixel` and `rook-high-contrast` Textual themes, a compact
  pixel-bird identity, grouped `/help` viewer, and 21 deterministic SVG
  snapshots across three terminal sizes.

### Changed

- Reworked the TUI into the Night Flight Mint palette with clearer role labels,
  square interaction borders, a focused metadata bar, and higher-contrast
  command, picker, message, activity, and composer states.
- Refreshed the README TUI screenshots and product demo GIF from real Textual
  widgets without model calls.

### Fixed

- Forced TrueColor only while constructing the full-screen TUI so inherited
  `NO_COLOR=1` and `TERM=dumb` no longer erase the interface palette, while
  ordinary CLI output and the parent process environment remain unchanged.
- Moved `/help` out of the transcript into a dismissible local page so command
  discovery no longer consumes conversation space or context.
- Kept SVG snapshot comparison inside the test suite and raised the development
  pytest floor to 9.0.3, avoiding the known vulnerability forced by the latest
  third-party Textual snapshot plugin.

## [0.4.0] - 2026-07-29

### Added

- Added a selectable Textual coding workbench with a searchable Slash Command
  palette, `@file` references, controlled shell mode, diff/transcript viewers,
  clipboard actions, prompt history, external-editor support, and bounded
  rendering for long sessions.
- Added the local Rook Mobile Channel gateway for controlling whitelisted
  projects from private Feishu or WeChat conversations.
- Added the official Feishu `lark-oapi` long-connection transport and a native
  Python implementation of Tencent's iLink bot protocol.
- Added durable SQLite message deduplication, project bindings, conversation
  cursors, expiring approval records, restart recovery, and leased job
  execution.
- Added `rook channel` setup, pairing, project, serve, status, login, and
  current-user Windows autostart commands, plus bilingual operating guides.

### Changed

- Extracted a shared `RookRuntime` so the TUI and mobile gateway use the same
  Agent loop, permissions, sessions, Skill discovery, and project execution
  lock.
- Added the optional `im` dependency extra; default installation and offline CI
  do not require either channel SDK.

### Fixed

- Corrected the WeChat iLink channel-version payload and Windows QR rendering.
- Limited IM replies to the final Agent answer instead of exposing internal tool
  traces.
- Bound mobile approval records to the real tool, action, target, and action
  hash.
- Scoped Todo reminders to the current user turn and included untracked files
  in the channel diff.

### Security

- Remote access is fail-closed: one paired user, private text messages only,
  explicit absolute-path project allowlists, single-use pairing codes, and no
  remote arbitrary shell command.
- Tool approvals are limited to `allow once` or `deny`, bind the exact action
  hash, expire safely across restarts, and cannot grant permanent permission.
- Sensitive channel and execution failures are redacted before being persisted
  or returned to IM clients.

## [0.2.7] - 2026-07-28

### Added

- Added an automatic first-run Provider setup wizard plus the explicit
  `rook config setup` command for OpenAI, DeepSeek, Qwen, Moonshot, Zhipu,
  OpenRouter, Anthropic, Ollama, and custom OpenAI-compatible endpoints.
- API keys are entered with hidden input and stored in the operating-system
  credential manager; generated TOML files contain only the credential name.

### Changed

- Interactive startup now retries after setup, while non-interactive runs fail
  closed with an actionable setup command and never prompt in CI.
- Environment variables keep priority over system credentials and config files.

## [0.2.6] - 2026-07-28

### Added

- Added the Rookie baby-rook mascot, a friendlier mint-and-navy Textual
  welcome screen, and refreshed repository/website visuals.
- Added `rook repo issue-pr-demo`, a deterministic Issue-to-reviewed-Draft-PR
  walkthrough with a real local Git branch, tests, path and secret gates,
  hash-chained lifecycle evidence, and no implicit GitHub write.
- Added a redacted ten-task Rook-only live dogfood v3 record: 10/10 validators
  passed under a 120-call ceiling, with one Provider-limit terminal retained.

### Fixed

- Replaced the release-version assertion in the portfolio test with a value
  derived from package metadata, preventing routine releases from breaking
  every Linux/Windows matrix job on a stale hard-coded tag.

## [0.2.5] - 2026-07-28

### Added

- Added redacted evidence for a 24-call Candidate v5 cross-repository holdout
  and a bounded ten-task Rook Coding Agent live dogfood run.
- Added a pinned 24-task SWE-bench Lite full-repository catalog spanning
  pytest, scikit-learn, and Sphinx, with verified Issue/maintenance-PR
  provenance and a hidden-verifier data boundary.
- Added a SQLite WAL execution queue with idempotent enqueue, expiring leases,
  recovery, retry budgets, dead letters, event history, 1-50 concurrent
  workers, rate limiting, and deterministic fault injection.
- Added digest-allowlisted, networkless Linux Docker execution with resource
  ceilings, bounded redacted output, optional Prometheus/OTLP adapters, an
  Ubuntu real-container CI check, and a reproducible scale report.

### Changed

- The local pytest dogfood runner now enforces explicit per-task Provider,
  tool-round, and wall-clock ceilings, supports one-based continuation, records
  terminal reasons, and atomically persists every completed task.
- Agent Todo self-checks now convert Provider limits and cancellation into
  stable terminal responses instead of escaping the tool loop.
- Coverage enforcement now uses two-decimal precision so a rounded 84.57%
  cannot satisfy the 85% EvalOps gate; additional PR Gate tests cover path
  escape, unresolved Git refs, malformed governed assets, invalid provenance,
  and unresolved Candidate locks.

## [0.2.4] - 2026-07-27

### Added

- Added `rook eval record-decision` to fail-closed verify a measurement-only
  report against the current Candidate, Suite, policy, Agent/Adapter/Normalizer
  fingerprints, complete terminal artifacts, reconstructed per-case evidence,
  operator-supplied ScoreCard SHA-256, ScoreCard fingerprint, and promotion
  policy before recording eligibility.
- Added redacted evidence for two live Skills on two public repositories and
  five isolated Rook Coding Agent dogfood tasks.
- Added a frozen Candidate v5 cross-repository holdout with six
  Direct/Transfer/Regression cases, pinned provenance, hidden validators, and
  a separately authorized 24-call live boundary.
- Added `rook eval pr-gate` and a cost-free GitHub pull-request workflow that
  validates strict Candidates, Suites, policies, Candidate locks, provenance,
  and fixture hashes, then uploads a fingerprinted JSON report.
- Added a pinned ten-task Rook Coding Agent dogfood v2 dataset and provider
  call, Token, and Skill identity telemetry for the next authorized rerun.

### Changed

- The promoted 72-call Formal decision is now linked to an immutable human
  approval and repository-level Codex deployment of the exact Candidate hash.
  A controlled mutation was detected as drift and restored to the active state.
- Both independent real-repository Candidates were rejected after 16/16 live
  calls because they introduced regressions. They remain quarantined and are
  published as negative evidence rather than an improvement claim.
- Candidate v5 completed a separate Adapter v12 72-call Formal, was
  independently approved and deployed as the successor to v1, and exercised a
  real v5-to-v1-to-v5 transaction chain during audit repair.
- Runtime control reminders no longer create logical user turns. Global Skill
  metadata routing requires a distinctive name or trigger signal; global
  catalog text is omitted from the provider prompt; active Skills are bounded,
  deduplicated, and cleared at confirmed task boundaries.

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

[Unreleased]: https://github.com/Lem0nTea2002/Rook/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Lem0nTea2002/Rook/compare/v0.4.2...v0.5.0
[0.4.2]: https://github.com/Lem0nTea2002/Rook/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/Lem0nTea2002/Rook/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Lem0nTea2002/Rook/compare/v0.2.7...v0.4.0
[0.2.7]: https://github.com/Lem0nTea2002/Rook/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/Lem0nTea2002/Rook/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ZHUMUJUN/Rook/tree/v0.2.0
