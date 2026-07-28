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
    ".svg",
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
        if any(
            part in {".git", ".rook", ".venv", "__pycache__", ".pytest_cache"}
            for part in path.parts
        ):
            continue
        if any(token in path.name for token in forbidden):
            violations.append(str(path.relative_to(ROOT)))
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore"):
            text = path.read_text(encoding="utf-8", errors="strict")
            if any(token in text for token in forbidden):
                violations.append(str(path.relative_to(ROOT)))

    unique_violations = sorted(set(violations))
    assert not unique_violations, "Legacy identifiers remain:\n" + "\n".join(unique_violations)


def test_readmes_use_current_rook_tui_assets() -> None:
    legacy_tui_assets = {
        "docs/images/rook-ready.png",
        "docs/images/tui-chat.png",
        "docs/images/tui-empty.png",
    }
    for readme_name in ("README.md", "README.zh-CN.md"):
        text = (ROOT / readme_name).read_text(encoding="utf-8")
        assert "docs/images/rook-demo.gif" in text
        assert "docs/images/rook-tui-welcome.png" in text
        assert "docs/images/rook-tui-welcome.svg" not in text
        assert "docs/video/rook-forge-demo.mp4" not in text
        assert not any(asset in text for asset in legacy_tui_assets)
    assert (ROOT / "docs" / "images" / "rook-demo.gif").is_file()
    assert (ROOT / "docs" / "images" / "rook-tui-welcome.png").is_file()


def test_demo_site_uses_current_rook_tui_assets() -> None:
    text = (ROOT / "website-demo" / "index.html").read_text(encoding="utf-8")
    assert "github.com/Lem0nTea2002/Rook" in text
    assert "github.com/ZHUMUJUN/Rook" not in text
    for filename in (
        "rook-demo.gif",
        "rook-tui-conversation.png",
        "rook-tui-permission.png",
        "rook-tui-resume.png",
    ):
        assert f"assets/{filename}" in text
        assert (ROOT / "website-demo" / "assets" / filename).is_file()
