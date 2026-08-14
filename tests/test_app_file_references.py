from hashlib import sha256
from pathlib import Path

import pytest

from rook_agent.app.file_references import ProjectFileReferenceService


def test_file_reference_suggestions_include_files_and_parent_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo", encoding="utf-8")
    service = ProjectFileReferenceService(
        tmp_path,
        file_lister=lambda root: ("src/app.py", "README.md"),
    )

    assert [item.path for item in service.suggest("app")] == ["src/app.py"]
    assert [item.path for item in service.suggest("src")] == ["src", "src/app.py"]


def test_rookignore_removes_matching_reference_candidates(tmp_path: Path) -> None:
    (tmp_path / ".rookignore").write_text("secret/*\n*.key\n", encoding="utf-8")
    service = ProjectFileReferenceService(
        tmp_path,
        file_lister=lambda root: ("src/app.py", "secret/token.txt", "private.key"),
    )

    assert [item.path for item in service.suggest("")] == ["src", "src/app.py"]


def test_resolve_prompt_attaches_bounded_text_with_path_and_hash(tmp_path: Path) -> None:
    content = "def hello():\n    return 'world'\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(content, encoding="utf-8")
    service = ProjectFileReferenceService(
        tmp_path,
        file_lister=lambda root: ("src/app.py",),
    )

    result = service.resolve_prompt("解释 @src/app.py")
    actual = (tmp_path / "src" / "app.py").read_bytes()

    assert result.original_prompt == "解释 @src/app.py"
    assert result.references[0].path == "src/app.py"
    assert result.references[0].sha256 == sha256(actual).hexdigest()
    assert result.references[0].content == actual.decode("utf-8")
    assert '<rook-file path="src/app.py"' in result.enriched_prompt
    assert "def hello()" in result.enriched_prompt
    assert result.warnings == ()


def test_large_and_binary_files_are_path_only_with_truthful_warning(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * 20, encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"a\x00b")
    service = ProjectFileReferenceService(
        tmp_path,
        max_file_bytes=10,
        file_lister=lambda root: ("large.txt", "binary.bin"),
    )

    result = service.resolve_prompt("检查 @large.txt 和 @binary.bin")

    assert [item.content for item in result.references] == [None, None]
    assert any("超过 10 bytes" in warning for warning in result.warnings)
    assert any("二进制" in warning for warning in result.warnings)


@pytest.mark.parametrize("reference", ["../outside.txt", "/absolute.txt", "C:/absolute.txt"])
def test_reference_rejects_absolute_and_traversal_paths(tmp_path: Path, reference: str) -> None:
    service = ProjectFileReferenceService(tmp_path, file_lister=lambda root: ())

    result = service.resolve_prompt(f"读取 @{reference}")

    assert result.references == ()
    assert any("拒绝" in warning for warning in result.warnings)


def test_reference_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")
    service = ProjectFileReferenceService(
        tmp_path,
        file_lister=lambda root: ("link.txt",),
    )

    result = service.resolve_prompt("读取 @link.txt")

    assert result.references == ()
    assert any("符号链接逃逸" in warning for warning in result.warnings)


def test_total_reference_budget_keeps_later_files_path_only(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a" * 8, encoding="utf-8")
    (tmp_path / "b.txt").write_text("b" * 8, encoding="utf-8")
    service = ProjectFileReferenceService(
        tmp_path,
        max_file_bytes=10,
        max_total_bytes=10,
        file_lister=lambda root: ("a.txt", "b.txt"),
    )

    result = service.resolve_prompt("检查 @a.txt @b.txt")

    assert result.references[0].content == "a" * 8
    assert result.references[1].content is None
    assert any("总预算" in warning for warning in result.warnings)
