# Rook execution scale benchmark

External/model calls: **disabled**.

| Workers | Throughput jobs/s | P95 ms | Success | Retries | Recovery |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 38.17 | 266.00 | 300/300 | 17 | 100.0% |
| 25 | 80.34 | 297.00 | 300/300 | 17 | 100.0% |
| 50 | 106.65 | 844.00 | 300/300 | 17 | 100.0% |

Evidence fingerprint: `14f8c1a8147aec51be78a1719f84b141a217a2ec47b2fed12288350878a41f02`

## Method

- Host: Windows 11, Python 3.12.7, 28 logical processors.
- Each profile enqueued 300 unique idempotency keys into a new SQLite WAL
  database.
- Each successful handler performed a deterministic 250ms payload.
- One recoverable pre-handler failure was injected every 17 jobs.
- Every retry, terminal state, duration, and queue event was persisted.

No Docker container, coding Agent, provider, or external API was used in this
microbenchmark. It isolates the durable scheduler and recovery control plane.

## Interpretation

Throughput rose from 38.17 jobs/s at 10 workers to 80.34 at 25 workers (2.10x)
and 106.65 at 50 workers (2.79x). All 900 terminal jobs succeeded, and all
51 injected faults were recovered within their retry budgets.

P95 remained close to the 250ms payload at 10 and 25 workers, then increased
to 844ms at 50. This is reported as single-node SQLite/host saturation, not
hidden as an average. The next production step, if workloads require more than
one host or sustained 50-worker saturation, is a network queue and distributed
lease service; this repository does not claim that scale today.

The JSON report is
[`docs/evidence/rook-execution-scale-2026-07-27.json`](evidence/rook-execution-scale-2026-07-27.json).
