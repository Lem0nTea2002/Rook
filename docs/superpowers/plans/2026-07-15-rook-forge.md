# Rook Forge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Rook Forge, an opt-in, execution-grounded Skill evolution pipeline that converts verified session trajectories into safe, versioned Markdown Skills and reuses them through Rook's deterministic router.

**Architecture:** Add a standalone `rook_agent.evolution` subsystem. It reads append-only session events, builds stable task segments, classifies evidence, asks the active provider for strict `SkillDelta` JSON, applies deterministic governance and novelty gates, and writes only generated Skill directories through an atomic/versioned store. `AgentLoop` exposes completion notifications; application wiring owns flush and provider-switch behavior. Existing handwritten Skill behavior remains unchanged when Forge is disabled.

**Tech Stack:** Python 3.11+, dataclasses, `StrEnum`, JSON/JSONL, TOML via `tomllib`, SHA-256 helpers already in Rook, pytest, existing `ChatProvider` abstraction, Markdown Skill frontmatter.

## Global Constraints

- Treat [the approved design](../specs/2026-07-15-rook-forge-design.md) as the source of truth. This plan does not add model-weight training, source self-modification, vector storage, or automatic Skill deletion.
- Use `& '.\.venv\Scripts\python.exe'` for every Python command. On this machine, bare `python` resolves to the Windows Store alias and exits with code 9009.
- Forge remains disabled unless `[evolution].enabled = true`. With Forge disabled, no extra provider request, filesystem write, or evolution event may occur.
- The current full-core baseline is `27 failed, 956 passed, 3 skipped` with `tests/test_evalplus_benchmark.py` excluded. New work must make all new and directly touched tests pass and must not add failures beyond that recorded baseline.
- `tests/test_evalplus_benchmark.py` currently fails during collection because optional package `evalplus` is absent. Keep it as a separate optional verification gate; do not hide the missing dependency.
- `.rook/` is already ignored by `.gitignore`; preserve that rule so generated project Skills never dirty the user's worktree.
- Every Forge exception is best-effort: append a redacted `forge_failed` event and return the user's original response unchanged.
- Never log or persist the matched secret text. Events may contain only stable reason codes, identifiers, counts, scopes, and hashes.
- Use `apply_patch` for source changes. Keep commits scoped to the task boundaries below.

---

### Task 1: Establish the evolution protocol, configuration, and audit events

**Files:**

- Create: `rook_agent/evolution/__init__.py`
- Create: `rook_agent/evolution/models.py`
- Create: `rook_agent/evolution/events.py`
- Modify: `rook_agent/config/settings.py`
- Test: `tests/test_evolution_config.py`
- Test: `tests/test_evolution_events.py`

**Public interfaces:**

```python
class EvolutionScope(StrEnum):
    AUTO = "auto"
    PROJECT = "project"
    GLOBAL = "global"

class EvidenceSource(StrEnum):
    LOCAL_EXECUTION = "local_execution"
    WORKSPACE_STATE = "workspace_state"
    USER_STATEMENT = "user_statement"
    EXTERNAL_CONTENT = "external_content"
    MODEL_STATEMENT = "model_statement"

class TraceOutcome(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    RECOVERED_FAILURE = "recovered_failure"
    STATE_VERIFIED_SUCCESS = "state_verified_success"
    COMPLETED_WITHOUT_VERIFIER = "completed_without_verifier"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

class GateStatus(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    DOWNGRADE_TO_PROJECT = "downgrade_to_project"

class CurationAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    SKIP = "skip"

@dataclass(frozen=True, slots=True)
class EvolutionConfig:
    enabled: bool = False
    scope: EvolutionScope = EvolutionScope.AUTO
    allow_global: bool = True
    max_skills_per_task: int = 2

@dataclass(frozen=True, slots=True)
class EvidenceRef:
    session_id: str
    segment_id: str
    event_id: str
    part_id: str
    archive_id: str | None = None

@dataclass(frozen=True, slots=True)
class EvidenceItem:
    ref: EvidenceRef
    source: EvidenceSource
    tool_name: str | None
    ok: bool | None
    content: str
    data: dict[str, object]

@dataclass(frozen=True, slots=True)
class TaskTrace:
    session_id: str
    segment_id: str
    first_event_id: str
    last_event_id: str
    user_goal: str
    final_answer: str
    evidence: tuple[EvidenceItem, ...]
    event_ids: tuple[str, ...]
    loaded_skill_hashes: tuple[str, ...]
    is_closed: bool = False

@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    outcome: TraceOutcome
    reason_code: str

@dataclass(frozen=True, slots=True)
class SkillDelta:
    should_write: bool
    title: str
    description: str
    triggers: tuple[str, ...]
    proposed_scope: EvolutionScope
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    confidence: str

@dataclass(frozen=True, slots=True)
class GateDecision:
    status: GateStatus
    scope: EvolutionScope | None
    reason_code: str
    delta: SkillDelta | None

@dataclass(frozen=True, slots=True)
class SkillDocument:
    slug: str
    title: str
    description: str
    triggers: tuple[str, ...]
    scope: EvolutionScope
    version: int
    procedure: tuple[str, ...]
    verification: tuple[str, ...]
    pitfalls: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class CurationPlan:
    action: CurationAction
    reason_code: str
    document: SkillDocument | None
    existing_path: str | None = None
    base_content_hash: str | None = None

@dataclass(frozen=True, slots=True)
class StoredSkill:
    name: str
    scope: EvolutionScope
    path: str
    version: int
    content_hash: str

@dataclass(frozen=True, slots=True)
class ForgeResult:
    segment_id: str
    status: str
    reason_code: str
    stored_skill: StoredSkill | None = None
```

Add a raw nested-section accessor to `AppConfig` so `False` and `0` are not mistaken for missing values:

