# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ZHUMUJUN/Rook/tree/v0.2.0
