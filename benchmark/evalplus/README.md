# EvalPlus Benchmark

This runner generates EvalPlus-compatible samples with the configured Rook
provider, then lets the official EvalPlus evaluator score them.

EvalPlus is lightweight compared with SWE-bench and Terminal-Bench: it runs pure
Python function tasks and does not require Docker.

## Setup

Install EvalPlus in the local virtual environment:

```sh
.venv/bin/python -m pip install evalplus
```

Configure the normal Rook provider environment variables:

```sh
export ROOK_PROVIDER=openai-compatible
export ROOK_BASE_URL=...
export ROOK_API_KEY=...
export ROOK_MODEL=...
```

## Generate HumanEval+ Mini Samples

```sh
.venv/bin/python benchmark/evalplus/runner.py generate \
  --dataset humaneval \
  --mini \
  --id-range 0 3 \
  --samples-out runs/evalplus/humaneval-mini-rook.jsonl \
  --summary-out runs/evalplus/humaneval-mini-rook-summary.json
```

## Evaluate Samples

```sh
.venv/bin/python benchmark/evalplus/runner.py evaluate \
  --dataset humaneval \
  --samples runs/evalplus/humaneval-mini-rook.jsonl \
  --mini \
  --id-range 0 3 \
  --summary-out runs/evalplus/humaneval-mini-rook-eval.json
```

The generated samples use EvalPlus's JSONL format:

```json
{"task_id":"HumanEval/0","solution":"..."}
```

## Small MBPP+ Run

EvalPlus does not currently ship an MBPP+ mini split, so use `--id-range` for a
small smoke run:

```sh
.venv/bin/python benchmark/evalplus/runner.py generate \
  --dataset mbpp \
  --id-range 2 5 \
  --samples-out runs/evalplus/mbpp-rook.jsonl \
  --summary-out runs/evalplus/mbpp-rook-summary.json
```
