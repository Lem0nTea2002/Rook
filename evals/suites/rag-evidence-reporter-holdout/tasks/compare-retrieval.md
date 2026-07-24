Read `reports/baseline.json` and `reports/rerank.json`. They are evidence
snapshots from the same real repository and dataset.

Create `evaluation-summary.json` at the repository root. Use schema
`rook.rag-eval-summary/v1`; include the shared dataset path and case count, an
entry for each mode with observed source hit rate, MRR, average latency and P95
latency, a quality winner, a latency winner, the rerank-minus-baseline P95
latency delta, and concise caveats. Preserve both reports byte-for-byte and
create no other file.