```python
def get_section_value(self, section: str, name: str, *, default: Any = None) -> Any:
    for config in (self.project_config, self.global_config):
        section_value = config.get(section) if config else None
        if isinstance(section_value, dict) and name in section_value:
            return section_value[name]
    return default
```

Expose `load_evolution_config(config: AppConfig) -> EvolutionConfig`. It must coerce booleans with the existing `_bool_value_from_raw`, validate `scope` through `EvolutionScope`, and require `1 <= max_skills_per_task <= 2`.

**Steps:**

- [ ] 1.1 Write configuration tests for defaults, project-over-global precedence, false values, all three scopes, and invalid scope/count.

```python
def test_evolution_defaults_to_disabled() -> None:
    config = AppConfig(provider_name="openai", env={})
    assert load_evolution_config(config) == EvolutionConfig()

def test_project_evolution_config_overrides_global() -> None:
    config = AppConfig(
        provider_name="openai",
        env={},
        project_config={"evolution": {"enabled": True, "allow_global": False}},
        global_config={"evolution": {"enabled": False, "allow_global": True}},
    )
    assert load_evolution_config(config).enabled is True
    assert load_evolution_config(config).allow_global is False
```

- [ ] 1.2 Run the tests and confirm collection fails because `rook_agent.evolution` does not exist.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_config.py tests/test_evolution_events.py
```

Expected: failing collection or imports for the new evolution modules.

- [ ] 1.3 Implement the enums/dataclasses, nested configuration accessor, and `load_evolution_config` with explicit `ValueError` messages.

- [ ] 1.4 Add the only allowed audit-event helper. This prevents arbitrary event names and strips payload values that are not identifiers, counts, booleans, hashes, scopes, or stable reason codes.

```python
FORGE_EVENT_TYPES = frozenset({
    "forge_trace_eligible",
    "forge_trace_skipped",
    "skill_delta_proposed",
    "skill_delta_rejected",
    "skill_created",
    "skill_updated",
    "skill_duplicate_skipped",
    "skill_use_outcome",
    "forge_failed",
})

def append_forge_event(writer: SessionEventWriter, event_type: str, **payload: object) -> str:
    if event_type not in FORGE_EVENT_TYPES:
        raise ValueError(f"unsupported forge event: {event_type}")
    event_id = new_event_id()
    writer.store.append_event(SessionEvent(
        id=event_id,
        session_id=writer.session_id,
        type=event_type,
        payload=dict(payload),
    ))
    return event_id
```

The event tests must assert an unsupported type is rejected and a valid event round-trips through `JsonlSessionStore.list_events()`.

- [ ] 1.5 Run focused tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_config.py tests/test_evolution_events.py tests/test_config.py tests/test_context_writer.py
```

Expected: all selected tests pass.

- [ ] 1.6 Commit the protocol layer.

```powershell
git add rook_agent/evolution rook_agent/config/settings.py tests/test_evolution_config.py tests/test_evolution_events.py
git commit -m "feat: define Rook Forge protocol"
```

---

### Task 2: Build stable task traces and classify execution evidence

**Files:**

- Create: `rook_agent/evolution/trace.py`
- Create: `rook_agent/evolution/evidence.py`
- Modify: `rook_agent/agent/verification.py`
- Test: `tests/test_evolution_trace.py`
- Test: `tests/test_evolution_evidence.py`
- Modify: `tests/test_agent_verification.py`

**Interfaces and invariants:**

```python
@dataclass(frozen=True, slots=True)
class TraceBatch:
    completed: tuple[TaskTrace, ...]
    current: TaskTrace | None

class TaskTraceBuilder:
    def build(self, events: list[SessionEvent], *, close_current: bool = False) -> TraceBatch: ...

class EvidenceClassifier:
    def evaluate(self, trace: TaskTrace, *, allow_soft_completion: bool = False) -> EligibilityDecision: ...
```

`TaskTraceBuilder` must use the first user message referenced by a confirmed boundary's `candidate_basis_message_id` as the start of the new segment. That message is excluded from the old segment even when stable-window confirmation was recorded several events later. Initial-task observations (`confirmation_reason` equal to `initial_task` or `implicit_initial_task`) do not create an empty preceding segment.

Generate the id without model input:

```python
segment_id = stable_json_hash(
    {
        "session_id": session_id,
        "first_event_id": segment_events[0].id,
        "last_event_id": segment_events[-1].id,
    },
    length=32,
)
```

Map evidence sources deterministically:

```python
LOCAL_EXECUTION_TOOLS = frozenset({"shell", "diagnostics", "python_exec"})
WORKSPACE_STATE_TOOLS = frozenset({
    "write", "edit", "apply_patch", "delete", "git_diff", "git_status",
    "view", "grep", "glob", "tree", "read_multi", "ls",
})
EXTERNAL_TOOLS = frozenset({"fetch", "web_search"})
CONTROL_TOOLS = frozenset({"ask_user", "task_boundary", "think", "todo"})
```

Extend `is_verification_command()` with exact, non-compound commands for:

- `ruff check`, `mypy`, `pyright`;
- `npm run build`, `npm run lint`, `npm run typecheck`;
- `pnpm build|lint|typecheck`, `yarn build|lint|typecheck`;
- `cargo build|check|clippy`, `go build`, while preserving existing test commands.

**Steps:**

- [ ] 2.1 Add event factories in the tests and encode these trace cases: one task, delayed boundary confirmation, initial boundary, multiple boundaries, and `close_current=True`.

