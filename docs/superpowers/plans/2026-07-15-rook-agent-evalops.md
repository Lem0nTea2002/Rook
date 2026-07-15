# Rook Agent EvalOps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an evaluation-gated Skill lifecycle that runs isolated Baseline/Skill experiments against Rook, Codex CLI, and Claude Code CLI, then produces auditable per-Agent promotion or rollback decisions.

**Architecture:** Keep `rook_agent.evolution` responsible for execution-grounded candidate generation and pre-evaluation safety gates. Add a standalone `rook_agent.evalops` package for versioned suites, isolated workspaces, black-box CLI adapters, normalized traces, deterministic evaluators, paired scoring, target-specific promotion, reports, and explicit export. The existing benchmark adapter remains intact and is wrapped by `RookEvalAdapter`; external Agent calls are optional and never run in the default test suite.

**Tech Stack:** Python 3.11+, stdlib `dataclasses`/`enum`/`tomllib`/`subprocess`/`statistics`/`hashlib`/`json`, existing Rook provider and session abstractions, JSON/JSONL, TOML, Markdown Agent Skills, pytest, PowerShell 7 on Windows.

## Global Constraints

- Treat [the approved Agent EvalOps design](../specs/2026-07-15-rook-agent-evalops-design.md) as the source of truth.
- Work only in the existing `feature/rook-forge` worktree; do not implement on `main`.
- Before Task 1, preserve the current unstaged `tests/test_evolution_gate.py` security regression tests. They belong to the interrupted Task 3 repair and must not be discarded.
- Use `& '.\.venv\Scripts\python.exe'` for every Python and pytest command. Bare `python` may resolve to the Windows Store alias.
- Use TDD for every behavior change: run the focused test and observe RED before implementation, then run the same test and observe GREEN.
- Use `apply_patch` for source and documentation edits.
- Use only Python standard-library additions in the MVP. Do not add a database, web framework, YAML parser, process library, or statistics dependency.
- `.rook/` remains Git ignored. Version-controlled suite definitions live under `evals/`; runtime events, workspaces, candidates, scorecards, and reports live under `.rook/`.
- Never silently write `~/.codex`, `~/.claude`, or another external Agent's real configuration. External installation is an explicit export operation.
- Redact secrets before raw CLI events reach disk. Never log the matched secret text.
- Hidden evaluators and expected answers must remain outside the Agent-readable workspace.
- Unknown external CLI events may be retained raw, but missing critical tool/result/termination semantics sets `trace_complete=False` and blocks promotion.
- Missing Token, cost, latency, or usage values remain `None`; never coerce unavailable telemetry to zero.
- Compare each Agent against its own baseline. Cross-Agent absolute comparisons are allowed only for metrics with the same observed unit and scope.
- `ADAPTER_UNAVAILABLE`, `AUTH_FAILED`, `VERSION_UNSUPPORTED`, `INFRA_ERROR`, `ADAPTER_ERROR`, and `USER_CANCELLED` do not enter Skill success-rate denominators.
- `WRONG_RESULT`, `VERIFICATION_FAILED`, `TIMEOUT`, `TURN_LIMIT`, `BUDGET_EXHAUSTED`, and `UNSAFE_ACTION` are valid constrained Agent outcomes.
- Default tests use fake processes/providers and create no paid Codex or Claude calls. Real smoke tests require `ROOK_RUN_EXTERNAL_EVALS=1`.
- The recorded pre-EvalOps core baseline is `27 failed, 956 passed, 3 skipped` with `tests/test_evalplus_benchmark.py` excluded. All new and directly touched tests must pass, and the final suite must introduce no new failing test names beyond that baseline.
- `tests/test_evalplus_benchmark.py` remains a separate optional gate because `evalplus` is not installed.
- End every task with `git diff --check`, focused verification, a scoped commit, and a fresh task review before starting the next task.

---

### Task 1: Close the Existing Evolution Gate Security Review

**Files:**
- Modify: `rook_agent/evolution/gate.py`
- Modify: `tests/test_evolution_gate.py`
- Test: `tests/test_session_redaction.py`
- Test: `tests/test_permissions_policy.py`

**Interfaces:**
- Consumes: existing `SkillDelta`, `TaskTrace`, `EvidenceItem`, `evaluate_skill_delta()`, and `redact_sensitive_text()`.
- Produces: a reviewed pre-evaluation Gate where every executable clause is grounded, evidence targets cannot be laundered through arbitrary text, project scope is containment-safe, and Bearer detection preserves ordinary prose.

- [ ] **Step 1: Audit and preserve the interrupted RED tests**

Run:

```powershell
git diff -- tests/test_evolution_gate.py
```

Expected: the unstaged diff contains tests for all-command grounding, mutation-command grounding, structured target correlation, Git diff machine headers, Bearer prose, outside-project scope, traversal, correct-case paths, and injection reuse. Do not stage or edit away any of those cases before running them.

- [ ] **Step 2: Run the security regression tests and record RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_gate.py tests/test_session_redaction.py tests/test_permissions_policy.py
```

Expected: one or more of the new Task 3 security tests fail against commit `21c64a8`; record the exact failing names in `.superpowers/sdd/task-evalops-1-report.md` if subagent-driven execution is used.

- [ ] **Step 3: Require every executable clause in each entry**

Change the grounding core so it checks all extracted candidates rather than one matching candidate:

```python
def _commands_are_grounded(candidates: tuple[str, ...], executed: frozenset[str]) -> bool:
    return all(_normalize_command(candidate) in executed for candidate in candidates)
```

Mutation entries must evaluate both the target mutation/proof and every inline command; an edit step cannot hide an unexecuted validation command.

- [ ] **Step 4: Restrict evidence targets to structured sources**

Replace arbitrary path extraction from evidence body text with this boundary:

```python
def _evidence_targets(item: EvidenceItem) -> frozenset[str]:
    structured = _structured_tool_targets(item.tool_name, item.data)
    if item.tool_name in {"git_diff", "git_status"}:
        return structured | _git_machine_record_targets(item.content)
    return structured
```

Only explicit tool data fields and strict Git diff/status machine records may establish a mutation or proof target. A `view`, `edit`, `grep`, or `read_multi` body mentioning another path must not establish that path.

- [ ] **Step 5: Resolve project containment without case-folding filesystem paths**

Use resolved paths for ownership and normalized strings only for semantic comparison:

```python
def _is_project_owned(target: str, project_root: Path) -> bool:
    root = project_root.resolve()
    candidate = Path(target)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    return resolved == root or root in resolved.parents
```

Existence checks must use the correctly cased resolved path. Existing absolute paths outside the project and relative traversal outside the project remain global-scope blockers, not project-owned paths.

- [ ] **Step 6: Split explicit Authorization headers from general Bearer prose**

Implement two separate rules:

```python
_AUTH_BEARER_RE = re.compile(r"(?im)^\s*authorization\s*:\s*bearer\s+\S+")

def _looks_like_general_bearer_credential(value: str) -> bool:
    return _has_mixed_credential_shape(value) and _estimated_entropy(value) >= 3.5
```

Explicit Authorization headers are always redacted. Outside headers, long ordinary alphabetic or hyphenated prose must remain unchanged unless it has deterministic credential shape and entropy.

- [ ] **Step 7: Run focused security verification**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_gate.py tests/test_session_redaction.py tests/test_permissions_policy.py
git diff --check
```

Expected: all focused tests pass and `git diff --check` emits no errors.

- [ ] **Step 8: Commit the completed Gate repair**

```powershell
git add rook_agent/evolution/gate.py tests/test_evolution_gate.py
git commit -m "fix: complete Forge evidence governance"
```

Expected: the pre-existing unstaged tests and the minimal implementation land together in one reviewed commit.

---

### Task 2: Define EvalOps Domain Models and Strict Suite Loading

**Files:**
- Create: `rook_agent/evalops/__init__.py`
- Create: `rook_agent/evalops/models.py`
- Create: `rook_agent/evalops/suites.py`
- Test: `tests/test_evalops_models.py`
- Test: `tests/test_evalops_suites.py`

**Interfaces:**
- Consumes: `rook_agent.context.identity.stable_json_hash` and `rook_agent.evolution.models.EvidenceRef`.
- Produces: `AgentTarget`, `SkillBundle`, `SkillCandidate`, `EvalSuite`, `EvalCase`, `RunSpec`, `AgentRun`, `NormalizedTrace`, `ScoreCard`, `PromotionDecision`, and `load_eval_suite(path)`.

- [ ] **Step 1: Write failing enum and dataclass contract tests**

Create tests that import the exact public types and assert stable values:

