from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
LEGACY_LOWER = "first" + "coder"
LEGACY_TITLE = "First" + "Coder"
LEGACY_UPPER = "FIRST" + "CODER"
LEGACY_PROJECT_LOWER = "mycode" + "agent"
LEGACY_PROJECT_TITLE = "MyCode" + "Agent"
LEGACY_PROJECT_UPPER = "MYCODE" + "AGENT"
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".in",
    ".j2",
    ".js",
    ".json",
    ".md",
    ".py",
    ".tcss",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def test_package_metadata_uses_rook_identifiers() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["name"] == "rook-agent"
    assert metadata["project"]["scripts"] == {"rook": "rook_agent.cli:main"}
    assert metadata["tool"]["setuptools"]["packages"]["find"]["include"] == ["rook_agent*"]


def test_only_rook_package_directory_exists() -> None:
    assert (ROOT / "rook_agent").is_dir()
    assert not (ROOT / LEGACY_LOWER).exists()


def test_filenames_and_text_have_no_legacy_identifier() -> None:
    forbidden = (
        LEGACY_LOWER,
        LEGACY_TITLE,
        LEGACY_UPPER,
        LEGACY_PROJECT_LOWER,
        LEGACY_PROJECT_TITLE,
        LEGACY_PROJECT_UPPER,
    )
    violations: list[str] = []

    for path in ROOT.rglob("*"):
        if any(part in {".venv", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if any(token in path.name for token in forbidden):
            violations.append(str(path.relative_to(ROOT)))
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore"):
            text = path.read_text(encoding="utf-8", errors="strict")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(ROOT)))

    unique_violations = sorted(set(violations))
    assert not unique_violations, "Legacy identifiers remain:\n" + "\n".join(unique_violations)