```python
def test_confirmed_boundary_splits_at_candidate_basis_message() -> None:
    events = [
        user_event("u1", "fix parser"),
        tool_event("t1", "shell", ok=True, command="pytest -q"),
        user_event("u2", "configure cmd"),
        boundary_event("b1", candidate_basis_message_id="u2", confirmed_change=False),
        boundary_event("b2", candidate_basis_message_id="u2", confirmed_change=True),
        assistant_event("a2", "done"),
    ]
    batch = TaskTraceBuilder().build(events)
    assert batch.completed[0].user_goal == "fix parser"
    assert batch.current is not None
    assert batch.current.user_goal == "configure cmd"
    assert "b1" not in batch.completed[0].event_ids
```

- [ ] 2.2 Run trace tests and confirm they fail before implementation.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_trace.py
```

Expected: new trace tests fail.

- [ ] 2.3 Implement message/part extraction directly from `SessionEvent.payload`, preserving event and part ids. Read raw event payloads rather than rebuilt compacted views so evidence remains traceable.

- [ ] 2.4 Add eligibility tests for successful verification, deterministic post-write state proof, recovered failure, pure failure, pending Todo items, cancellation/limit endings, control-only traces, and soft completion.

The latest successful `todo` result is authoritative; any item with `pending` or `in_progress` makes the trace ineligible.

```python
def test_failure_then_verification_is_recovered_failure() -> None:
    trace = trace_with_results(
        shell_result(ok=False, command="pytest -q", exit_code=1),
        shell_result(ok=True, command="pytest -q", exit_code=0),
    )
    decision = EvidenceClassifier().evaluate(trace)
    assert decision == EligibilityDecision(
        eligible=True,
        outcome=TraceOutcome.RECOVERED_FAILURE,
        reason_code="recovered_and_verified",
    )
```

- [ ] 2.5 Implement the classifier with this precedence: cancelled/limited, unfinished Todo, no informative result, successful verifier, mutation plus later deterministic state read, soft completion, pure failure, unknown. Soft completion is accepted only when `allow_soft_completion=True` and always receives `COMPLETED_WITHOUT_VERIFIER`.

- [ ] 2.6 Add and implement the verification-command tests, including rejection of `pytest && malicious`, `npm run build; echo x`, and malformed quoting.

- [ ] 2.7 Run focused regression tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_trace.py tests/test_evolution_evidence.py tests/test_agent_verification.py tests/test_context_task_boundary.py
```

Expected: all selected tests pass.

- [ ] 2.8 Commit trace and evidence handling.

```powershell
git add rook_agent/evolution/trace.py rook_agent/evolution/evidence.py rook_agent/evolution/models.py rook_agent/agent/verification.py tests/test_evolution_trace.py tests/test_evolution_evidence.py tests/test_agent_verification.py
git commit -m "feat: derive Forge traces from execution evidence"
```

---

### Task 3: Enforce schema, evidence, secret, volatility, injection, and scope gates

**Files:**

- Create: `rook_agent/evolution/gate.py`
- Test: `tests/test_evolution_gate.py`

**Interface:**

```python
class SkillGate:
    def evaluate(
        self,
        delta: SkillDelta,
        trace: TaskTrace,
        *,
        project_root: Path,
        configured_scope: EvolutionScope,
        allow_global: bool,
    ) -> GateDecision: ...

def redact_sensitive_text(value: str) -> str: ...
```

Run gates in this exact order and stop on the first rejection: schema, evidence, secret, volatility, injection, scope. Novelty is deterministic curation in Task 5; the coordinator combines both decisions into `accept_create`, `accept_update`, or `skip_duplicate` behavior.

Stable reason codes:

```python
SCHEMA_INVALID = "schema_invalid"
EVIDENCE_REF_MISSING = "evidence_ref_missing"
EVIDENCE_REF_OUTSIDE_SEGMENT = "evidence_ref_outside_segment"
EXECUTABLE_STEP_UNGROUNDED = "executable_step_ungrounded"
SECRET_DETECTED = "secret_detected"
VOLATILE_CONTENT = "volatile_content"
INJECTION_ONLY_EVIDENCE = "injection_only_evidence"
LOW_CONFIDENCE = "low_confidence"
WRITE_NOT_REQUESTED = "write_not_requested"
GLOBAL_DISABLED = "global_disabled"
PROJECT_SPECIFIC = "project_specific"
ACCEPTED = "accepted"
```

Schema rules are deterministic: title and description non-empty; 2-8 concrete triggers; 2-10 procedure steps; confidence only `low|medium|high`; low confidence and `should_write=False` reject; maximum field lengths are fixed constants; a trigger made only of broad words such as `代码`, `问题`, `项目`, `code`, `issue`, or `project` rejects.

Secret detection must cover:

- provider keys such as `sk-` followed by a credential-shaped body;
- assignments to names containing `API_KEY`, `TOKEN`, `PASSWORD`, `SECRET`, `COOKIE`;
- `Bearer` credentials and PEM private-key headers;
- high-entropy strings only when paired with credential context, to avoid rejecting normal content hashes.

`redact_sensitive_text()` returns `[REDACTED]` in place of the match and is used before provider distillation as well as before error-event construction.

**Steps:**

- [ ] 3.1 Write table-driven schema, evidence ownership, and source-trust tests.

- [ ] 3.2 Write secret tests using synthetic credentials only. Assert `SECRET_DETECTED` is returned and the original synthetic secret is absent from the serialized decision and captured events.

- [ ] 3.3 Write volatility tests for temp directories, localhost ephemeral ports, timestamps, session ids, and one-time request ids. Stable commands such as `pytest -q` and `cmd /d /c cd /d D:\work` remain allowed when grounded.

- [ ] 3.4 Write injection tests where external/workspace text says to ignore prior instructions or save itself to memory. It must reject when that text is the sole support, but allow a separately executed and verified command that happens to solve the same problem.

- [ ] 3.5 Write scope tests. Global requests downgrade to project when content includes the current repository name, project-relative paths, package-private module names, or project-only commands. `allow_global=False` also downgrades rather than rejects.