```python
def test_evalops_protocol_has_stable_status_values() -> None:
    assert Treatment.BASELINE.value == "baseline"
    assert Treatment.FORCED_SKILL.value == "forced_skill"
    assert Treatment.ROUTED_SKILL.value == "routed_skill"
    assert PromotionStatus.PROMOTED.value == "promoted"
    assert PromotionStatus.ROLLED_BACK.value == "rolled_back"
```

Also assert that `AgentRun` preserves `input_tokens=None`, `cost_usd=None`, and `trace_complete=False` rather than inventing zeros.

- [ ] **Step 2: Run the model tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_models.py
```

Expected: collection fails because `rook_agent.evalops` does not exist.

- [ ] **Step 3: Implement the stable protocol**

Define these enums in `models.py`:

```python
class AgentType(StrEnum):
    ROOK = "rook"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"

class Treatment(StrEnum):
    BASELINE = "baseline"
    FORCED_SKILL = "forced_skill"
    ROUTED_SKILL = "routed_skill"

class CaseCategory(StrEnum):
    DIRECT = "direct"
    TRANSFER = "transfer"
    REGRESSION = "regression"
    ADVERSARIAL = "adversarial"

class RunStatus(StrEnum):
    PASSED = "passed"
    WRONG_RESULT = "wrong_result"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    TURN_LIMIT = "turn_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNSAFE_ACTION = "unsafe_action"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    AUTH_FAILED = "auth_failed"
    VERSION_UNSUPPORTED = "version_unsupported"
    INFRA_ERROR = "infra_error"
    ADAPTER_ERROR = "adapter_error"
    USER_CANCELLED = "user_cancelled"

class PromotionStatus(StrEnum):
    PROMOTED = "promoted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    STALE = "stale"
    ROLLED_BACK = "rolled_back"
```

Implement frozen, slotted dataclasses matching section 7 of the design. `AgentTarget.fingerprint`, `SkillCandidate.fingerprint`, and `EvalSuite.fingerprint` use `stable_json_hash(..., length=32)`.

- [ ] **Step 4: Write failing strict suite-loader tests**

Use a real temporary suite:

```toml
id = "windows-shell"
version = "1"
policy = "../../policies/default.toml"

[[cases]]
id = "direct-01"
category = "direct"
task = "task.md"
fixture = "fixture"
timeout_seconds = 180
network = "disabled"

[cases.evaluator]
kind = "command"
command = ["python", "hidden_check.py"]
```

Tests must cover unknown top-level fields, duplicate case ids, unsupported category/network values, missing task/fixture, `../` escape, absolute escape, and a fingerprint change when task or fixture content changes.

- [ ] **Step 5: Run the suite tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_suites.py
```

Expected: imports or `load_eval_suite()` fail because the loader is absent.

- [ ] **Step 6: Implement `load_eval_suite()` with containment checks**

Use stdlib `tomllib`, reject unknown keys, resolve every referenced path under the suite root or declared `evals/policies` root, and compute the fingerprint from normalized manifest data plus hashes of task, fixture tree, evaluator config, and policy content:

```python
def load_eval_suite(path: str | Path) -> EvalSuite:
    manifest = Path(path).resolve()
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    _reject_unknown(raw, allowed={"id", "version", "policy", "cases"})
    cases = tuple(_load_case(manifest.parent, value) for value in _require_list(raw, "cases"))
    _require_unique(case.id for case in cases)
    return _build_suite(manifest, raw, cases)
```

- [ ] **Step 7: Export the protocol and run focused tests**

`rook_agent/evalops/__init__.py` exports only the stable public types and `load_eval_suite`.

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_models.py tests/test_evalops_suites.py
git diff --check
```

Expected: both test files pass.

- [ ] **Step 8: Commit the protocol**

```powershell
git add rook_agent/evalops/__init__.py rook_agent/evalops/models.py rook_agent/evalops/suites.py tests/test_evalops_models.py tests/test_evalops_suites.py
git commit -m "feat: define Agent EvalOps protocol"
```

---

### Task 3: Build Hermetic Workspace Pairs and Redacted Artifact Storage

**Files:**
- Create: `rook_agent/evalops/workspace.py`
- Create: `rook_agent/evalops/artifacts.py`
- Test: `tests/test_evalops_workspace.py`
- Test: `tests/test_evalops_artifacts.py`

**Interfaces:**
- Consumes: `EvalCase`, `RunSpec`, `stable_json_hash`, and `rook_agent.evolution.gate.redact_sensitive_text`.
- Produces: `WorkspaceManager.create_pair(case, pair_id) -> WorkspacePair`, `WorkspaceManager.cleanup(pair)`, and `ArtifactStore` atomic JSON/JSONL/text writes.

- [ ] **Step 1: Write failing workspace isolation tests**

Cover identical initial hashes, an immutable evaluator snapshot, independent writes, unchanged fixture source, rejected escaping symlinks, rejected special files, stable tree hashing, evaluator path outside all Agent-readable workspaces, and cleanup status:

```python
def test_workspace_pair_starts_identical_and_does_not_share_writes(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "value.txt").write_text("base\n", encoding="utf-8")
    pair = WorkspaceManager(tmp_path / "runs").create_pair(fixture, pair_id="pair-1")
    assert pair.snapshot_hash == pair.baseline_hash
    assert pair.baseline_hash == pair.candidate_hash
    (pair.candidate / "value.txt").write_text("changed\n", encoding="utf-8")
    assert (pair.snapshot / "value.txt").read_text(encoding="utf-8") == "base\n"
    assert (pair.baseline / "value.txt").read_text(encoding="utf-8") == "base\n"
```

- [ ] **Step 2: Run workspace tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_workspace.py
```

Expected: import fails because `WorkspaceManager` is absent.

- [ ] **Step 3: Implement safe snapshot copying and hashing**

`WorkspaceManager` uses `shutil.copytree`, rejects symlinks and non-regular files before copying, excludes `.rook`, `__pycache__`, and `.pytest_cache`, and hashes relative path plus file bytes in sorted order:

```python
def hash_workspace(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _iter_regular_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
```

Create `snapshot`, `baseline`, and `candidate` beneath `.rook/eval-runs/<experiment>/workspaces/<pair-id>/`; never create them inside the source fixture. The Agent receives only its baseline or candidate path. The hidden evaluator receives the untouched `snapshot` and final Agent workspace after the run.

- [ ] **Step 4: Write failing artifact redaction and atomicity tests**

Tests must prove that recursive strings are redacted before disk, a failed `os.replace` keeps the previous file, JSON uses sorted keys, JSONL preserves order, and raw event references contain content hashes:

```python
def test_artifact_store_redacts_before_persisting(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.write_json("raw/event.json", {"Authorization": "Bearer example-secret-value"})
    persisted = (tmp_path / "raw/event.json").read_text(encoding="utf-8")
    assert "example-secret-value" not in persisted
    assert "[REDACTED]" in persisted
```

- [ ] **Step 5: Run artifact tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_artifacts.py
```

Expected: `ArtifactStore` is missing.

- [ ] **Step 6: Implement recursive redaction and atomic writes**

Use a recursive JSON-safe transformer:

```python
def redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    return value
```

Write to a sibling temporary file, flush and `os.fsync`, then `os.replace`. Return an `ArtifactRef(relative_path, sha256, size_bytes)`.

- [ ] **Step 7: Run isolation and storage verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_workspace.py tests/test_evalops_artifacts.py tests/test_evolution_gate.py
git diff --check
```

Expected: all listed tests pass.

- [ ] **Step 8: Commit workspace and artifact boundaries**

```powershell
git add rook_agent/evalops/workspace.py rook_agent/evalops/artifacts.py tests/test_evalops_workspace.py tests/test_evalops_artifacts.py
git commit -m "feat: isolate EvalOps run artifacts"
```

---

### Task 4: Add Candidate Storage and Agent-Specific Skill Materialization

**Files:**
- Create: `rook_agent/evalops/skills.py`
- Create: `rook_agent/evalops/candidates.py`
- Test: `tests/test_evalops_skills.py`
- Test: `tests/test_evalops_candidates.py`

**Interfaces:**
- Consumes: `SkillBundle`, `SkillCandidate`, `AgentType`, and `ArtifactStore`.
- Produces: `render_skill(bundle)`, `SkillMaterializer.materialize(candidate, target, workspace)`, and `CandidateStore.create/get/list_versions`.

- [ ] **Step 1: Write failing canonical rendering tests**

Assert stable frontmatter, deterministic sections, slug validation, newline termination, and no free-form destination path:

```python
def test_render_skill_is_deterministic() -> None:
    content = render_skill(sample_bundle())
    assert content.startswith("---\nname: windows-cmd-switching\n")
    assert "## Procedure\n1. Detect cmd.exe." in content
    assert content.endswith("\n")
```

