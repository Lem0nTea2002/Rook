# Rook Complete Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the complete coding-agent project to Rook with no legacy command, package, environment, configuration, data-path, documentation, benchmark, or asset aliases.

**Architecture:** Treat branding as a repository-wide contract rather than a copy edit. Rename the Python namespace to `rook_agent`, expose the distribution as `rook-agent` and the command as `rook`, then make configuration, persistence, adapters, tests, documentation, and assets consume the same canonical identifiers.

**Tech Stack:** Python 3.11+, setuptools, pytest, Textual, PowerShell 7, Markdown, HTML/CSS/JavaScript

## Global Constraints

- Product brand is exactly `Rook`; the subtitle is `Local Coding Agent`.
- Distribution is exactly `rook-agent`; import namespace is exactly `rook_agent`; console command is exactly `rook`.
- Environment variables use only `ROOK_*`; project configuration is `rook.toml`; global configuration is `~/.config/rook/config.toml`; project data is `.rook`.
- This is a clean break: do not add compatibility aliases, fallbacks, or data migration.
- Run Windows commands with PowerShell 7.
- The project has no Git metadata, so commit steps are replaced by explicit verification checkpoints.

---

### Task 1: Add the repository branding contract

**Files:**
- Create: `tests/test_brand_contract.py`
- Inspect: `pyproject.toml`

**Interfaces:**
- Consumes: the canonical identifiers in `docs/superpowers/specs/2026-07-14-rook-rename-design.md`.
- Produces: pytest checks for metadata, package paths, filenames, and text residue.

- [ ] **Step 1: Write the failing contract test**

```python
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
LEGACY_LOWER = "first" + "coder"
LEGACY_TITLE = "First" + "Coder"
LEGACY_UPPER = "FIRST" + "CODER"
TEXT_SUFFIXES = {".py", ".toml", ".md", ".yml", ".yaml", ".json", ".html", ".css", ".js", ".j2", ".in", ".tcss", ".txt"}


def test_package_metadata_uses_rook_identifiers() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["name"] == "rook-agent"
    assert metadata["project"]["scripts"] == {"rook": "rook_agent.cli:main"}
    assert metadata["tool"]["setuptools"]["packages"]["find"]["include"] == ["rook_agent*"]


def test_only_rook_package_directory_exists() -> None:
    assert (ROOT / "rook_agent").is_dir()
    assert not (ROOT / LEGACY_LOWER).exists()


def test_filenames_and_text_have_no_legacy_identifier() -> None:
    forbidden = (LEGACY_LOWER, LEGACY_TITLE, LEGACY_UPPER)
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
    assert not sorted(set(violations)), "Legacy identifiers remain:\n" + "\n".join(sorted(set(violations)))
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m pytest tests/test_brand_contract.py -q
```

Expected: three failures because the current metadata, source directory, filenames, and text have not yet adopted the Rook contract.

### Task 2: Rename the Python package and code identifiers

**Files:**
- Rename: the current top-level Python package directory to `rook_agent/`
- Modify: `rook_agent/**/*.py`
- Modify: `tests/**/*.py`
- Modify: `benchmark/**/*.py`

**Interfaces:**
- Consumes: Python imports rooted at `rook_agent`.
- Produces: `rook_agent.cli:main`, `RookApp`, `RookTuiConfig`, `RookCodingAgentAdapter`, and `create_rook_app`.

- [ ] **Step 1: Rename the package directory**

```powershell
$legacy = 'first' + 'coder'
Move-Item -LiteralPath $legacy -Destination 'rook_agent'
```

- [ ] **Step 2: Rewrite Python namespace and symbols mechanically**

```powershell
$legacyLower = 'first' + 'coder'
$legacyTitle = 'First' + 'Coder'
$legacyUpper = 'FIRST' + 'CODER'
Get-ChildItem rook_agent,tests,benchmark -Recurse -File -Include *.py | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName)
    $text = $text.Replace($legacyUpper, 'ROOK').Replace($legacyTitle, 'Rook').Replace($legacyLower, 'rook_agent')
    [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
}
```