```python
def test_project_specific_global_delta_is_downgraded(tmp_path: Path) -> None:
    decision = SkillGate().evaluate(
        delta_for("Run .\\.venv\\Scripts\\python.exe in Rook"),
        verified_trace(),
        project_root=tmp_path / "Rook",
        configured_scope=EvolutionScope.AUTO,
        allow_global=True,
    )
    assert decision.status is GateStatus.DOWNGRADE_TO_PROJECT
    assert decision.scope is EvolutionScope.PROJECT
    assert decision.reason_code == PROJECT_SPECIFIC
```

- [ ] 3.6 Run gate tests and confirm failure before implementation.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_gate.py
```

- [ ] 3.7 Implement the ordered gates and redact helper without emitting matched source text in exceptions.

- [ ] 3.8 Run the security-focused suite.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_gate.py tests/test_session_redaction.py tests/test_permissions_policy.py
```

Expected: all selected tests pass.

- [ ] 3.9 Commit governance gates.

```powershell
git add rook_agent/evolution/gate.py tests/test_evolution_gate.py
git commit -m "feat: govern Forge skill writes"
```

---

### Task 4: Add generated-Skill discovery and an atomic versioned store

**Files:**

- Modify: `rook_agent/skills/models.py`
- Modify: `rook_agent/skills/discovery.py`
- Create: `rook_agent/evolution/store.py`
- Modify: `tests/test_skill_discovery.py`
- Test: `tests/test_evolution_store.py`

**Skill sources and order:**

```python
class SkillSource(StrEnum):
    PROJECT_MARKDOWN = "project_markdown"
    PROJECT_AGENT_SKILL = "project_agent_skill"
    PROJECT_GENERATED = "project_generated"
    GLOBAL_MARKDOWN = "global_markdown"
    GLOBAL_AGENT_SKILL = "global_agent_skill"
    GLOBAL_GENERATED = "global_generated"
```

Update `SkillDefinition.scope` so all three project sources map to `project`. Discover project-generated skills from `<project>/.rook/skills/*/SKILL.md`. Discover `~/.rook/skills/*/SKILL.md` as `GLOBAL_GENERATED`, not `GLOBAL_AGENT_SKILL`. Existing `ROOK_DISABLE_GLOBAL_SKILLS` behavior still suppresses all global sources.

**Store interface:**

```python
class SkillStoreError(RuntimeError):
    reason_code: str

class SkillStore:
    def __init__(self, *, project_root: Path, home: Path | None = None, lock_timeout: float = 2.0): ...
    def write(self, plan: CurationPlan, *, evidence_refs: tuple[EvidenceRef, ...]) -> StoredSkill: ...
    def record_use(self, stored: StoredSkill, *, outcome: TraceOutcome, segment_id: str) -> None: ...
    def read_meta(self, skill: SkillDefinition) -> dict[str, object]: ...
```

Store layout is fixed:

```text
<project>/.rook/skills/<slug>/SKILL.md
<project>/.rook/skills/<slug>/meta.json
<project>/.rook/skill-history/<slug>/<version>.md
~/.rook/skills/<slug>/SKILL.md
~/.rook/skills/<slug>/meta.json
~/.rook/skill-history/<slug>/<version>.md
```

`meta.json` schema:

```json
{
  "schema_version": 1,
  "rook_generated": true,
  "name": "cmd-directory-switching",
  "scope": "global",
  "version": 2,
  "content_hash": "sha256...",
  "previous_content_hash": "sha256...",
  "evidence_refs": [],
  "uses": 0,
  "verified_successes": 0,
  "failures": 0,
  "updated_at": "ISO-8601"
}
```

Acquire `<slug>/.rook.lock` using exclusive creation (`os.open(..., os.O_CREAT | os.O_EXCL | os.O_WRONLY)`). Record pid and creation time, recover only locks older than the bounded stale threshold, and time out with `reason_code="lock_timeout"`. Before replacement, compare `base_content_hash` with current `SKILL.md`; mismatch raises `content_conflict`.

Write both new files to same-directory temporary files, fsync them, save the old Markdown to history, then replace `SKILL.md` and `meta.json`. If the second replace fails, restore both prior byte sequences. Never update a file whose frontmatter or metadata does not contain `rook_generated: true`.

**Steps:**

- [ ] 4.1 Extend discovery tests for generated project/global roots, source values, disabled globals, and handwritten/generated name collisions.

- [ ] 4.2 Run discovery tests and see the new assertions fail.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_skill_discovery.py
```

- [ ] 4.3 Implement new source values and root-specific discovery without changing the existing file loader format.

- [ ] 4.4 Write store tests for create, update, history version, Markdown frontmatter, path slug safety, non-generated collision, content conflict, lock timeout/stale recovery, and rollback after an injected `os.replace` failure.

```python
def test_update_writes_history_and_increments_version(tmp_path: Path) -> None:
    store = SkillStore(project_root=tmp_path, home=tmp_path / "home")
    first = store.write(create_plan(version=1), evidence_refs=(evidence_ref("e1"),))
    second = store.write(update_plan(first.content_hash, version=2), evidence_refs=(evidence_ref("e2"),))
    assert second.version == 2
    assert (tmp_path / ".rook/skill-history/example/1.md").is_file()
```

- [ ] 4.5 Implement safe slugging, Markdown rendering, metadata serialization, per-Skill locking, optimistic hash checks, history, atomic replacement, and rollback.

- [ ] 4.6 Run store/discovery regression tests on Windows paths.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_store.py tests/test_skill_discovery.py tests/test_skill_loader.py
```

Expected: all selected tests pass.

- [ ] 4.7 Commit storage and discovery.