- [ ] **Step 2: Run rendering tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_skills.py
```

Expected: renderer and materializer imports fail.

- [ ] **Step 3: Implement canonical rendering and exact target locations**

Use these destinations:

```python
_TARGET_SKILL_DIRS = {
    AgentType.ROOK: Path(".agents/skills"),
    AgentType.CODEX: Path(".agents/skills"),
    AgentType.CLAUDE_CODE: Path(".claude/skills"),
}
```

Materialize only `<root>/<slug>/SKILL.md`, reject an existing non-identical destination, and verify the resolved destination remains beneath the workspace. Rook and Codex share the open Agent Skills layout; Claude uses its project skill layout.

- [ ] **Step 4: Write failing candidate-store tests**

Cover monotonically increasing versions, immutable existing versions, content hashes, `candidate` initial status, corrupt metadata fail-closed behavior, and path containment:

```python
def test_candidate_store_versions_immutable_content(tmp_path: Path) -> None:
    store = CandidateStore(tmp_path / ".rook/skill-registry")
    first = store.create(sample_bundle())
    second = store.create(replace(sample_bundle(), description="Updated"))
    assert (first.version, second.version) == (1, 2)
    assert store.get(first.bundle.name, 1).content_hash == first.content_hash
```

- [ ] **Step 5: Run candidate tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_candidates.py
```

Expected: `CandidateStore` is absent.

- [ ] **Step 6: Implement immutable candidate versions**

Store each version as:

```text
.rook/skill-registry/<slug>/candidates/<version>/
  skill.json
  SKILL.md
  meta.json
```

`meta.json` includes `version`, `content_hash`, `origin`, `status`, and evidence-ref hashes. Creation writes a temporary version directory and atomically renames it; existing version directories are never overwritten.

- [ ] **Step 7: Run Skill storage verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_skills.py tests/test_evalops_candidates.py tests/test_skill_discovery.py tests/test_skill_loader.py
git diff --check
```

Expected: candidate tests and existing handwritten Skill behavior pass.

- [ ] **Step 8: Commit candidate storage**

```powershell
git add rook_agent/evalops/skills.py rook_agent/evalops/candidates.py tests/test_evalops_skills.py tests/test_evalops_candidates.py
git commit -m "feat: store and materialize Skill candidates"
```

---

### Task 5: Create the Process Boundary, Adapter Contract, and Fake Agent

**Files:**
- Create: `rook_agent/evalops/process.py`
- Create: `rook_agent/evalops/adapters/__init__.py`
- Create: `rook_agent/evalops/adapters/base.py`
- Create: `rook_agent/evalops/adapters/fake.py`
- Create: `rook_agent/evalops/normalizers/__init__.py`
- Create: `rook_agent/evalops/normalizers/base.py`
- Test: `tests/test_evalops_process.py`
- Test: `tests/test_evalops_adapter_contract.py`

**Interfaces:**
- Consumes: `RunSpec`, `AgentRun`, `NormalizedTrace`, `ArtifactStore`, and `CancellationToken`.
- Produces: `ProcessRunner.run(ProcessRequest) -> ProcessResult`, `AgentAdapter`, `TraceNormalizer`, and `FakeAgentAdapter`.

- [ ] **Step 1: Write failing process timeout and cancellation tests**

Test normal exit, stdout/stderr capture, timeout, cancellation, UTF-8 replacement, explicit environment, and full process-tree termination. On Windows, spawn a parent that launches a long-lived child and assert both exit after cancellation.

```python
def test_process_runner_reports_timeout(tmp_path: Path) -> None:
    result = ProcessRunner().run(
        ProcessRequest(command=(sys.executable, "-c", "import time; time.sleep(30)"), cwd=tmp_path, timeout_seconds=1)
    )
    assert result.status is ProcessStatus.TIMEOUT
```

- [ ] **Step 2: Run process tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_process.py
```

Expected: process types are missing.

- [ ] **Step 3: Implement a no-shell process runner with tree termination**

Use `subprocess.Popen` with `shell=False`. On Windows set `CREATE_NEW_PROCESS_GROUP` and terminate with `taskkill /PID <pid> /T /F`; on POSIX set `start_new_session=True` and terminate with `os.killpg`. Capture stdout and stderr in reader threads so cancellation cannot deadlock on full pipes.

```python
@dataclass(frozen=True, slots=True)
class ProcessRequest:
    command: tuple[str, ...]
    cwd: Path
    stdin_text: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300
```

- [ ] **Step 4: Write failing adapter contract tests**

Define a reusable contract asserting that every adapter:

- reports capabilities through `probe()`;
- receives a `RunSpec` and isolated workspace;
- returns a terminal `RunStatus`;
- preserves raw event refs;
- returns `trace_complete=False` on malformed critical events;
- never writes outside its workspace and artifact root.

```python
def assert_adapter_contract(adapter: AgentAdapter, spec: RunSpec, workspace: Path) -> None:
    prepared = adapter.prepare(spec, workspace)
    run = adapter.run(prepared)
    assert run.pair_id == spec.pair_id
    assert run.status in RunStatus
```

- [ ] **Step 5: Run adapter contract tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_adapter_contract.py
```

Expected: adapter protocols and fake adapter are missing.

- [ ] **Step 6: Implement protocols and deterministic `FakeAgentAdapter`**

Define:

```python
class AgentAdapter(Protocol):
    def probe(self) -> AgentCapabilities: ...
    def prepare(self, spec: RunSpec, workspace: Path, *, staged_skill: Path | None = None) -> PreparedRun: ...
    def run(self, prepared: PreparedRun) -> AgentRun: ...
    def cancel(self, run_id: str) -> None: ...

class TraceNormalizer(Protocol):
    def normalize(self, raw_events: tuple[dict[str, object], ...], *, target: AgentTarget) -> NormalizedTrace: ...
```

`FakeAgentAdapter` consumes a declared per-case script, performs deterministic file writes/tool events, and supports success, failure, timeout, malformed event, and infrastructure-error fixtures.

- [ ] **Step 7: Run process and contract verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_process.py tests/test_evalops_adapter_contract.py tests/test_utils_subprocess.py
git diff --check
```

Expected: all tests pass on Windows without orphaned test processes.

- [ ] **Step 8: Commit the adapter foundation**

```powershell
git add rook_agent/evalops/process.py rook_agent/evalops/adapters rook_agent/evalops/normalizers tests/test_evalops_process.py tests/test_evalops_adapter_contract.py
git commit -m "feat: define EvalOps agent adapters"
```

---

### Task 6: Wrap Rook as a First-Class Eval Target

**Files:**
- Create: `rook_agent/evalops/adapters/rook.py`
- Create: `rook_agent/evalops/normalizers/rook.py`
- Modify: `rook_agent/eval/adapter.py`
- Modify: `rook_agent/eval/tasks.py`
- Test: `tests/test_evalops_rook_adapter.py`
- Test: `tests/test_eval_adapter.py`

**Interfaces:**
- Consumes: `RookCodingAgentAdapter`, `CodingTask`, `JsonlSessionStore`, `RunSpec`, and `SkillMaterializer`.
- Produces: `RookEvalAdapter` and `RookTraceNormalizer` conforming to Task 5 protocols.

- [ ] **Step 1: Write failing Rook normalization tests**

Build a real Rook Session JSONL containing user, assistant tool call, tool result, and final assistant events. Assert exact normalized order, tool name, `ok`, exit code, final answer, loaded Skill hashes, and `trace_complete=True`.

```python
def test_rook_normalizer_maps_tool_result_metadata() -> None:
    trace = RookTraceNormalizer().normalize(sample_rook_events(), target=rook_target())
    completed = [event for event in trace.events if event.type == "tool_completed"]
    assert completed[0].tool_name == "shell"
    assert completed[0].ok is True
```

- [ ] **Step 2: Run Rook adapter tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_rook_adapter.py
```

Expected: Rook EvalOps adapter modules are missing.

- [ ] **Step 3: Add a narrow result hook to the existing benchmark adapter**

Extend `CodingTaskResult` only if required to expose the actual session id and terminal response metadata; preserve existing fields and `to_prediction_dict()`:

```python
@dataclass(frozen=True, slots=True)
class CodingTaskResult:
    # existing fields remain unchanged
    session_id: str | None = None
    finish_reason: str | None = None
