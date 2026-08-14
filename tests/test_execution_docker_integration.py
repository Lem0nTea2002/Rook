from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from rook_agent.benchmarks.native import NativeContainerBackend, SealedValidator
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


@pytest.mark.skipif(
    os.environ.get("ROOK_RUN_DOCKER_TESTS") != "1",
    reason="real Docker execution is opt-in",
)
def test_native_backend_is_non_root_networkless_and_does_not_inherit_secrets(
    tmp_path: Path,
) -> None:
    image = os.environ.get("ROOK_TEST_DOCKER_IMAGE")
    if not image:
        pytest.fail("ROOK_TEST_DOCKER_IMAGE must contain an image@sha256 digest")
    (tmp_path / ".git").mkdir()
    hidden_patch = tmp_path.parent / "hidden.patch"
    hidden_patch.write_text("", encoding="utf-8")
    empty_hash = hashlib.sha256(b"").hexdigest()
    validator = SealedValidator(
        task_id="docker-readiness",
        validator_id="validator-docker-readiness",
        image=image,
        test_patch_path=hidden_patch,
        command=("python", "-c", "pass"),
        regression_command=("python", "-c", "pass"),
        test_patch_sha256=empty_hash,
        source_fingerprint=empty_hash,
        environment_fingerprint=empty_hash,
    )
    program = (
        "from pathlib import Path; import os, socket; "
        "assert os.geteuid() != 0; "
        "assert 'OPENAI_API_KEY' not in os.environ; "
        "assert 'DEEPSEEK_API_KEY' not in os.environ; "
        "\ntry:\n Path('/rook-root-write').write_text('forbidden')\n"
        "except OSError:\n pass\n"
        "else:\n raise SystemExit('root filesystem unexpectedly writable')\n"
        "\ntry:\n socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n"
        "except OSError:\n pass\n"
        "else:\n raise SystemExit('container network unexpectedly available')\n"
        "Path('native-output.txt').write_text('isolated\\n'); "
        "print(os.geteuid())"
    )

    result = NativeContainerBackend().run(
        validator=validator,
        workspace=tmp_path,
        command=("python", "-c", program),
        timeout_seconds=30,
    )

    assert result.succeeded is True
    assert int(result.stdout.strip()) > 0
    assert (tmp_path / "native-output.txt").read_text(encoding="utf-8") == (
        "isolated\n"
    )
