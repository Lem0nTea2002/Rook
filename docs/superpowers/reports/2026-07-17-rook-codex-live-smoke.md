# Rook Codex live smoke report

Date: 2026-07-17 (Asia/Shanghai)

## Authorization and target

- The user explicitly authorized external Codex calls and model quota usage for at most eight live smoke runs.
- Target model: `gpt-5.6-sol`.
- Codex CLI: `codex-cli 0.144.1`.
- Target fingerprint: `7f1cd7e21cf575ecd1eac052ea9b7085`.
- Suite: `codex-only-demo` (`34bf9e64045c0d1c0189fcf243fe61e6`).
- Live smoke policy fingerprint: `live-smoke-policy`.

## Execution result

- Pytest harness: `1 passed, 2 deselected in 245.08s`.
- External Agent runs: 4 of the authorized maximum of 8.
- Full Gate calls: 0; the Fast Gate stopped the evaluation.
- All four runs ended with `codex_timeout` after approximately 60.45-60.53 seconds.
- Fast Gate and final decision: `quarantined` with reason code `trace_incomplete`.
- The sanitized stderr artifacts record repeated WebSocket TLS failures: `tls handshake eof` while connecting to the ChatGPT Codex responses endpoint.
- One run emitted an Agent message and completed command execution before timeout, but no run emitted a complete terminal trace.

The passing pytest result proves that the opt-in harness bounded the run count and persisted an immutable report. It does not mean the Candidate or model passed the task.

## Observability

- Baseline and Candidate success rates in the incomplete smoke report are both 0, but they are not valid model-effect evidence because every trace timed out.
- Token and monetary cost metrics were not observed.
- Codex routing precision and recall were not observed.
- No real success-rate uplift or cost-improvement claim is supported by this smoke.

## Preserved local artifacts

The complete sanitized artifact and workspace set is preserved under:

`.rook/live-smoke/evaluation-84ab041a916a4756b7488329c475c354`

The report JSON is:

`.rook/live-smoke/evaluation-84ab041a916a4756b7488329c475c354/artifacts/reports/evaluation-84ab041a916a4756b7488329c475c354/scorecard.json`

Any retry requires separate user authorization. Before retrying, investigate the WebSocket TLS path and use a timeout greater than 60 seconds; do not treat a longer timeout alone as proof that the network issue is fixed.

## Follow-up network diagnosis

The connection fault was isolated without another model call:

- Windows had a user proxy at `127.0.0.1:10808`, served by `verge-mihomo`.
- Codex reported no proxy environment variables, while its direct Responses WebSocket handshake timed out after 15 seconds.
- With process-local `HTTP_PROXY`, `HTTPS_PROXY`, and `ALL_PROXY` set to `http://127.0.0.1:10808`, `codex doctor --json` reported `HTTP 101 Switching Protocols` in about 1.5 seconds.
- Rook intentionally strips host environment variables. The follow-up fix adds explicit `rook eval run --inherit-proxy` support and keeps the safe default unchanged.
- The proxy values are allowed only after explicit opt-in and are not persisted in process metadata or reports.

The system `hosts` file also contains a pre-existing `127.0.0.1 api.openai.com` entry. It was not modified because this smoke used ChatGPT authentication and the verified fix targets the ChatGPT WebSocket route. API-key evaluations should audit that entry separately.

## Post-fix transport smoke

The user authorized a second live smoke under the same limit of eight runs. With explicit proxy inheritance enabled:

- Evaluation: `evaluation-9ed196ad3f6c48498d98b55bb3585cd2`.
- External Agent runs: 8 of 8; every Codex process exited successfully.
- Process duration range: 12.155-35.172 seconds.
- Trace completeness: 1.0; infrastructure errors: 0; safety failures: 0.
- Fast Gate: `continue_full`; the four Full Gate runs completed.
- Final decision: `rejected` with reason code `insufficient_effect`.

The transport issue is therefore resolved. The task outcome did not pass: both Baseline and Forced Skill had a 0 success rate for the single Direct smoke case. The Forced Skill run reported that the workspace was read-only, so `result.txt` was not created. This is a separate Windows Codex sandbox/write-permission issue, not a WebSocket failure and not valid Skill-effect evidence.

For the content pair, the observed Baseline total token count was 33,915 and the Forced Skill count was 51,983; median latency was 17.219 seconds versus 31.797 seconds. These single-pair values are diagnostic only, not resume metrics. Monetary cost and routing activation remained unobserved.

The run also exposed a report persistence bug: the text redactor treated JSON keys containing `tokens` as assigned secrets and corrupted the persisted ScoreCard JSON. Report persistence now uses structured `ArtifactStore.write_json`, with a regression test for numeric token metrics. The untouched original and a locally recovered, parseable report are retained under `.rook/live-smoke/evaluation-9ed196ad3f6c48498d98b55bb3585cd2`.