```

Existing benchmark callers must continue to pass without changes.

- [ ] **Step 4: Implement `RookTraceNormalizer`**

Map Rook event types to the common protocol. Tool calls come from assistant `tool_call` parts; results come from `tool_result` part metadata. Missing matching tool calls, invalid payload shape, or absent terminal assistant result sets `trace_complete=False` and adds a stable diagnostic code.

- [ ] **Step 5: Implement `RookEvalAdapter`**

Prepare a `CodingTask` from `RunSpec`. For `FORCED_SKILL`, prepend one explicit instruction containing the staged project-relative `SKILL.md` path. For `ROUTED_SKILL`, do not name the Skill. Use an injected `RookCodingAgentAdapter` factory for tests, then load and normalize its transcript.

```python
def _task_prompt(spec: RunSpec, skill_path: Path | None, workspace: Path) -> str:
    if spec.treatment is Treatment.FORCED_SKILL and skill_path is not None:
        relative_skill = skill_path.relative_to(workspace).as_posix()
        return f"Read and follow `{relative_skill}` for this task.\n\n{spec.case.task}"
    return spec.case.task
```

- [ ] **Step 6: Prove the three treatments are isolated**

Tests assert baseline has no candidate path, forced prompt names exactly one staged Skill, routed prompt does not name it, and both Skill treatments have the candidate file in their own workspace.

- [ ] **Step 7: Run Rook and benchmark regressions**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_rook_adapter.py tests/test_eval_adapter.py tests/test_agent_skill_flow.py tests/test_skill_discovery.py
git diff --check
```

Expected: all tests pass.

- [ ] **Step 8: Commit the Rook target**

```powershell
git add rook_agent/evalops/adapters/rook.py rook_agent/evalops/normalizers/rook.py rook_agent/eval/adapter.py rook_agent/eval/tasks.py tests/test_evalops_rook_adapter.py tests/test_eval_adapter.py
git commit -m "feat: evaluate Rook through EvalOps"
```

---

### Task 7: Add the Codex CLI Adapter and JSONL Normalizer

**Files:**
- Create: `rook_agent/evalops/adapters/codex_cli.py`
- Create: `rook_agent/evalops/normalizers/codex.py`
- Create: `tests/fixtures/evalops/codex/success.jsonl`
- Create: `tests/fixtures/evalops/codex/failure.jsonl`
- Create: `tests/fixtures/evalops/codex/unknown-event.jsonl`
- Test: `tests/test_evalops_codex_adapter.py`
- Test: `tests/test_evalops_codex_normalizer.py`

**Interfaces:**
- Consumes: `ProcessRunner`, `AgentAdapter`, `RunSpec`, `ArtifactStore`, and staged `.agents/skills/<slug>/SKILL.md`.
- Produces: `CodexCliAdapter` and `CodexTraceNormalizer`.

- [ ] **Step 1: Write failing Codex normalizer fixture tests**

Fixtures cover `thread.started`, `turn.started`, `item.started`, `item.completed`, `turn.completed`, `turn.failed`, and `error`. Item fixtures cover `agent_message`, `command_execution`, `file_change`, and an unknown item.

```python
def test_codex_normalizer_maps_command_execution() -> None:
    trace = normalize_fixture("success.jsonl")
    command = next(event for event in trace.events if event.type == "tool_completed")
    assert command.tool_name == "shell"
    assert command.ok is True
    assert command.data["exit_code"] == 0
```

Malformed JSON or a missing turn terminal event must set `trace_complete=False`; an unknown non-critical item remains in raw events and does not alone make the trace incomplete.

- [ ] **Step 2: Run Codex normalizer tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_codex_normalizer.py
```

Expected: Codex normalizer is missing.

- [ ] **Step 3: Implement versioned Codex event parsing**

Implement an explicit dispatch table keyed by top-level `type` and item `type`. Preserve the raw line number and hash on every normalized event. Map `turn.completed.usage` only when present; do not infer cost.

```python
_ITEM_NORMALIZERS = {
    "agent_message": _agent_message,
    "command_execution": _command_execution,
    "file_change": _file_change,
}
```

- [ ] **Step 4: Write failing Codex command-construction and probe tests**

Inject a fake `ProcessRunner` and assert the prepared command contains:

```text
codex exec --json --ephemeral --ignore-user-config --ignore-rules
--sandbox workspace-write --skip-git-repo-check -C <workspace>
```

It must include `-c approval_policy="never"`, pass the task via stdin, never include `--dangerously-bypass-approvals-and-sandbox`, and add a forced Skill instruction only for `FORCED_SKILL`.

- [ ] **Step 5: Run Codex adapter tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_codex_adapter.py
```

Expected: adapter is missing.

- [ ] **Step 6: Implement `CodexCliAdapter` and safe environment policy**

`probe()` uses `shutil.which("codex")`, `codex --version`, and `codex exec --help`. It returns `VERSION_UNSUPPORTED` if `--json`, `--ephemeral`, or `--ignore-user-config` is absent. The environment inherits only OS execution keys plus explicitly configured auth keys; persisted manifests contain key names, never values.

Use stdin prompt construction:

```python
def _prompt(spec: RunSpec, staged_skill: Path | None, workspace: Path) -> str:
    if spec.treatment is Treatment.FORCED_SKILL and staged_skill is not None:
        relative_skill = staged_skill.relative_to(workspace).as_posix()
        return f"Read and follow `{relative_skill}`.\n\n{spec.case.task}"
    return spec.case.task
```

- [ ] **Step 7: Run Codex verification without a live API call**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_codex_normalizer.py tests/test_evalops_codex_adapter.py tests/test_evalops_adapter_contract.py
git diff --check
```

Expected: fixtures and fake-process adapter tests pass; no Codex task is submitted.

- [ ] **Step 8: Commit Codex support**

```powershell
git add rook_agent/evalops/adapters/codex_cli.py rook_agent/evalops/normalizers/codex.py tests/fixtures/evalops/codex tests/test_evalops_codex_adapter.py tests/test_evalops_codex_normalizer.py
git commit -m "feat: add Codex EvalOps adapter"
```

---

### Task 8: Add the Claude Code CLI Adapter and Stream-JSON Normalizer

**Files:**
- Create: `rook_agent/evalops/adapters/claude_cli.py`
- Create: `rook_agent/evalops/normalizers/claude.py`
- Create: `tests/fixtures/evalops/claude/success.jsonl`
- Create: `tests/fixtures/evalops/claude/failure.jsonl`
- Create: `tests/fixtures/evalops/claude/unknown-event.jsonl`
- Test: `tests/test_evalops_claude_adapter.py`
- Test: `tests/test_evalops_claude_normalizer.py`

**Interfaces:**
- Consumes: `ProcessRunner`, `AgentAdapter`, `RunSpec`, `ArtifactStore`, and staged `.claude/skills/<slug>/SKILL.md`.
- Produces: `ClaudeCodeCliAdapter` and `ClaudeTraceNormalizer`.

- [ ] **Step 1: Write failing Claude stream-json fixture tests**

Fixtures cover `system`, `assistant`, `user`, and `result`. Assistant content covers text and `tool_use`; user content covers `tool_result`; result covers success, error subtype, usage, cost, duration, and session id.

```python
def test_claude_normalizer_maps_result_usage_without_fabrication() -> None:
    trace = normalize_fixture("success.jsonl")
    assert trace.usage.input_tokens == 120
    assert trace.usage.output_tokens == 40
    assert trace.cost_usd == Decimal("0.012")
```

When the fixture omits usage or cost, the normalized values must remain `None`.

- [ ] **Step 2: Run Claude normalizer tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_claude_normalizer.py
```

Expected: Claude normalizer is missing.

- [ ] **Step 3: Implement Claude event normalization**

Pair `tool_use.id` with later `tool_result.tool_use_id`. A missing result for a requested tool or missing terminal `result` sets `trace_complete=False`. Preserve unknown non-critical content blocks as raw references.

- [ ] **Step 4: Write failing Claude command-construction and probe tests**

Assert the command contains:

```text
claude -p --output-format stream-json --verbose
--no-session-persistence --setting-sources project
--strict-mcp-config --mcp-config {} --permission-mode dontAsk --no-chrome
```

It must pass `--max-turns` and `--max-budget-usd` only when specified, constrain tools through `--allowed-tools`, set `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `CLAUDE_CODE_SKIP_PROMPT_HISTORY=1`, and never use `--dangerously-skip-permissions`.

- [ ] **Step 5: Run Claude adapter tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_claude_adapter.py
```

Expected: adapter is missing.

- [ ] **Step 6: Implement `ClaudeCodeCliAdapter`**

`probe()` uses `shutil.which("claude")`, `claude --version`, and `claude --help`; it requires print mode, stream-json, no-session-persistence, setting-sources, strict-mcp-config, mcp-config, and permission-mode support. Pass `--strict-mcp-config --mcp-config "{}"` as separate argument-list entries, use project-only settings, no Chrome, and explicit allowed tools. Authentication may use the user's existing CLI login, but user settings, memory, plugins, hooks, and global Skills must not enter the evaluated context.

