from __future__ import annotations

import os
from pathlib import Path

import pytest

from rook_agent.execution.executors import DockerExecutionSpec, DockerExecutor


@pytest.mark.skipif(
    os.environ.get("ROOK_RUN_DOCKER_TESTS") != "1",
    reason="real Docker execution is opt-in",
)
def test_real_linux_container_is_networkless_and_reads_bound_workspace(
    tmp_path: Path,
) -> None:
    image = os.environ.get("ROOK_TEST_DOCKER_IMAGE")
    if not image:
        pytest.fail("ROOK_TEST_DOCKER_IMAGE must contain an image@sha256 digest")
    (tmp_path / "input.txt").write_text("trusted workspace\n", encoding="utf-8")
    program = (
        "from pathlib import Path; import socket; "
        "text=Path('input.txt').read_text(); "
        "\ntry:\n socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n"
        "except OSError:\n print(text, end='')\n"
        "else:\n raise SystemExit('container network unexpectedly available')"
    )

    result = DockerExecutor().execute(
        DockerExecutionSpec(
            image=image,
            command=("python", "-c", program),
            workspace=tmp_path,
            timeout_seconds=30,
            cpus=1,
            memory_mb=256,
            pids_limit=64,
        )
    )

    assert result.succeeded is True
    assert result.stdout == "trusted workspace\n"
