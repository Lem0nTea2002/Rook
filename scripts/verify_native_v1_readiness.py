"""在首次模型调用前验证 Native v1 的三个 smoke 环境。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from rook_agent.benchmarks._utils import write_json_exclusive
from rook_agent.benchmarks.native import (
    NativeContainerBackend,
    NativeTaskCatalog,
    SealedValidatorManifest,
)
from rook_agent.execution.repository import GitRepositoryMaterializer


_IMPORTS = {
    "https://github.com/pytest-dev/pytest": "_pytest",
    "https://github.com/scikit-learn/scikit-learn": "sklearn",
    "https://github.com/sphinx-doc/sphinx": "sphinx",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--validators", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if root.exists():
        raise FileExistsError("Native readiness 输出目录已存在，禁止覆盖")
    root.mkdir(parents=True)
    catalog = NativeTaskCatalog.load(args.tasks, excluded_task_ids=())
    validators = SealedValidatorManifest.load(
        args.validators,
        catalog=catalog,
    )
    sources = _sources(args.source)
    if set(sources) != set(catalog.repository_counts):
        raise ValueError("Native readiness 必须提供精确的三个仓库源")

    selected = []
    for repository in sources:
        selected.append(
            next(task for task in catalog.tasks if task.repository == repository)
        )

    backend = NativeContainerBackend()
    results: list[dict[str, object]] = []
    for task in selected:
        validator = validators.for_task(task.task_id)
        workspace = GitRepositoryMaterializer(
            root / "workspaces" / task.task_id
        ).materialize(
            task,
            source=sources[task.repository],
            allow_network=False,
        )
        import_name = _IMPORTS[task.repository]
        backend.hydrate_workspace(
            validator=validator,
            workspace=workspace,
        )
        isolation = backend.run(
            validator=validator,
            workspace=workspace,
            command=(
                "/opt/miniconda3/envs/testbed/bin/python",
                "-c",
                _isolation_program(import_name),
            ),
            timeout_seconds=120,
        )
        if not isolation.succeeded:
            raise RuntimeError(f"Native 容器隔离失败：{task.task_id}")

        status = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if status.stdout:
            raise RuntimeError(f"Native 构建制品污染 Git 工作区：{task.task_id}")

        regression = backend.run(
            validator=validator,
            workspace=workspace,
            command=validator.regression_command,
            timeout_seconds=900,
        )
        if not regression.succeeded:
            raise RuntimeError(f"Native 基线回归失败：{task.task_id}")

        patch = validator.test_patch_path.read_bytes()
        if hashlib.sha256(patch).hexdigest() != validator.test_patch_sha256:
            raise ValueError(f"Native 隐藏测试补丁漂移：{task.task_id}")
        subprocess.run(
            ("git", "apply", "--whitespace=nowarn", str(validator.test_patch_path)),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        hidden = backend.run(
            validator=validator,
            workspace=workspace,
            command=validator.command,
            timeout_seconds=900,
        )
        if hidden.succeeded or hidden.exit_code != 1:
            raise RuntimeError(
                f"Native 隐藏测试未形成预期能力失败：{task.task_id}"
            )
        results.append(
            {
                "task_id": task.task_id,
                "repository": task.repository,
                "image": validator.image,
                "base_commit": task.base_commit,
                "isolation": "passed",
                "regression": "passed",
                "hidden_baseline": "failed_as_expected",
                "regression_duration_ms": regression.duration_ms,
                "hidden_duration_ms": hidden.duration_ms,
            }
        )

    receipt = {
        "schema_version": 1,
        "benchmark_version": "native-v1",
        "catalog_fingerprint": catalog.fingerprint,
        "validator_manifest_sha256": validators.fingerprint,
        "status": "ready",
        "task_count": len(results),
        "tasks": results,
    }
    write_json_exclusive(root / "readiness.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _sources(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        repository, separator, raw_path = value.partition("=")
        if not separator or repository in result:
            raise ValueError("--source 格式必须为唯一的 REPOSITORY=PATH")
        path = Path(raw_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"Native 本地仓库源不存在：{path}")
        result[repository] = path
    return result


def _isolation_program(import_name: str) -> str:
    return (
        "from pathlib import Path; import importlib, os, socket; "
        f"module=importlib.import_module({import_name!r}); "
        "path=str(Path(module.__file__).resolve()).replace('\\\\','/'); "
        "assert path.startswith('/workspace/'), path; "
        "assert 'OPENAI_API_KEY' not in os.environ; "
        "assert 'DEEPSEEK_API_KEY' not in os.environ; "
        "probe=Path('.rook-readiness'); probe.write_text('ok'); probe.unlink(); "
        "\ntry:\n socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n"
        "except OSError:\n pass\n"
        "else:\n raise SystemExit('container network unexpectedly available')\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