- [ ] **Step 3: Run import-focused tests**

Run:

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m pytest tests/test_brand_contract.py tests/test_config.py tests/test_cli.py -q
```

Expected: imports collect from `rook_agent`; remaining failures identify metadata or semantic path strings that Task 3 owns.

### Task 3: Rename packaging, configuration, persistence, and CLI semantics

**Files:**
- Modify: `pyproject.toml`
- Modify: `MANIFEST.in`
- Modify: `.gitignore`
- Modify: `rook_agent/config/settings.py`
- Modify: `rook_agent/cli.py`
- Modify: `rook_agent/session/**/*.py`
- Modify: `rook_agent/context/**/*.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli.py`
- Modify: session and context tests under `tests/`

**Interfaces:**
- Consumes: `rook_agent` package namespace.
- Produces: `rook-agent` distribution, `rook` command, `ROOK_*` environment contract, `rook.toml`, `.config/rook/config.toml`, and `.rook` data roots.

- [ ] **Step 1: Update project metadata**

```toml
[project]
name = "rook-agent"
description = "Rook is a local coding agent with a Textual TUI."

[project.scripts]
rook = "rook_agent.cli:main"

[tool.setuptools.packages.find]
include = ["rook_agent*"]

[tool.setuptools.package-data]
rook_agent = ["context/prompts/*.md", "app/*.tcss"]
```

- [ ] **Step 2: Replace semantic configuration and data identifiers**

Apply these exact mappings in source and tests:

```text
environment prefix -> ROOK_
project config      -> rook.toml
global config dir   -> rook
project data dir    -> .rook
benchmark data dir  -> .rook-<benchmark>
command             -> rook
module invocation   -> python -m rook_agent
```

- [ ] **Step 3: Verify configuration and CLI behavior**

Run:

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m pytest tests/test_brand_contract.py tests/test_config.py tests/test_cli.py tests/test_session_package.py tests/test_session_catalog.py -q
```

Expected: all selected tests pass.

### Task 4: Rename benchmark adapters and artifacts

**Files:**
- Rename: adapter filenames under `benchmark/` that carry the legacy brand
- Modify: `benchmark/**/*.py`
- Modify: `benchmark/**/*.md`
- Modify: `benchmark/**/*.j2`
- Modify: benchmark tests under `tests/`

**Interfaces:**
- Consumes: `rook_agent` APIs and `ROOK_*` configuration.
- Produces: Rook-named Harbor, terminal, harness, ChainSWE, AtCoder, EvalPlus, and local pytest integrations.

- [ ] **Step 1: Rename adapter files and their import targets**

Use `rook_agent.py`, `rook_cli.py`, and `rook-setup.sh.j2` as the branded adapter filenames where an adapter currently includes a product name.

- [ ] **Step 2: Normalize benchmark runtime identifiers**

Use `rook`, `rook-agent`, `.rook-<benchmark>`, `/opt/rook-agent`, `/opt/rook-src`, `/tmp/rook-<benchmark>-sessions`, and `ROOK_*` consistently in commands, paths, metadata, logs, fixture names, and help text.

- [ ] **Step 3: Run benchmark adapter tests**

Run:

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m pytest tests/test_harbor_adapter.py tests/test_terminal_bench_adapter.py tests/test_chainswe_runner.py tests/test_chainswe_docker_runner.py tests/test_atcoder_benchmark.py tests/test_evalplus_benchmark.py -q
```

Expected: all installed-dependency tests pass; unavailable optional suites are reported separately with their missing dependency.

### Task 5: Rename documentation, website copy, and media files

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/**/*.md`
- Modify: `website-demo/**/*.{html,css,js}`
- Rename: `assets/rook-logo.png`
- Rename: `docs/images/rook-ready.png`
- Rename: `docs/images/rook-demo.gif`
- Rename: `website-demo/assets/rook-logo.png`
- Rename: `website-demo/assets/rook-demo.gif`