- [ ] **Step 7: Run Claude verification without a live API call**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_claude_normalizer.py tests/test_evalops_claude_adapter.py tests/test_evalops_adapter_contract.py
git diff --check
```

Expected: fixtures and fake-process tests pass; no Claude task is submitted.

- [ ] **Step 8: Commit Claude Code support**

```powershell
git add rook_agent/evalops/adapters/claude_cli.py rook_agent/evalops/normalizers/claude.py tests/fixtures/evalops/claude tests/test_evalops_claude_adapter.py tests/test_evalops_claude_normalizer.py
git commit -m "feat: add Claude Code EvalOps adapter"
```

---

### Task 9: Implement Deterministic and Optional LLM Evaluators

**Files:**
- Create: `rook_agent/evalops/evaluators/__init__.py`
- Create: `rook_agent/evalops/evaluators/base.py`
- Create: `rook_agent/evalops/evaluators/command.py`
- Create: `rook_agent/evalops/evaluators/file_state.py`
- Create: `rook_agent/evalops/evaluators/trajectory.py`
- Create: `rook_agent/evalops/evaluators/composite.py`
- Create: `rook_agent/evalops/evaluators/llm_judge.py`
- Test: `tests/test_evalops_evaluators.py`
- Test: `tests/test_evalops_llm_judge.py`

**Interfaces:**
- Consumes: `EvalCase`, `EvaluatorSpec`, `NormalizedTrace`, `EvaluationResult`, `ChatProvider`, and isolated initial/final workspace paths.
- Produces: `Evaluator`, `EvaluatorFactory.create(spec)`, deterministic evaluator implementations, `CompositeEvaluator`, and optional `LlmJudgeEvaluator`.

- [ ] **Step 1: Write failing deterministic evaluator tests**

Cover command exit codes, hidden command path, file existence/content/hash, required and forbidden tools, required successful verification, and deterministic composition:

```python
def test_file_state_evaluator_checks_final_hash(tmp_path: Path) -> None:
    final = tmp_path / "final"
    final.mkdir()
    (final / "result.txt").write_text("done\n", encoding="utf-8")
    result = FileStateEvaluator(expected_sha256={"result.txt": sha256_text("done\n")}).evaluate(
        initial_workspace=tmp_path / "initial",
        final_workspace=final,
        trace=complete_trace(),
    )
    assert result.passed is True
```

The evaluator must reject any expected path escaping `final_workspace`.

- [ ] **Step 2: Run deterministic evaluator tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_evaluators.py
```

Expected: evaluator modules are missing.

- [ ] **Step 3: Implement the evaluator protocol and factory**

Define:

```python
class Evaluator(Protocol):
    def evaluate(
        self,
        *,
        initial_workspace: Path,
        final_workspace: Path,
        trace: NormalizedTrace,
    ) -> EvaluationResult: ...
```

`EvaluatorFactory` accepts only `command`, `file_state`, `trajectory`, `composite`, and `llm_judge`. Unknown kinds fail suite loading rather than runtime execution.

- [ ] **Step 4: Implement hidden command and state evaluators**

`CommandEvaluator` receives an already resolved command from suite loading, executes after the Agent run, and may read the final workspace. The command executable or script remains outside the Agent workspace. `FileStateEvaluator` resolves every target beneath final workspace. `TrajectoryEvaluator` uses normalized events only.

- [ ] **Step 5: Write failing LLM judge tests with a fake provider**

Test strict JSON parsing, `tool_choice="none"`, bounded tokens, redacted inputs, unknown fields, invalid booleans, provider error, and the rule that an LLM pass cannot override a deterministic failure:

```python
def test_llm_judge_uses_no_tools_and_parses_strict_result() -> None:
    provider = RecordingProvider('{"passed": true, "reason": "meets rubric"}')
    result = LlmJudgeEvaluator(provider, rubric="Answer is complete.").evaluate(**sample_inputs())
    assert provider.requests[0].tool_choice == "none"
    assert result.passed is True
```

- [ ] **Step 6: Run LLM judge tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_llm_judge.py
```

Expected: optional judge is missing.

- [ ] **Step 7: Implement bounded optional LLM judging**

Send only task text, final answer, evaluator rubric, and a redacted bounded trace summary. Parse exactly:

```json
{"passed": true, "reason": "short explanation"}
```

Reject unknown fields and reasons longer than 500 characters. `CompositeEvaluator` evaluates deterministic children first and does not invoke the judge after a deterministic failure.

- [ ] **Step 8: Run evaluator verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_evaluators.py tests/test_evalops_llm_judge.py tests/test_providers.py tests/test_provider_errors.py
git diff --check
```

Expected: all listed tests pass.

- [ ] **Step 9: Commit evaluators**

```powershell
git add rook_agent/evalops/evaluators tests/test_evalops_evaluators.py tests/test_evalops_llm_judge.py
git commit -m "feat: evaluate Agent outcomes deterministically"
```

---

### Task 10: Orchestrate Paired Baseline, Forced, and Routed Runs

**Files:**
- Create: `rook_agent/evalops/runner.py`
- Test: `tests/test_evalops_runner.py`

**Interfaces:**
- Consumes: `EvalSuite`, `AgentTarget`, `SkillCandidate`, `WorkspaceManager`, `SkillMaterializer`, `AgentAdapter`, `EvaluatorFactory`, and `ArtifactStore`.
- Produces: `build_experiment_plan(...) -> ExperimentPlan` and `ExperimentRunner.run(plan) -> ExperimentRecord`.

- [ ] **Step 1: Write failing experiment-plan tests**

Assert that every case creates a Baseline/Forced Skill pair for content evaluation and a separate Baseline/Routed Skill pair for routing evaluation. Direct and transfer cases are routing-relevant; regression and adversarial cases are routing-negative. Pair ids are stable, repetitions are explicit, and order alternates to reduce time bias:

```python
def test_plan_alternates_pair_order() -> None:
    plan = build_experiment_plan(sample_suite(), targets=(rook_target(),), candidate=sample_candidate(), repetitions=2)
    assert [run.treatment for run in plan.runs[:4]] == [
        Treatment.BASELINE,
        Treatment.FORCED_SKILL,
        Treatment.FORCED_SKILL,
        Treatment.BASELINE,
    ]
```

- [ ] **Step 2: Run plan tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_runner.py -k plan
```

Expected: experiment planning functions are missing.

- [ ] **Step 3: Implement stable experiment planning**

`experiment_id` is a new id; `pair_id` is a stable hash of suite fingerprint, case id, target fingerprint, repetition, and treatment family (`content` or `routing`). The plan records Fast or Full phase, exact RunSpecs, routing relevance, and the manifest fingerprint. Baseline results are not shared across treatment families because each comparison must remain a separately auditable pair.

- [ ] **Step 4: Write failing runner isolation and artifact tests**

Use `FakeAgentAdapter` and prove:

- every pair starts from equal workspace hashes;
- baseline never sees the candidate;
- forced and routed workspaces contain only their staged candidate;
- raw events are persisted before normalization;
- evaluator runs after the Agent;
- malformed critical trace blocks a pass;
- infrastructure errors remain distinct;
- cancellation stops later runs and keeps partial artifacts;
- a rerun creates a new experiment id.

```python
def test_runner_persists_raw_before_normalization_failure(tmp_path: Path) -> None:
    record = make_runner(tmp_path, adapter=malformed_fake()).run(sample_plan())
    assert record.runs[0].status is RunStatus.ADAPTER_ERROR
    assert record.runs[0].raw_event_refs
```

- [ ] **Step 5: Run runner tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_runner.py
```

Expected: runner execution is missing.

- [ ] **Step 6: Implement sequential paired orchestration**

For each RunSpec:

```python
pair = workspace_manager.create_pair(spec.case.fixture, spec.pair_id)
workspace = pair.baseline if spec.treatment is Treatment.BASELINE else pair.candidate
staged = materializer.materialize(spec.skill, spec.target.type, workspace) if spec.skill else None
prepared = adapter.prepare(spec, workspace, staged_skill=staged)
agent_run = adapter.run(prepared)
evaluation = evaluator.evaluate(initial_workspace=pair.snapshot, final_workspace=workspace, trace=agent_run.trace)
```

The actual implementation must use `try/finally` to persist the terminal manifest and cleanup status without deleting evidence.

- [ ] **Step 7: Add Fast/Full case selection without scoring**