```powershell
git add rook_agent/skills/models.py rook_agent/skills/discovery.py rook_agent/evolution/store.py tests/test_skill_discovery.py tests/test_evolution_store.py
git commit -m "feat: store versioned generated skills"
```

---

### Task 5: Implement deterministic novelty matching and section-level curation

**Files:**

- Create: `rook_agent/skills/text.py`
- Modify: `rook_agent/skills/router.py`
- Create: `rook_agent/evolution/curator.py`
- Test: `tests/test_evolution_curator.py`
- Modify: `tests/test_skill_router.py`

**Interfaces:**

```python
def normalize_text(value: str) -> str: ...
def text_tokens(value: str) -> tuple[str, ...]: ...
def overlap_score(left: str, right: str) -> int: ...

@dataclass(frozen=True, slots=True)
class SkillMatch:
    skill: SkillDefinition
    score: int
    content_hash: str
    generated: bool

class SkillMatcher:
    def find(self, delta: SkillDelta, catalog: SkillCatalog) -> SkillMatch | None: ...

class SkillCurator:
    def plan(self, decision: GateDecision, catalog: SkillCatalog) -> CurationPlan: ...
```

Move the router's current normalization, alias expansion, Chinese 2/3-grams, and overlap calculation into `rook_agent.skills.text`; preserve existing results before adding new weighting.

Matcher score:

```text
exact normalized name: +8
each trigger overlap: +3
description token overlap: +1 per token, capped at 4
procedure tool/error-signature overlap: +2 per signature, capped at 6
same scope: +1
```

A score below 6 means create. A score of 6 or more against a generated Skill means update only if at least one normalized procedure, verification, or pitfall entry is new. A score of 6 or more against a handwritten Skill means skip with `reason_code="handwritten_duplicate"`; never mutate handwritten content.

Section-level merge is programmatic:

```python
def merge_unique(existing: tuple[str, ...], incoming: tuple[str, ...]) -> tuple[str, ...]:
    seen = {normalize_text(item).strip(" .;:") for item in existing}
    additions = tuple(
        item for item in incoming
        if normalize_text(item).strip(" .;:") not in seen
    )
    return existing + additions
```

Title, description, name, and old entries are preserved on update. Only `Procedure`, `Verification`, and `Pitfalls` gain evidence-backed entries. Version increments only when rendered content changes.

**Steps:**

- [ ] 5.1 Add router characterization tests before extracting text helpers. They must freeze current English token, Chinese n-gram, alias, explicit-name, and ambiguity behavior.

- [ ] 5.2 Extract helpers and rerun router tests unchanged.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_skill_router.py
```

- [ ] 5.3 Write curator tests for create, generated update, duplicate skip, handwritten skip, scope-sensitive matching, and preservation of old sections.

- [ ] 5.4 Run the curator tests and confirm failure.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_curator.py
```

- [ ] 5.5 Implement matcher scoring, generated-file parsing, and deterministic section merging. A malformed existing generated file yields `SKIP` with `existing_skill_invalid`; it is not overwritten.

- [ ] 5.6 Run focused tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_curator.py tests/test_skill_router.py tests/test_skill_loader.py
```

Expected: all selected tests pass.

- [ ] 5.7 Commit novelty and curation.

```powershell
git add rook_agent/skills/text.py rook_agent/skills/router.py rook_agent/evolution/curator.py tests/test_evolution_curator.py tests/test_skill_router.py
git commit -m "feat: curate incremental Forge skills"
```

---

### Task 6: Distill strict, evidence-bound SkillDelta JSON with the active provider

**Files:**

- Create: `rook_agent/evolution/distiller.py`
- Test: `tests/test_evolution_distiller.py`

**Interface:**

```python
class DistillationError(RuntimeError):
    reason_code: str

class ExperienceDistiller:
    def __init__(self, provider: ChatProvider) -> None: ...
    def set_provider(self, provider: ChatProvider) -> None: ...
    def distill(self, trace: TaskTrace, *, max_skills: int) -> tuple[SkillDelta, ...]: ...
```

The request must use the current provider with no tools:

```python
request = ChatRequest(
    messages=[
        ChatMessage(role="system", content=DISTILLER_SYSTEM_PROMPT),
        ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    ],
    tools=[],
    tool_choice="none",
    temperature=0,
    max_tokens=1800,
)
```

The accepted top-level shape is exactly `{"skills": [...]}`. The parser rejects unknown top-level fields, more than `max_skills`, non-list sections, invented evidence ids, and non-string elements. The model emits refs as `event_id:part_id`; the parser resolves them only through a lookup built from `trace.evidence`, creating full `EvidenceRef` objects itself.

Before request construction:

- pass all user/final/evidence text through `redact_sensitive_text()`;
- omit system prompts, environment snapshots, full `.env` content, unrelated events, and archive bodies;
- cap each evidence text excerpt and total payload characters;
- include tool name, `ok`, bounded structured `data`, evidence ref, and source label.

If the first response is invalid JSON/schema, make one correction request containing only the validation error and the invalid response truncated to a safe bound. If the second response fails, raise `DistillationError("invalid_json")`. Provider errors become `DistillationError("provider_error")` without embedding provider text that may contain secrets.

**Steps:**

- [ ] 6.1 Create a recording fake provider and tests for request flags, valid parsing, two Deltas, `should_write=false`, invented refs, secret redaction, over-limit output, one successful correction, and two failed parses.

```python
def test_distiller_uses_no_tools_and_resolves_real_refs() -> None:
    provider = RecordingProvider([valid_delta_json(ref="event-1:part-1")])
    deltas = ExperienceDistiller(provider).distill(trace_with_ref("event-1", "part-1"), max_skills=2)
    assert provider.requests[0].tools == []
    assert provider.requests[0].tool_choice == "none"
    assert deltas[0].evidence_refs[0].event_id == "event-1"
