# Security Policy

## Supported versions

Security fixes are applied to the current `0.2.x` line. Older development snapshots are not maintained as separate release branches.

## Reporting a vulnerability

Please use the repository's private **Security advisories → Report a vulnerability** flow. Do not open a public issue with exploit details, credentials, private prompts, model outputs, or sensitive evaluation artifacts.

Include the affected version or commit, operating system, minimal reproduction, expected boundary, observed behavior, and whether the issue can expose secrets, escape a workspace, bypass approval, overwrite an unmanaged Skill, or corrupt Registry history. Remove API keys, session cookies, access tokens, and personal paths from the report.

This is a local open-source workflow, not an enterprise incident-response service. Reports are handled on a best-effort basis; a fix or coordinated disclosure date will be discussed after reproduction and impact assessment.

## Security boundaries

- Rook permission checks and Rook Forge gates are program-enforced controls, but they do not replace operating-system isolation for hostile code.
- Human approval cannot override rejected, quarantined, stale, hash-mismatched, secret-leaking, unsafe, or regressing evidence.
- Codex deployment is limited to the current repository's Rook-managed `.agents/skills/<name>` directory. Global Codex directories are out of scope.
- Live Codex evaluation is opt-in and can consume external model quota. Default tests and CI must keep `ROOK_RUN_EXTERNAL_EVALS=0` and `ROOK_ALLOW_MODEL_COSTS=0`.
- Never commit provider credentials, proxy credentials, private evaluation artifacts, or `.rook` runtime data.