`select_fast_cases()` takes the first deterministically sorted configured count per category. `select_full_cases()` uses every suite case. Runner does not decide promotion; it only executes the requested phase.

- [ ] **Step 8: Run paired orchestration verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_runner.py tests/test_evalops_workspace.py tests/test_evalops_artifacts.py tests/test_evalops_adapter_contract.py tests/test_evalops_evaluators.py
git diff --check
```

Expected: all listed tests pass.

- [ ] **Step 9: Commit experiment orchestration**

```powershell
git add rook_agent/evalops/runner.py tests/test_evalops_runner.py
git commit -m "feat: run paired Skill experiments"
```

---

### Task 11: Build Transparent ScoreCards and Promotion Policies

**Files:**
- Create: `rook_agent/evalops/scoring.py`
- Create: `rook_agent/evalops/policy.py`
- Test: `tests/test_evalops_scoring.py`
- Test: `tests/test_evalops_policy.py`

**Interfaces:**
- Consumes: `ExperimentRecord`, `RunStatus`, `Treatment`, `CaseCategory`, and versioned policy config from `EvalSuite`.
- Produces: `ScoreCardBuilder.build(record) -> ScoreCard`, `PromotionPolicy.evaluate(scorecard) -> PromotionDecision`, and `FastGatePolicy.evaluate(scorecard)`.

- [ ] **Step 1: Write failing metric formula tests**

Test exact formulas:

```python
success_rate = successful_valid_runs / valid_runs
efficiency_improvement = 1 - candidate_median / baseline_median
routing_precision = relevant_and_loaded / loaded
routing_recall = relevant_and_loaded / relevant
```

If a denominator is zero or telemetry is absent, the value is `None`. Infrastructure statuses are excluded from valid runs but counted in `infra_error_count`.

- [ ] **Step 2: Run scoring tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_scoring.py
```

Expected: ScoreCard builder is missing.

- [ ] **Step 3: Implement paired aggregation and intervals**

Use stdlib `statistics.median` and deterministic percentile interpolation. For binary success rates, expose Wilson 95% bounds using a fixed `z=1.959963984540054`. Store sample count, median, quartiles, paired deltas, observed fields, missing fields, and per-case failures.

- [ ] **Step 4: Write failing hard-gate and effect-policy tests**

Cover:

- any safety failure rejects;
- any new regression failure rejects;
- incomplete traces quarantine;
- too few valid pairs quarantine;
- success uplift at threshold promotes;
- non-inferior success plus efficiency improvement promotes;
- lower success rejects even with lower cost;
- routed precision or recall below configured thresholds rejects routed activation without invalidating Forced Skill content evidence;
- different Agent/model/suite/policy fingerprints produce independent decisions.

```python
def test_safety_failure_cannot_be_offset_by_efficiency() -> None:
    decision = PromotionPolicy(default_policy()).evaluate(scorecard(safety_failures=1, efficiency_improvement=0.80))
    assert decision.status is PromotionStatus.REJECTED
    assert decision.reason_code == "safety_failure"
```

- [ ] **Step 5: Run policy tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_policy.py
```

Expected: policy is missing.

- [ ] **Step 6: Implement fail-fast hard gates and explicit effect paths**

Evaluate in this order:

```text
valid experiment
-> safety
-> secrets
-> regression
-> trace coverage
-> sample count
-> success uplift
-> non-inferior success plus efficiency
-> routed precision/recall
```

Every branch returns a stable reason code. Do not collapse forced-content and routed-activation decisions into one boolean; ScoreCard and decision expose both.

- [ ] **Step 7: Implement Fast Gate decisions**

Fast Gate returns `continue_full`, `rejected`, or `quarantined`. Safety/regression failures reject immediately; all-infrastructure results quarantine; no improvement across valid direct/transfer pairs rejects; otherwise continue to Full Gate.

- [ ] **Step 8: Run ScoreCard and policy verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_scoring.py tests/test_evalops_policy.py tests/test_evalops_runner.py
git diff --check
```

Expected: all listed tests pass.

- [ ] **Step 9: Commit scoring and policy**

```powershell
git add rook_agent/evalops/scoring.py rook_agent/evalops/policy.py tests/test_evalops_scoring.py tests/test_evalops_policy.py
git commit -m "feat: gate Skill promotion with EvalOps"
```

---

### Task 12: Add the Promotion Registry, Reports, and End-to-End Evaluation Service

**Files:**
- Create: `rook_agent/evalops/registry.py`
- Create: `rook_agent/evalops/report.py`
- Create: `rook_agent/evalops/service.py`
- Test: `tests/test_evalops_registry.py`
- Test: `tests/test_evalops_report.py`
- Test: `tests/test_evalops_service.py`

**Interfaces:**
- Consumes: `CandidateStore`, `ExperimentRunner`, `ScoreCardBuilder`, `FastGatePolicy`, `PromotionPolicy`, `ArtifactStore`, and `PromotionDecision`.
- Produces: `PromotionRegistry`, `ReportRenderer`, and `EvalOpsService.evaluate_candidate(...) -> EvaluationSummary`.

- [ ] **Step 1: Write failing registry state-machine tests**

Cover per-Agent active versions, immutable decisions, atomic pointer changes, stale detection, rollback, no eligible prior version, corrupt registry fail-closed behavior, and target fingerprint changes:

```python
def test_registry_tracks_independent_agent_versions(tmp_path: Path) -> None:
    registry = PromotionRegistry(tmp_path)
    registry.record(promoted_decision(agent=AgentType.ROOK, version=2))
    registry.record(promoted_decision(agent=AgentType.CODEX, version=1))
    assert registry.active_version("skill", AgentType.ROOK) == 2
    assert registry.active_version("skill", AgentType.CODEX) == 1
    assert registry.active_version("skill", AgentType.CLAUDE_CODE) is None
```

- [ ] **Step 2: Run registry tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_registry.py
```

Expected: PromotionRegistry is missing.

- [ ] **Step 3: Implement immutable history and atomic active pointers**

Store decisions under:

```text
.rook/skill-registry/<slug>/history/<decision-id>.json
.rook/skill-registry/<slug>/registry.json
```

`registry.json` contains per-target active version and fingerprint. A content, Agent, model, suite, policy, or critical normalizer fingerprint change returns `STALE` until a matching decision is recorded.

- [ ] **Step 4: Write failing JSON and Markdown report tests**

Golden tests assert sorted JSON, explicit `null` fields, no secret values, per-Agent baseline/candidate tables, per-case failures, decision reason, observed fields, and relative rather than fabricated cross-Agent comparisons.

- [ ] **Step 5: Run report tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_report.py
```

Expected: ReportRenderer is missing.

- [ ] **Step 6: Implement deterministic report rendering**

`ReportRenderer.write(summary, artifact_store)` writes `scorecard.json` and `report.md`. Markdown headings and table order are stable by Agent type and case id. Missing metrics display `not observed`, not `0`.

- [ ] **Step 7: Write failing service pipeline tests**

Use fake targets and assert:

```text
Fast plan -> Fast runs -> Fast ScoreCard -> continue_full
-> Full plan -> Full runs -> Full ScoreCard
-> per-Agent decisions -> registry -> reports
```

Also cover Fast rejection without Full calls, one unavailable Agent not blocking other targets, report persistence on registry failure, and no external export.

- [ ] **Step 8: Implement `EvalOpsService`**

```python
class EvalOpsService:
    def evaluate_candidate(
        self,
        candidate: SkillCandidate,
        suite: EvalSuite,
        targets: tuple[AgentTarget, ...],
    ) -> EvaluationSummary:
        ...
```

The service evaluates each target independently, runs Full only after Fast continuation, records decisions after reports are safely persisted, and returns partial summaries for unavailable targets.

- [ ] **Step 9: Run registry/report/service verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_registry.py tests/test_evalops_report.py tests/test_evalops_service.py tests/test_evalops_scoring.py tests/test_evalops_policy.py tests/test_evalops_runner.py
git diff --check
```

Expected: all listed tests pass.

- [ ] **Step 10: Commit the EvalOps pipeline**

```powershell
git add rook_agent/evalops/registry.py rook_agent/evalops/report.py rook_agent/evalops/service.py tests/test_evalops_registry.py tests/test_evalops_report.py tests/test_evalops_service.py
git commit -m "feat: publish evaluated Skill decisions"
```

---

### Task 13: Expose Doctor, Eval, Status, Rollback, and Explicit Export Commands

**Files:**
- Modify: `rook_agent/cli.py`
- Create: `rook_agent/evalops/cli.py`
- Test: `tests/test_evalops_cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `EvalOpsService`, `CandidateStore`, `PromotionRegistry`, adapters, `load_eval_suite`, and `SkillMaterializer`.
- Produces: `rook eval doctor`, `rook eval run`, `rook eval report`, `rook skill status`, `rook skill rollback`, and `rook skill export`.

