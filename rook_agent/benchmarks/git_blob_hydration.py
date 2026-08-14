"""用 GitHub 只读归档补齐 partial clone 缺失的 Git blob。"""

from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
import subprocess
import tarfile
from typing import Callable
from urllib.request import Request, urlopen
import zlib

from rook_agent.benchmarks._utils import write_json_exclusive


ArchiveReader = Callable[[str, str], bytes]


def hydrate_github_git_blobs(
    *,
    repository: str,
    git_root: str | Path,
    commits: tuple[str, ...],
    receipt_path: str | Path,
    read_archive: ArchiveReader | None = None,
) -> dict[str, object]:
    """补齐指定 commit 的 blob，不修改索引、HEAD 或工作树。"""

    root = Path(git_root).resolve()
    if not (root / ".git").is_dir():
        raise ValueError("Git blob 补齐只接受普通本地 Git 仓库")
    if len(set(commits)) != len(commits):
        raise ValueError("Git blob 补齐 commit 不能重复")
    for commit in commits:
        if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
            raise ValueError(f"Git commit 必须是完整小写 SHA-1：{commit}")
        _git(root, ("cat-file", "-e", f"{commit}^{{commit}}"))

    object_root = _object_root(root)
    reader = read_archive or _read_github_archive
    rows: list[dict[str, object]] = []
    for commit in commits:
        archive = reader(repository, commit)
        written = 0
        seen = 0
        with tarfile.open(fileobj=BytesIO(archive), mode="r:*") as bundle:
            for member in bundle:
                data = _member_bytes(bundle, member)
                if data is None:
                    continue
                seen += 1
                if _write_loose_blob(object_root, data):
                    written += 1
        missing = _missing_objects(root, commit)
        if missing:
            raise RuntimeError(
                f"GitHub 归档未补齐 commit {commit}：缺少 {len(missing)} 个对象"
            )
        rows.append(
            {
                "commit": commit,
                "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "archive_files": seen,
                "new_loose_blobs": written,
            }
        )
    receipt = {
        "schema_version": 1,
        "repository": repository,
        "git_root": "<LOCAL_REPOSITORY>",
        "commit_count": len(rows),
        "commits": rows,
    }
    write_json_exclusive(receipt_path, receipt)
    return receipt


def _member_bytes(
    bundle: tarfile.TarFile,
    member: tarfile.TarInfo,
) -> bytes | None:
    if member.isfile() or member.islnk():
        source = bundle.extractfile(member)
        if source is None:
            raise ValueError(f"GitHub 归档文件不可读：{member.name}")
        return source.read()
    if member.issym():
        return member.linkname.encode("utf-8")
    return None


def _write_loose_blob(object_root: Path, data: bytes) -> bool:
    payload = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    object_id = hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    destination = object_root / object_id[:2] / object_id[2:]
    if destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.rook-tmp")
    if temporary.exists():
        raise FileExistsError(f"Git 临时对象已存在：{temporary}")
    temporary.write_bytes(zlib.compress(payload))
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _object_root(root: Path) -> Path:
    raw = _git(root, ("rev-parse", "--git-path", "objects")).stdout.strip()
    path = Path(raw)
    return (path if path.is_absolute() else root / path).resolve()


def _missing_objects(root: Path, commit: str) -> tuple[str, ...]:
    tree = _git(
        root,
        (
            "ls-tree",
            "-r",
            "--full-tree",
            commit,
        ),
    )
    object_ids = tuple(
        line.split(maxsplit=3)[2]
        for line in tree.stdout.splitlines()
        if len(line.split(maxsplit=3)) >= 3
        and line.split(maxsplit=3)[1] == "blob"
    )
    check = subprocess.run(
        (
            "git",
            "-c",
            "remote.origin.promisor=false",
            "cat-file",
            "--batch-check=%(objectname) %(objecttype)",
        ),
        cwd=root,
        input="\n".join(object_ids) + "\n",
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return tuple(
        line.split(" ", 1)[0]
        for line in check.stdout.splitlines()
        if line.endswith(" missing")
    )


def _read_github_archive(repository: str, commit: str) -> bytes:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub 归档补齐需要 GITHUB_TOKEN")
    request = Request(
        f"https://api.github.com/repos/{repository}/tarball/{commit}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "rook-native-git-blob-hydrator/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=120) as response:
        return response.read()


def _git(
    root: Path,
    arguments: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


__all__ = ["hydrate_github_git_blobs"]
