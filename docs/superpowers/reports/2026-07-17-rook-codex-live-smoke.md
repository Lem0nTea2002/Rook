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