- [ ] **Step 1: Write failing parser and dispatch tests**

Assert these forms parse without changing legacy single-turn behavior:

```text
rook eval doctor
rook eval run --skill-path <candidate> --suite <suite.toml> --agents rook,codex,claude
rook eval report <experiment-id>
rook skill status <name>
rook skill rollback <name> --agent codex --to-version 1
rook skill export <name> --agent claude --output <directory>
```

Unknown Agents, missing suite/candidate, invalid version, and export without a matching promoted decision return exit code 2.

- [ ] **Step 2: Run CLI parser tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_cli.py -k parser
```

Expected: EvalOps subcommands are absent.

- [ ] **Step 3: Refactor CLI dispatch without changing existing defaults**

Add nested subparsers and dispatch before TUI/single-message selection:

```python
if args.command == "eval":
    return run_eval_command(args)
if args.command == "skill":
    return run_skill_registry_command(args)
if args.command == "config":
    return run_config_command(args)
```

Keep current top-level `--project`, message, interactive, TUI, benchmark, and config behavior.

- [ ] **Step 4: Write failing Doctor tests**

Inject fake adapters and assert Doctor reports executable, version, structured output capability, auth/probe status, and isolation capability. Secret values must never appear. A missing Claude CLI does not make Rook/Codex Doctor entries disappear.

- [ ] **Step 5: Implement command handlers with dependency injection**

`rook_agent.evalops.cli` builds services through injectable factories so unit tests never invoke real CLIs. `eval run` prints the experiment id, report path, and one decision line per target. `eval report` reads immutable artifacts only.

- [ ] **Step 6: Implement explicit export boundaries**

`skill export` requires a non-stale `PROMOTED` decision matching target fingerprint. It writes only beneath the user-provided `--output` directory. The MVP has no override for real global configuration: reject any resolved destination equal to or beneath `~/.codex` or `~/.claude`. Users can inspect the exported directory and copy it themselves after evaluation.

- [ ] **Step 7: Run CLI regressions**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_cli.py tests/test_cli.py tests/test_brand_contract.py
git diff --check
```

Expected: new commands and all existing CLI behavior pass.

- [ ] **Step 8: Commit CLI commands**

```powershell
git add rook_agent/cli.py rook_agent/evalops/cli.py tests/test_evalops_cli.py tests/test_cli.py
git commit -m "feat: expose Agent EvalOps CLI"
```

---

### Task 14: Convert Execution-Grounded Forge Output into Quarantined Candidates

**Files:**
- Create: `rook_agent/evolution/distiller.py`
- Create: `rook_agent/evolution/candidates.py`
- Create: `rook_agent/evolution/coordinator.py`
- Modify: `rook_agent/evolution/models.py`
- Modify: `rook_agent/evolution/__init__.py`
- Modify: `rook_agent/evolution/events.py`
- Modify: `rook_agent/agent/loop.py`
- Modify: `rook_agent/app/factory.py`
- Modify: `rook_agent/app/runtime.py`
- Modify: `rook_agent/app/tui.py`
- Test: `tests/test_evolution_distiller.py`
- Test: `tests/test_evolution_candidates.py`
- Test: `tests/test_evolution_coordinator.py`
- Modify: `tests/test_agent_context_loop.py`
- Modify: `tests/test_app_factory.py`
- Modify: `tests/test_app_runtime.py`
- Modify: `tests/test_app_tui.py`

**Interfaces:**
- Consumes: `TaskTraceBuilder`, `EvidenceClassifier`, `evaluate_skill_delta`, `ChatProvider`, `CandidateStore`, and `SkillBundle`.
- Produces: `ExperienceDistiller.distill(trace, max_skills)`, `CandidateService.propose(trace)`, and best-effort `CandidateCoordinator` lifecycle hooks that never publish directly.

- [ ] **Step 1: Write failing strict distiller tests**

Test `tool_choice="none"`, temperature 0, bounded output, exact top-level shape `{"skills": [...]}`, maximum candidate count, unknown fields, non-list sections, invented evidence refs, invalid JSON, one format retry, and no write on parse failure:

```python
def test_distiller_resolves_only_real_evidence_refs() -> None:
    provider = RecordingProvider(valid_delta_json(ref="event-1:part-1"))
    deltas = ExperienceDistiller(provider).distill(trace_with_ref("event-1", "part-1"), max_skills=2)
    assert deltas[0].evidence_refs[0].event_id == "event-1"
    assert provider.requests[0].tool_choice == "none"
```

- [ ] **Step 2: Run distiller tests and observe RED**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_distiller.py
```

Expected: ExperienceDistiller is absent.

- [ ] **Step 3: Implement strict evidence-bound distillation**

The model emits only `event_id:part_id`; the parser resolves full `EvidenceRef` values from the current trace lookup. It cannot invent session ids, segment ids, archive ids, paths, status, or version. Redact trace content before provider input.

- [ ] **Step 4: Write failing CandidateService tests**

Cover eligible trace, ineligible trace, zero deltas, Gate rejection, global-to-project downgrade, multiple candidates, duplicate content, CandidateStore failure, event redaction, and candidate initial status `candidate`/quarantined from active use.

- [ ] **Step 5: Implement `SkillDelta -> SkillBundle -> CandidateStore`**

```python
def bundle_from_delta(delta: SkillDelta, *, slug: str) -> SkillBundle:
    return SkillBundle(
        name=slug,
        description=delta.description,
        triggers=delta.triggers,
        procedure=delta.procedure,
        verification=delta.verification,
        pitfalls=delta.pitfalls,
        evidence_refs=delta.evidence_refs,
    )
```

CandidateService appends `skill_candidate_created` or `skill_candidate_rejected` events with hashes/reason codes only. Add both names to the explicit evolution-event type allowlist and its serialization tests. It never writes a discoverable active Skill.

- [ ] **Step 6: Write failing lifecycle tests**

Assert disabled mode creates no coordinator, no provider call, no candidate and no evolution event. Enabled mode processes verified completed segments once, task switch/normal close flush best-effort, provider switch updates the distiller, and any exception returns the user's original response unchanged.

- [ ] **Step 7: Implement best-effort coordinator wiring**

Add an optional `candidate_coordinator` lifecycle interface to the existing Agent loop/runtime path. Idempotency uses terminal candidate events keyed by `segment_id`. Candidate creation does not call EvalOps automatically in the user's foreground turn; it queues or records the candidate for explicit `rook eval run`.

- [ ] **Step 8: Run evolution and lifecycle verification**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_distiller.py tests/test_evolution_candidates.py tests/test_evolution_coordinator.py tests/test_evolution_config.py tests/test_evolution_events.py tests/test_evolution_trace.py tests/test_evolution_evidence.py tests/test_evolution_gate.py tests/test_agent_context_loop.py tests/test_app_factory.py tests/test_app_runtime.py tests/test_app_tui.py
git diff --check
```

Expected: all new and touched tests pass.

- [ ] **Step 9: Commit quarantined candidate generation**

```powershell
git add rook_agent/evolution rook_agent/agent/loop.py rook_agent/app/factory.py rook_agent/app/runtime.py rook_agent/app/tui.py tests/test_evolution_distiller.py tests/test_evolution_candidates.py tests/test_evolution_coordinator.py tests/test_agent_context_loop.py tests/test_app_factory.py tests/test_app_runtime.py tests/test_app_tui.py
git commit -m "feat: generate quarantined Skill candidates"
```

---

### Task 15: Add a Reproducible Demo Suite, Optional Live Smoke Tests, and Documentation

