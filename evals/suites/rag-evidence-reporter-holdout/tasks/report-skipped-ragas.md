Read `reports/answer.json` and create `evaluation-summary.json` at the
repository root.

Use schema `rook.rag-eval-summary/v1`, preserve the dataset path and case count,
and report the Ragas measurement honestly. If the report did not evaluate any
case, mark Ragas as `not_observed` and preserve its reason rather than treating
missing scores as success or zero. Preserve the source byte-for-byte and create
no other file.
