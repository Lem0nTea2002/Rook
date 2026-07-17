# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/ZHUMUJUN/Rook/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ZHUMUJUN/Rook/tree/v0.2.0