**Files:**
- Create: `evals/policies/default.toml`
- Create: `evals/suites/demo/suite.toml`
- Create: `evals/suites/demo/cases/direct-01/task.md`
- Create: `evals/suites/demo/cases/direct-01/fixture/`
- Create: `evals/suites/demo/cases/transfer-01/task.md`
- Create: `evals/suites/demo/cases/transfer-01/fixture/`
- Create: `evals/suites/demo/cases/regression-01/task.md`
- Create: `evals/suites/demo/cases/regression-01/fixture/`
- Create: `evals/suites/demo/cases/adversarial-01/task.md`
- Create: `evals/suites/demo/cases/adversarial-01/fixture/`
- Create: `evals/suites/demo/evaluators/check_result.py`
- Create: `tests/test_evalops_demo_suite.py`
- Create: `tests/test_evalops_external_smoke.py`
- Create: `docs/ROOK_EVALOPS.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `tests/test_readme_provider_docs.py`

**Interfaces:**
- Consumes: full EvalOps service and CLI.
- Produces: one deterministic four-category demo suite, opt-in live Codex/Claude probes, accurate user documentation, and resume-safe wording.

- [ ] **Step 1: Write failing demo-suite tests**

The suite must load, contain exactly one case per required category, keep hidden evaluator code outside every fixture, run with `FakeAgentAdapter`, and produce different per-Agent decisions in a deterministic fixture.

- [ ] **Step 2: Create a minimal file-transformation demo**

Use a task family that requires reading an input file, applying a deterministic normalization rule, and writing `result.txt`. Direct and transfer cases vary filenames/data; regression requires a conflicting rule; adversarial includes untrusted text that must remain data. `check_result.py` compares final bytes to hidden expected content and emits JSON with `passed` and `reason`.

- [ ] **Step 3: Add the versioned default policy**

Create:

```toml
[requirements]
min_valid_pairs = 6
max_safety_failures = 0
max_new_regression_failures = 0
min_trace_coverage = 0.95

[effect]
min_success_uplift = 0.10
min_efficiency_improvement = 0.15

[routing]
min_precision = 0.80
min_recall = 0.80

[fast]
cases_per_category = 1
repetitions = 1

[full]
repetitions = 3
```

`min_valid_pairs` is counted across direct and transfer cases after repetitions for one Agent target; regression and adversarial cases remain hard gates rather than uplift-pair contributors. The four-case demo therefore exercises policy shape, while the three-repetition full profile supplies six valid uplift pairs per target.

- [ ] **Step 4: Run the deterministic demo test**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo_suite.py
```

Expected: the fake end-to-end demo passes without network access.

- [ ] **Step 5: Write opt-in live smoke tests**

At module level:

```python
pytestmark = pytest.mark.skipif(
    os.environ.get("ROOK_RUN_EXTERNAL_EVALS") != "1",
    reason="set ROOK_RUN_EXTERNAL_EVALS=1 to run paid external Agent smoke tests",
)
```

Each smoke test runs one bounded read-only case, verifies adapter probe, structured trace, terminal result, no user config write, and a strict timeout. A missing auth token/login reports a skip with the adapter reason rather than a false capability failure.

- [ ] **Step 6: Document architecture, commands, cost boundary, and limitations**

`docs/ROOK_EVALOPS.md` covers:

- Baseline/Forced/Routed distinction;
- Direct/Transfer/Regression/Adversarial cases;
- Rook/Codex/Claude CLI requirements;
- `.rook` storage and explicit export;
- ScoreCard fields and promotion reasons;
- real smoke-test opt-in and potential API cost;
- security model and hidden evaluators;
- limitations and non-claims;
- one complete PowerShell 7 demo.

README files link to the document and use the approved positioning, not “Claude Code replacement” or “model self-training”.

- [ ] **Step 7: Run documentation and optional-smoke default verification**

```powershell
Remove-Item Env:ROOK_RUN_EXTERNAL_EVALS -ErrorAction SilentlyContinue
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_demo_suite.py tests/test_evalops_external_smoke.py tests/test_readme_provider_docs.py tests/test_brand_contract.py
git diff --check
```

Expected: demo/docs tests pass; live smoke tests are skipped with the explicit reason.

- [ ] **Step 8: Commit demo and docs**

```powershell
git add evals docs/ROOK_EVALOPS.md README.md README.zh-CN.md tests/test_evalops_demo_suite.py tests/test_evalops_external_smoke.py tests/test_readme_provider_docs.py
git commit -m "docs: demonstrate Rook Agent EvalOps"
```

---

### Task 16: Run Final Security, Regression, Baseline, and Optional External Verification

**Files:**
- Create: `docs/superpowers/reports/2026-07-15-rook-agent-evalops-verification.md`

If verification exposes a source defect, return to its owning Task 1-15, add the failing regression test and fix there, commit that scoped repair, and rerun Task 16 from Step 1. Do not hide a late source change inside the report commit.

**Interfaces:**
- Consumes: every completed task and the approved design acceptance criteria.
- Produces: fresh verification evidence, no new core failures, an optional external smoke record, and a final scoped hardening commit if needed.

- [ ] **Step 1: Run all EvalOps and evolution tests in one fresh process**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_models.py tests/test_evalops_suites.py tests/test_evalops_workspace.py tests/test_evalops_artifacts.py tests/test_evalops_skills.py tests/test_evalops_candidates.py tests/test_evalops_process.py tests/test_evalops_adapter_contract.py tests/test_evalops_rook_adapter.py tests/test_evalops_codex_adapter.py tests/test_evalops_codex_normalizer.py tests/test_evalops_claude_adapter.py tests/test_evalops_claude_normalizer.py tests/test_evalops_evaluators.py tests/test_evalops_llm_judge.py tests/test_evalops_runner.py tests/test_evalops_scoring.py tests/test_evalops_policy.py tests/test_evalops_registry.py tests/test_evalops_report.py tests/test_evalops_service.py tests/test_evalops_cli.py tests/test_evalops_demo_suite.py tests/test_evolution_config.py tests/test_evolution_events.py tests/test_evolution_trace.py tests/test_evolution_evidence.py tests/test_evolution_gate.py tests/test_evolution_distiller.py tests/test_evolution_candidates.py tests/test_evolution_coordinator.py
```

Expected: all listed tests pass.

- [ ] **Step 2: Run directly affected regressions**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_eval_adapter.py tests/test_eval_tasks.py tests/test_agent_context_loop.py tests/test_agent_skill_flow.py tests/test_skill_discovery.py tests/test_skill_loader.py tests/test_skill_router.py tests/test_app_factory.py tests/test_cli.py tests/test_providers.py tests/test_provider_errors.py tests/test_session_redaction.py tests/test_permissions_policy.py tests/test_utils_subprocess.py tests/test_readme_provider_docs.py tests/test_brand_contract.py
```

Expected: all listed tests pass.

- [ ] **Step 3: Prove default verification performs no live external calls**

```powershell
Remove-Item Env:ROOK_RUN_EXTERNAL_EVALS -ErrorAction SilentlyContinue
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_external_smoke.py
```

Expected: all live tests skip with the explicit opt-in reason and no Codex/Claude process starts.

- [ ] **Step 4: Run the full core baseline comparison**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q --ignore=tests/test_evalplus_benchmark.py
```

Expected: no new failing test names beyond the recorded pre-EvalOps set. Record exact pass/fail/skip totals and compare failing names in the verification report.

- [ ] **Step 5: Run the optional EvalPlus gate separately**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalplus_benchmark.py
```

Expected on the current machine: collection reports missing optional `evalplus`. Record it without hiding or reclassifying the dependency.

- [ ] **Step 6: Optionally run bounded live adapters when the user authorizes API cost**

```powershell
$env:ROOK_RUN_EXTERNAL_EVALS = '1'
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalops_external_smoke.py
Remove-Item Env:ROOK_RUN_EXTERNAL_EVALS
```

Expected: authenticated installed targets pass; unavailable authentication is recorded explicitly. Do not run this step without user authorization for external calls and cost.

- [ ] **Step 7: Verify repository hygiene and design acceptance**

```powershell
git diff --check
git status --short
git log --oneline --decorate -20
```

Manually map every item in design section 19 to a passing test or report entry. Confirm no `.rook/eval-runs`, candidate, raw event, credential, or user global Agent configuration is staged.

- [ ] **Step 8: Write the verification report**

Record:

- exact focused and core counts;
- unchanged baseline failures;
- EvalPlus optional result;
- live-smoke skipped or authorized result;
- one demo experiment id and report path;
- per-Agent decisions;
- security and isolation evidence;
- remaining limitations and non-claims.

- [ ] **Step 9: Commit the verification report**

```powershell
git add docs/superpowers/reports/2026-07-15-rook-agent-evalops-verification.md
git commit -m "docs: record Agent EvalOps verification"
```

---

## Final Handoff Criteria

The implementation is ready for branch completion only when:

- Task 1's existing security review is closed with a clean fresh review;
- every Task 2-15 focused suite passes;
- default tests make no paid external calls;
- the full core run introduces no new failing test names;
- a candidate produces isolated Baseline and Skill runs for all three targets;
- forced content effectiveness and routed retrieval are reported separately;
- direct, transfer, regression, and adversarial cases are represented;
- raw events are redacted before disk and remain traceable to normalized events;
- per-Agent promotion decisions are independent and explainable;
- promotion does not modify real Codex or Claude configuration;
- stale detection and rollback are tested end to end;
- the final report uses the approved resume-safe wording;
- `superpowers:finishing-a-development-branch` is used to offer merge, PR, keep, or discard options.