```

- [ ] 6.2 Run tests and confirm failure before implementation.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_distiller.py
```

- [ ] 6.3 Implement bounded trace serialization, strict decoding, evidence resolution, one retry, and provider replacement.

- [ ] 6.4 Run distiller and provider regression tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_distiller.py tests/test_providers.py tests/test_provider_errors.py
```

Expected: all selected tests pass.

- [ ] 6.5 Commit distillation.

```powershell
git add rook_agent/evolution/distiller.py tests/test_evolution_distiller.py
git commit -m "feat: distill evidence-bound skill deltas"
```

---

### Task 7: Coordinate idempotent Forge execution and application lifecycle hooks

**Files:**

- Create: `rook_agent/evolution/coordinator.py`
- Modify: `rook_agent/agent/loop.py`
- Modify: `rook_agent/app/runtime.py`
- Modify: `rook_agent/app/factory.py`
- Modify: `rook_agent/app/tui.py`
- Test: `tests/test_evolution_coordinator.py`
- Modify: `tests/test_agent_context_loop.py`
- Modify: `tests/test_app_runtime.py`
- Modify: `tests/test_app_factory.py`
- Modify: `tests/test_app_tui.py`

**Interfaces:**

```python
class ForgeCoordinator:
    def on_turn_completed(self, session: AgentSession) -> tuple[ForgeResult, ...]: ...
    def flush(self, session: AgentSession) -> tuple[ForgeResult, ...]: ...
    def set_provider(self, provider: ChatProvider) -> None: ...

class ForgeLifecycle(Protocol):
    def on_turn_completed(self, session: AgentSession) -> tuple[ForgeResult, ...]: ...
```

Coordinator pipeline per segment:

```text
events -> TaskTraceBuilder -> EvidenceClassifier -> forge_trace_eligible/skipped
       -> ExperienceDistiller -> skill_delta_proposed
       -> SkillGate -> skill_delta_rejected or SkillCurator
       -> create/update/duplicate event -> SkillStore
       -> refresh session.skill_catalog and clear session.prompt_cache
```

Idempotency uses terminal events already in the session log. A segment is terminal when a `skill_created`, `skill_updated`, `skill_duplicate_skipped`, `skill_delta_rejected`, or completed `forge_trace_skipped` event contains its `segment_id`. Repeated calls return `already_processed` without another provider call. A current segment that is not yet eligible is not terminal; it may become verified during a later turn.

`on_turn_completed()` processes all newly completed boundary segments with soft completion allowed, plus the current segment only when it has strong verification/state proof. `flush()` also closes and soft-evaluates the current segment. Waiting for input, cancelled, interrupted, or tool-limit responses remain ineligible.

Wrap the whole pipeline:

```python
try:
    return self._process(session, close_current=False)
except Exception as error:
    append_forge_event(
        session.writer,
        "forge_failed",
        reason_code=self._reason_code(error),
        segment_id=self._safe_segment_id(error),
    )
    return ()
```

Do not put `str(error)` in the event.

**Lifecycle wiring:**

- Add optional `forge_coordinator: ForgeLifecycle | None = None` to `AgentLoop`.
- Immediately after the final assistant response is appended and compaction has completed, call a private best-effort notifier. Do this for normal, interrupted, limit, sync, and streaming final-response paths; eligibility determines whether learning occurs.
- Add optional `forge_coordinator` to `AgentChatRunner`; pass it into every `AgentLoop` construction.
- `AgentChatRunner.set_provider()` calls `forge_coordinator.set_provider()` so `/model` changes affect both main reasoning and distillation.
- Add `AgentChatRunner.flush_current_session()`; the session replacement callback flushes the old session before `/new`, `/fork`, or `/resume` installs the new one.
- `RookApp.on_unmount()` calls `flush_current_session()` best-effort after stopping UI animations.
- `create_rook_app()` constructs the coordinator only when `load_evolution_config(...).enabled` is true. Disabled wiring passes `None`.

**Steps:**

- [ ] 7.1 Write coordinator unit tests using fake trace/distiller/gate/curator/store components. Cover create, update, skip, rejection, no-delta, idempotent repeat, refreshed catalog, prompt-cache clearing, and each failure stage.

- [ ] 7.2 Run coordinator tests and confirm failure.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_coordinator.py
```

- [ ] 7.3 Implement coordinator orchestration and redacted events.

- [ ] 7.4 Add AgentLoop tests proving the notifier runs only after the final assistant event exists and that notifier exceptions do not alter `ChatResponse`.

- [ ] 7.5 Add factory/runtime/TUI tests proving disabled mode creates no coordinator and no extra provider calls; enabled mode uses the same provider; model switching updates it; session replacement and normal unmount flush exactly once.

```python
def test_disabled_forge_does_not_add_provider_calls(tmp_path: Path) -> None:
    provider = FakeProvider()
    app = create_rook_app(project_root=tmp_path, provider=provider, app_config=disabled_config())
    app.chat_runner.run_user_turn("hello")
    assert provider.call_count == 1
```

- [ ] 7.6 Implement lifecycle wiring with `None` defaults to preserve all direct test constructors.