**Interfaces:**
- Consumes: canonical CLI, packaging, configuration, and path identifiers.
- Produces: consistent English and Chinese Rook onboarding plus resolvable media links.

- [ ] **Step 1: Rewrite prose and command examples**

Use `Rook`, `rook-agent`, `rook`, `ROOK_*`, `rook.toml`, `.config/rook/config.toml`, `.rook`, and `rook_agent` according to the surface documented.

- [ ] **Step 2: Rename image files and update every reference**

```powershell
Move-Item assets/*-logo.png assets/rook-logo.png
Move-Item docs/images/*-ready.png docs/images/rook-ready.png
Move-Item docs/images/*-demo.gif docs/images/rook-demo.gif
Move-Item website-demo/assets/*-logo.png website-demo/assets/rook-logo.png
Move-Item website-demo/assets/*-demo.gif website-demo/assets/rook-demo.gif
```

- [ ] **Step 3: Verify documentation links and contract test**

Run:

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m pytest tests/test_brand_contract.py tests/test_readme_provider_docs.py -q
```

Expected: all tests pass and all referenced local media paths exist.

### Task 6: Install, test, package, and scan the finished repository

**Files:**
- Verify: all project files

**Interfaces:**
- Consumes: the completed Rook repository.
- Produces: fresh evidence for imports, tests, packaging, CLI help, and zero legacy residue.

- [ ] **Step 1: Create an isolated test environment and install the project**

```powershell
& 'D:\SofeWare\Anaconda3\python.exe' -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

- [ ] **Step 2: Run the complete test suite**

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
```

Expected: zero failures. If optional benchmark dependencies are absent, install the dependency declared by that benchmark or report the exact collection blocker.

- [ ] **Step 3: Build and inspect the distribution**

```powershell
& .\.venv\Scripts\python.exe -m pip install build
& .\.venv\Scripts\python.exe -m build
& .\.venv\Scripts\python.exe -c "from importlib.metadata import version; import rook_agent; print(version('rook-agent'))"
& .\.venv\Scripts\rook.exe --help
```

Expected: wheel and source archive names begin with `rook_agent-`; distribution lookup prints `0.1.2`; package import and CLI help exit successfully.

- [ ] **Step 4: Run the final residue and filename scan**

```powershell
$legacy = 'first' + 'coder'
$textHits = rg -n -i --hidden $legacy -g '!.venv/**' -g '!dist/**' -g '!*.pyc'
$nameHits = rg --files -g '!.venv/**' -g '!dist/**' | Where-Object { $_ -match $legacy }
if ($textHits -or $nameHits) { throw 'Legacy brand residue remains.' }
```

Expected: no output and exit success.

### Task 7: Rename the repository directory

**Files:**
- Rename: current repository directory to `<workspace>\Rook`

**Interfaces:**
- Consumes: a fully verified project with no open process holding the directory.
- Produces: the final Rook repository path.

- [ ] **Step 1: Rename from the parent directory**

```powershell
$sourceProject = Join-Path (Get-Location) ('MyCode' + 'Agent')
Rename-Item -LiteralPath $sourceProject -NewName 'Rook'
```

- [ ] **Step 2: Re-run the contract test from the final path**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest '.\tests\test_brand_contract.py' -q
```

Expected: all branding contract tests pass from the final directory.

## Self-review

- Spec coverage: tasks cover package, command, Python symbols, environment, configuration, persistence, benchmarks, tests, documentation, assets, packaging, and directory name.
- Placeholder scan: the plan contains no deferred implementation markers.
- Type consistency: target factory, TUI, adapter, package, and command identifiers use the same `Rook`/`rook_agent` mapping throughout.
- Repository constraint: no commit steps are present because the directory has no Git metadata.
