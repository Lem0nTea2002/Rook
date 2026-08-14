from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from rook_agent.execution.models import JobStatus
from rook_agent.execution.queue import IdempotencyConflict, SQLiteJobQueue


def test_enqueue_is_idempotent_and_detects_payload_conflicts(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")

    first = queue.enqueue(
        idempotency_key="suite/task/attempt-1",
        payload={"task_id": "task-1"},
        max_attempts=3,
    )
    second = queue.enqueue(
        idempotency_key="suite/task/attempt-1",
        payload={"task_id": "task-1"},
        max_attempts=3,
    )

    assert first.job_id == second.job_id
    assert queue.stats()[JobStatus.QUEUED] == 1

    with pytest.raises(IdempotencyConflict):
        queue.enqueue(
            idempotency_key="suite/task/attempt-1",
            payload={"task_id": "different"},
            max_attempts=3,
        )


def test_claim_complete_and_event_history_are_atomic(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")
    submitted = queue.enqueue(
        idempotency_key="task-1",
        payload={"task_id": "task-1"},
    )

    claimed = queue.claim(owner="worker-a", lease_seconds=30)
    assert claimed is not None
    assert claimed.job_id == submitted.job_id
    assert claimed.status is JobStatus.RUNNING
    assert claimed.attempts == 1

    completed = queue.complete(
        claimed.job_id,
        owner="worker-a",
        result={"status": "passed"},
    )
    assert completed.status is JobStatus.SUCCEEDED
    assert queue.get(claimed.job_id).result == {"status": "passed"}
    assert [event.event for event in queue.events(claimed.job_id)] == [
        "enqueued",
        "claimed",
        "succeeded",
    ]


def test_expired_lease_is_recovered_and_retry_budget_is_enforced(
    tmp_path: Path,
) -> None:
    now = [100.0]
    queue = SQLiteJobQueue(tmp_path / "queue.db", clock=lambda: now[0])
    job = queue.enqueue(
        idempotency_key="task-1",
        payload={"task_id": "task-1"},
        max_attempts=2,
    )

    first = queue.claim(owner="crashed", lease_seconds=5)
    assert first is not None
    now[0] = 106.0
    assert queue.recover_expired_leases() == 1

    second = queue.claim(owner="replacement", lease_seconds=5)
    assert second is not None
    assert second.attempts == 2
    queue.fail(
        job.job_id,
        owner="replacement",
        reason_code="container_exit_137",
        retryable=True,
    )

    failed = queue.get(job.job_id)
    assert failed.status is JobStatus.DEAD_LETTER
    assert failed.last_error == "container_exit_137"
    assert queue.claim(owner="worker-c", lease_seconds=5) is None


def test_concurrent_enqueue_keeps_one_job_per_idempotency_key(
    tmp_path: Path,
) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")

    def submit(_: int) -> str:
        return queue.enqueue(
            idempotency_key="same-task",
            payload={"task_id": "task-1"},
        ).job_id

    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(submit, range(64)))

    assert len(set(ids)) == 1
    assert queue.stats()[JobStatus.QUEUED] == 1


def test_wrong_owner_cannot_complete_a_leased_job(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(tmp_path / "queue.db")
    queue.enqueue(idempotency_key="task-1", payload={"task_id": "task-1"})
    claimed = queue.claim(owner="worker-a", lease_seconds=30)
    assert claimed is not None

    with pytest.raises(PermissionError, match="lease owner"):
        queue.complete(
            claimed.job_id,
            owner="worker-b",
            result={"status": "passed"},
        )