- [ ] 7.7 Run lifecycle regression tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_coordinator.py tests/test_agent_context_loop.py tests/test_app_runtime.py tests/test_app_factory.py tests/test_app_session_commands.py tests/test_app_tui.py
```

Expected: all new assertions pass. If the two known parallel timing tests remain flaky, record their names and confirm the Forge-related tests pass separately.

- [ ] 7.8 Commit lifecycle integration.

```powershell
git add rook_agent/evolution/coordinator.py rook_agent/agent/loop.py rook_agent/app/runtime.py rook_agent/app/factory.py rook_agent/app/tui.py tests/test_evolution_coordinator.py tests/test_agent_context_loop.py tests/test_app_runtime.py tests/test_app_factory.py tests/test_app_tui.py
git commit -m "feat: integrate Rook Forge lifecycle"
```

---

### Task 8: Route generated Skills conservatively and record reuse outcomes

**Files:**

- Modify: `rook_agent/skills/models.py`
- Modify: `rook_agent/skills/router.py`
- Modify: `rook_agent/skills/session.py`
- Create: `rook_agent/evolution/metrics.py`
- Modify: `rook_agent/evolution/coordinator.py`
- Modify: `tests/test_skill_router.py`
- Test: `tests/test_evolution_metrics.py`
- Modify: `tests/test_agent_skill_flow.py`

**Router contract:**

Add diagnostic fields without breaking existing constructors:

```python
@dataclass(frozen=True, slots=True)
class SkillRoutingDecision:
    selected: SkillDefinition | None
    candidates: list[SkillDefinition] = field(default_factory=list)
    reason: str = "none"
    confidence: str = "none"
    score: int = 0
    margin: int = 0
```

Source preference:

```python
SOURCE_PRIORITY = {
    SkillSource.PROJECT_AGENT_SKILL: 0,
    SkillSource.PROJECT_MARKDOWN: 1,
    SkillSource.PROJECT_GENERATED: 2,
    SkillSource.GLOBAL_AGENT_SKILL: 3,
    SkillSource.GLOBAL_MARKDOWN: 4,
    SkillSource.GLOBAL_GENERATED: 5,
}
```

Explicit name/path and `AGENTS.md` routes keep their current high-confidence behavior. Metadata-selected generated Skills require `score >= 2` and `score - second_score >= 1`; otherwise return no selected Skill with `reason="generated_below_threshold"` or `reason="generated_ambiguous"`. A handwritten top candidate wins ties by source preference.

**Outcome metrics:**

```python
GENERATED_SOURCES = frozenset({SkillSource.PROJECT_GENERATED, SkillSource.GLOBAL_GENERATED})

def loaded_generated_skills(trace: TaskTrace, events: list[SessionEvent]) -> tuple[StoredSkill, ...]: ...
def outcome_for_trace(decision: EligibilityDecision) -> str: ...
```

At terminal processing, correlate `skill_loaded` events inside the segment with generated sources and append one `skill_use_outcome` per unique `(segment_id, skill_path, content_hash)`. Update meta counters under the same per-Skill lock:

- every correlated terminal outcome increments `uses` once;
- `VERIFIED_SUCCESS`, `RECOVERED_FAILURE`, and `STATE_VERIFIED_SUCCESS` increment `verified_successes`;
- `FAILED` increments `failures`;
- soft completion, cancellation, and unknown only affect `uses`.

These are correlation statistics. They never disable, delete, or rewrite the Skill.

**Steps:**

- [ ] 8.1 Add router tests for the six-level priority, generated minimum score/margin, handwritten tie wins, explicit generated route, and unchanged ambiguity behavior for existing handwritten Skills.

- [ ] 8.2 Implement ranked metadata scoring and diagnostic fields.

- [ ] 8.3 Add outcome tests for unique counting, duplicate coordinator calls, success/failure counters, old Skill versions, and missing metadata.

- [ ] 8.4 Implement event correlation and locked `record_use()` updates. Missing or corrupt meta produces a redacted `forge_failed` reason and does not affect the user response.

- [ ] 8.5 Run routing and feedback tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_skill_router.py tests/test_agent_skill_flow.py tests/test_evolution_metrics.py tests/test_evolution_store.py
```

Expected: all selected tests pass.

- [ ] 8.6 Commit conservative reuse and feedback.

```powershell
git add rook_agent/skills/models.py rook_agent/skills/router.py rook_agent/skills/session.py rook_agent/evolution/metrics.py rook_agent/evolution/coordinator.py tests/test_skill_router.py tests/test_agent_skill_flow.py tests/test_evolution_metrics.py tests/test_evolution_store.py
git commit -m "feat: route and measure generated skills"
```

---

### Task 9: Prove the end-to-end loop and add reproducible A/B metrics

**Files:**

- Test: `tests/test_evolution_integration.py`
- Create: `rook_agent/eval/forge_metrics.py`
- Test: `tests/test_eval_forge_metrics.py`
- Create: `docs/ROOK_FORGE.md`
- Modify: `README.md`

**Evaluation interface:**

```python
@dataclass(frozen=True, slots=True)
class ForgeRunMetrics:
    completed_traces: int
    eligible_traces: int
    proposed_deltas: int
    created: int
    updated: int
    duplicate_skips: int
    rejected: int
    generated_routes: int
    ambiguous_routes: int
    verified_successes: int
    tool_calls: int
    provider_calls: int
    input_tokens: int
    output_tokens: int
    elapsed_ms: int

def collect_forge_metrics(path: Path) -> ForgeRunMetrics: ...
def compare_forge_runs(baseline: ForgeRunMetrics, learned: ForgeRunMetrics) -> dict[str, int | float | None]: ...
```

Expose a module entry point:

```powershell
& '.\.venv\Scripts\python.exe' -m rook_agent.eval.forge_metrics --baseline path\baseline.jsonl --learned path\learned.jsonl
```

The output is deterministic JSON. Rates with a zero denominator are `null`, never fabricated. Provider/token/latency metrics are summed only from fields that actually exist in events; the report includes an `observed_fields` list so missing telemetry is explicit.

**End-to-end scenario:**

Use a scripted fake provider and real temporary `JsonlSessionStore`, `AgentSession`, router, coordinator, gate, curator, and store:

1. The agent executes a failing verification command.
2. It applies a repair and executes a successful verification command.
3. Distillation returns one evidence-bound project Skill.
4. Forge writes `.rook/skills/<slug>/SKILL.md` plus metadata.
5. A new session discovers the generated Skill.
6. A semantically similar prompt routes and loads it.
7. Terminal processing appends `skill_use_outcome` and updates counters.

Add a second integration case with external content containing a memory-injection instruction. The candidate must be rejected and the malicious phrase must be absent from the Skill tree and session audit events.

**Steps:**

- [ ] 9.1 Write the end-to-end tests and confirm they fail before the final wiring corrections.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_integration.py
```

- [ ] 9.2 Make only the integration corrections required by the failing tests; keep subsystem behavior inside the modules introduced by earlier tasks.

- [ ] 9.3 Write synthetic transcript tests for metric collection, missing telemetry, zero denominators, and A/B deltas.

```python
def test_compare_reports_measured_tool_call_reduction() -> None:
    result = compare_forge_runs(metrics(tool_calls=10), metrics(tool_calls=6))
    assert result["tool_call_delta"] == -4
    assert result["tool_call_reduction_rate"] == 0.4
```

- [ ] 9.4 Implement `forge_metrics.py`, including `argparse` entry point and sorted JSON output.

- [ ] 9.5 Document the feature accurately in `docs/ROOK_FORGE.md`: architecture, enablement, project/global storage, security gates, events, metric command, limitations, and resume-safe wording. Add a short README link and configuration sample.

The documentation must call it “execution-grounded Skill evolution” or “external procedural memory”; it must not claim model self-training or measured percentage improvement until a real A/B run exists.

- [ ] 9.6 Run integration, metric, README, and brand tests.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_integration.py tests/test_eval_forge_metrics.py tests/test_readme_provider_docs.py tests/test_brand_contract.py
```

Expected: all selected tests pass.

- [ ] 9.7 Commit the demonstrable vertical slice.

```powershell
git add tests/test_evolution_integration.py rook_agent/eval/forge_metrics.py tests/test_eval_forge_metrics.py docs/ROOK_FORGE.md README.md
git commit -m "feat: evaluate Rook Forge end to end"
```

---

### Task 10: Run final security, regression, and baseline verification

**Files:**

- Modify only if a verification failure identifies a regression in files already listed above.

**Steps:**

- [ ] 10.1 Run all Forge and directly affected tests in one fresh process.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evolution_config.py tests/test_evolution_events.py tests/test_evolution_trace.py tests/test_evolution_evidence.py tests/test_evolution_gate.py tests/test_evolution_store.py tests/test_evolution_curator.py tests/test_evolution_distiller.py tests/test_evolution_coordinator.py tests/test_evolution_metrics.py tests/test_evolution_integration.py tests/test_eval_forge_metrics.py tests/test_agent_verification.py tests/test_skill_discovery.py tests/test_skill_loader.py tests/test_skill_router.py tests/test_agent_skill_flow.py tests/test_app_runtime.py tests/test_app_factory.py tests/test_app_session_commands.py tests/test_app_tui.py
```

Expected: all selected tests pass. Resolve every failure before continuing.

- [ ] 10.2 Run formatting/static checks only if they are configured in the repository; do not introduce a new formatter configuration during this feature.

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] 10.3 Scan tracked source, tests, and documentation for accidental credential-shaped content. Review every match; synthetic security fixtures must use unmistakably fake values.

```powershell
rg -n --glob '!*.jsonl' --glob '!docs/superpowers/plans/2026-07-15-rook-forge.md' 'sk-[A-Za-z0-9_-]{16,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|Bearer [A-Za-z0-9._-]{16,}' rook_agent tests docs README.md
```

Expected: no real credentials and no generated Skill containing a fixture secret.

- [ ] 10.4 Run the core suite with EvalPlus excluded and compare to the recorded baseline.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q --ignore=tests/test_evalplus_benchmark.py
```

Acceptance: no new failing test names beyond the pre-implementation set, and all Forge/touched tests pass. Record the exact final pass/fail/skip counts in the implementation handoff.

- [ ] 10.5 Run the optional EvalPlus gate separately and report its environment status honestly.

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_evalplus_benchmark.py
```

Expected on the current environment: collection reports missing optional dependency `evalplus`. If the dependency is installed during implementation, run the test normally and record the result.

- [ ] 10.6 Review the commit series and working tree.

```powershell
git log --oneline -10
git status --short
```

Expected: task-scoped commits are present and the working tree is clean.

- [ ] 10.7 If verification required code corrections, commit them separately.

```powershell
git add rook_agent tests docs README.md
git commit -m "fix: harden Rook Forge verification"
```

Skip this commit when no corrective files exist.

---

## Specification Coverage Matrix

| Approved requirement | Implemented by |
|---|---|
| Stable event-range segments and delayed boundary handling | Task 2 |
| Verified success, state proof, and recovered failure | Task 2 |
| Strict SkillDelta with real evidence refs | Tasks 1 and 6 |
| Schema/evidence/secret/volatility/injection/scope gates | Task 3 |
| Project/global generated layout and handwritten protection | Task 4 |
| Create/update/skip with section-level deltas and versions | Tasks 4 and 5 |
| Same-provider lifecycle, default off, failure isolation | Tasks 6 and 7 |
| Generated routing priority and ambiguity margin | Task 8 |
| Reuse outcome counters without automatic deletion | Task 8 |
| Full vertical integration and malicious-content case | Task 9 |
| Reproducible A/B metrics and honest documentation | Task 9 |
| Regression, security, and optional dependency verification | Task 10 |

## Completion Definition

Rook Forge is complete only when all focused tests pass, the disabled path produces zero additional provider calls, generated files survive atomic-failure tests, a recovered execution trace produces a discoverable Skill in the end-to-end test, malicious external text cannot enter persisted Skills, and the core-suite result introduces no new failures relative to the recorded baseline.