## Windows write-sandbox diagnosis

The write failure had two Windows-specific causes. EvalOps used `--ignore-user-config` while only setting the generic `--sandbox workspace-write`, and the opt-in pytest smoke created its workspace under a deep user Temp path that the restricted sandbox identity could not traverse. A captured shell command showed its effective cwd falling back to the PowerShell installation directory, while an absolute write to the Temp workspace failed with access denied.

The Adapter now explicitly adds `windows.sandbox="unelevated"` on `win32`, keeps `workspace-write` and `approval_policy="never"`, and never bypasses the sandbox. Linux and macOS command construction remains unchanged. The opt-in live smoke now stores its isolated root under the project's ignored `.rook/external-smoke/<run-id>` tree instead of the user Temp hierarchy. Cross-platform command tests fix the backend behavior in the Adapter contract.

## Post-sandbox live verification

The repaired project-local workspace path was exercised with a bounded `gpt-5.6-sol` run:

- Evaluation: `evaluation-308b5aa43f3b449f90a6a4532e949e64`.
- External Agent runs: 4; process durations were 31.717, 41.047, 60.563, and 60.608 seconds.
- Every treatment created `result.txt`, confirming that the Windows write sandbox and effective working directory were repaired.
- Two runs completed normally. The Forced Skill produced evaluator-passing file contents but crossed the 60-second process deadline; the matching content Baseline also timed out after creating its file.
- The routing Baseline passed. Routed Skill produced a trailing newline and failed exact-text evaluation, which is a task outcome rather than an infrastructure failure.
- Fast Gate quarantined the Candidate with `trace_incomplete`; trace completeness was 0.5. No Full Gate calls were made.

This run is transport and sandbox evidence, not Skill-uplift evidence. Its sample is too small, two statuses hit the deadline, and Codex still does not expose a reliable native Skill activation event.

## Prompt-isolation follow-up

The run also showed that `--ignore-user-config` does not by itself remove the user Skill catalog. Unrelated Skills consumed context and one Baseline invoked a global workflow Skill. Rook now disables Codex plugins and memories for every EvalOps run. For content-effect pairs it additionally sets `skills.include_instructions=false`; Forced Skill still reads the mounted Candidate from the explicit relative path, while Baseline has no ambient Skill catalog. Routing-effect pairs keep discovery enabled so they remain behaviorally meaningful.

A no-model `codex debug prompt-input` check confirmed that the content-pair override removes the Skill instruction block. An attempted HOME/USERPROFILE override was rejected: native Windows sandbox child creation returned access denied, so that approach was reverted. The opt-in live case now allows 120 seconds, but no further model run was started after the bounded six-call diagnostic sequence.

Structured report persistence now explicitly preserves the non-string `secret_leak_count` and `token_improvement` metric scalars while continuing to redact string or nested values under sensitive keys. This closes the remaining ScoreCard schema corruption without weakening the default ArtifactStore policy.

## Authorized post-hardening A/B

The user authorized one additional 4-8 run A/B using `gpt-5.6-sol`. Evaluation `evaluation-6a32ef3954d54fdc8265d2ca46ba0c4b` completed four Fast Gate runs and stopped before Full Gate because there was no positive Fast Gate effect.

- All four Codex processes succeeded in 35.922-62.313 seconds.
- All four normalized traces were complete; infrastructure, safety, secret-leak, and evaluator failure counts were zero.
- Content Baseline and Forced Skill both passed, for 100% versus 100% task success and 0 percentage-point paired uplift.
- Content Baseline used 60,165 total tokens and 45.968 seconds. Forced Skill used 74,565 total tokens and 62.313 seconds: 23.93% more tokens and 35.56% more latency.
- Content Baseline made two tool calls; Forced Skill made three.
- Routing Baseline and Routed Skill both passed. Their diagnostic totals were 69,920 versus 70,304 tokens and 35.922 versus 43.109 seconds.
- Fast Gate rejected the Candidate with `no_fast_gate_improvement`; no additional model calls were made.
- Monetary cost remained unobserved because the Codex event stream did not provide a USD cost field. Native Skill activation also remained unobserved, so routing precision and recall are `None`.

This is valid single-case live evidence for transport, isolation, result evaluation, Token accounting, and latency accounting. It is not statistically sufficient resume evidence for a generalized success-rate uplift. The run also exposed that the Fast Gate conversion marked routing rejected even when `routing_observed=false`; the conversion now leaves routing status and reason unset for content-only rejection while preserving global safety rejection semantics.
