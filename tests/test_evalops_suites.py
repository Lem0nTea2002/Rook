from collections.abc import Callable
from pathlib import Path

import pytest

from rook_agent.evalops import CaseCategory, load_eval_suite
from rook_agent.evalops.models import NetworkPolicy


SUITE_TOML = """\
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
"""


POLICY_TOML = """\
version = "1"

[requirements]
min_valid_pairs = 1
max_safety_failures = 0
"""


def write_eval_tree(tmp_path: Path, *, manifest: str = SUITE_TOML) -> Path:
    suite_dir = tmp_path / "evals" / "suites" / "windows-shell"
    suite_dir.mkdir(parents=True)
    policy_dir = tmp_path / "evals" / "policies"
    policy_dir.mkdir(parents=True)
    (policy_dir / "default.toml").write_text(POLICY_TOML, encoding="utf-8")
    (suite_dir / "task.md").write_text("Fix the shell command.\n", encoding="utf-8")
    fixture = suite_dir / "fixture"
    fixture.mkdir()
    (fixture / "input.txt").write_text("before\n", encoding="utf-8")
    (suite_dir / "hidden_check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    manifest_path = suite_dir / "suite.toml"
    manifest_path.write_text(manifest, encoding="utf-8")
    return manifest_path


def test_load_eval_suite_builds_frozen_protocol_from_real_files(tmp_path: Path) -> None:
    manifest = write_eval_tree(tmp_path)

    suite = load_eval_suite(manifest)

    assert suite.id == "windows-shell"
    assert suite.version == "1"
    assert suite.manifest_path == manifest.resolve()
    assert len(suite.fingerprint) == 32
    assert suite.policy.version == "1"
    assert suite.policy.source == (tmp_path / "evals" / "policies" / "default.toml").resolve()
    assert len(suite.policy.fingerprint) == 32
    assert suite.policy.data["requirements"]["min_valid_pairs"] == 1  # type: ignore[index]
    assert suite.candidate_content_hash is None
    assert len(suite.cases) == 1
    case = suite.cases[0]
    assert case.id == "direct-01"
    assert case.category is CaseCategory.DIRECT
    assert case.task == "Fix the shell command.\n"
    assert case.fixture == (manifest.parent / "fixture").resolve()
    assert case.timeout_seconds == 180
    assert case.network_policy is NetworkPolicy.DISABLED
    assert case.evaluator.kind == "command"
    assert case.evaluator.options == {
        "command": ("python", str((manifest.parent / "hidden_check.py").resolve())),
        "timeout_seconds": 30,
    }


def test_load_eval_suite_accepts_candidate_content_lock(tmp_path: Path) -> None:
    expected_hash = "a" * 64
    manifest = write_eval_tree(
        tmp_path,
        manifest=SUITE_TOML.replace(
            'policy = "../../policies/default.toml"',
            'policy = "../../policies/default.toml"\n'
            f'candidate_content_hash = "{expected_hash}"',
        ),
    )

    suite = load_eval_suite(manifest)

    assert suite.candidate_content_hash == expected_hash


def test_load_eval_suite_rejects_invalid_candidate_content_lock(tmp_path: Path) -> None:
    manifest = write_eval_tree(
        tmp_path,
        manifest=SUITE_TOML.replace(
            'policy = "../../policies/default.toml"',
            'policy = "../../policies/default.toml"\n'
            'candidate_content_hash = "not-a-sha256"',
        ),
    )

    with pytest.raises(ValueError, match="candidate_content_hash.*SHA-256"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    text = SUITE_TOML.replace(
        'policy = "../../policies/default.toml"',
        'policy = "../../policies/default.toml"\nsurprise = "closed"',
    )
    manifest = write_eval_tree(tmp_path, manifest=text)

    with pytest.raises(ValueError, match="unknown fields.*surprise"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_unknown_case_field(tmp_path: Path) -> None:
    text = SUITE_TOML.replace('network = "disabled"', 'network = "disabled"\nsurprise = true')
    manifest = write_eval_tree(tmp_path, manifest=text)

    with pytest.raises(ValueError, match="unknown fields.*surprise"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    duplicate = SUITE_TOML + "\n[[cases]]" + SUITE_TOML.split("[[cases]]", 1)[1]
    manifest = write_eval_tree(tmp_path, manifest=duplicate)

    with pytest.raises(ValueError, match="duplicate case id.*direct-01"):
        load_eval_suite(manifest)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('category = "direct"', 'category = "unknown"', "invalid case category"),
        ('network = "disabled"', 'network = "internet"', "invalid network policy"),
    ],
)
def test_load_eval_suite_rejects_unsupported_enum_values(
    tmp_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    manifest = write_eval_tree(tmp_path, manifest=SUITE_TOML.replace(old, new))

    with pytest.raises(ValueError, match=message):
        load_eval_suite(manifest)


@pytest.mark.parametrize("missing", ["task.md", "fixture"])
def test_load_eval_suite_rejects_missing_task_or_fixture(tmp_path: Path, missing: str) -> None:
    manifest = write_eval_tree(tmp_path)
    path = manifest.parent / missing
    if path.is_dir():
        (path / "input.txt").unlink()
        path.rmdir()
    else:
        path.unlink()

    with pytest.raises(ValueError, match="does not exist"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_parent_escape(tmp_path: Path) -> None:
    manifest = write_eval_tree(
        tmp_path,
        manifest=SUITE_TOML.replace('task = "task.md"', 'task = "../outside.md"'),
    )
    (manifest.parent.parent / "outside.md").write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes suite root"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_absolute_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    text = SUITE_TOML.replace('task = "task.md"', f'task = "{outside.as_posix()}"')
    manifest = write_eval_tree(tmp_path, manifest=text)

    with pytest.raises(ValueError, match="escapes suite root"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_evaluator_path_escape(tmp_path: Path) -> None:
    text = SUITE_TOML.replace('"hidden_check.py"', '"../hidden_check.py"')
    manifest = write_eval_tree(tmp_path, manifest=text)
    (manifest.parent.parent / "hidden_check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes suite root"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_evaluator_file_inside_fixture(tmp_path: Path) -> None:
    text = SUITE_TOML.replace('"hidden_check.py"', '"fixture/hidden_check.py"')
    manifest = write_eval_tree(tmp_path, manifest=text)
    (manifest.parent / "fixture" / "hidden_check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    with pytest.raises(ValueError, match="evaluator path.*inside fixture"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_evaluator_symlink_into_fixture(tmp_path: Path) -> None:
    text = SUITE_TOML.replace('"hidden_check.py"', '"evaluators/hidden_check.py"')
    manifest = write_eval_tree(tmp_path, manifest=text)
    evaluators = manifest.parent / "evaluators"
    evaluators.mkdir()
    target = manifest.parent / "fixture" / "hidden_check.py"
    target.write_text("raise SystemExit(0)\n", encoding="utf-8")
    try:
        (evaluators / "hidden_check.py").symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(ValueError, match="evaluator path.*inside fixture"):
        load_eval_suite(manifest)


def test_load_eval_suite_accepts_evaluator_file_outside_fixture(tmp_path: Path) -> None:
    text = SUITE_TOML.replace('"hidden_check.py"', '"evaluators/hidden_check.py"')
    manifest = write_eval_tree(tmp_path, manifest=text)
    evaluators = manifest.parent / "evaluators"
    evaluators.mkdir()
    (evaluators / "hidden_check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    suite = load_eval_suite(manifest)

    assert suite.cases[0].evaluator.options["command"] == (
        "python",
        str((manifest.parent / "evaluators" / "hidden_check.py").resolve()),
    )


@pytest.mark.parametrize(
    ("evaluator", "message"),
    [
        (
            'kind = "future_kind"\ncommand = ["python", "hidden_check.py"]',
            "unsupported evaluator kind",
        ),
        (
            'kind = "command"\ncommand = ["python", "hidden_check.py"]\nsurprise = true',
            "unknown fields.*surprise",
        ),
        ('kind = "command"\ncommand = []', "non-empty string list"),
        ('kind = "command"\ncommand = ["python", 1]', "non-empty string list"),
    ],
    ids=["unknown-kind", "unknown-field", "empty-command", "non-string-command"],
)
def test_load_eval_suite_rejects_invalid_evaluator_schema(
    tmp_path: Path,
    evaluator: str,
    message: str,
) -> None:
    original = 'kind = "command"\ncommand = ["python", "hidden_check.py"]'
    manifest = write_eval_tree(tmp_path, manifest=SUITE_TOML.replace(original, evaluator))

    with pytest.raises(ValueError, match=message):
        load_eval_suite(manifest)


@pytest.mark.parametrize(
    ("evaluator", "kind", "expected"),
    [
        (
            'kind = "file_state"\nrequired_files = ["result.txt"]\n'
            'forbidden_files = ["secret.txt"]\n'
            'expected_text = { "result.txt" = "done\\n" }\n'
            f'expected_sha256 = {{ "result.txt" = "{"a" * 64}" }}',
            "file_state",
            {"required_files": ("result.txt",), "forbidden_files": ("secret.txt",)},
        ),
        (
            'kind = "trajectory"\nrequired_tools = ["shell"]\n'
            'forbidden_tools = ["network"]\nrequired_successful_tools = ["shell"]',
            "trajectory",
            {"required_tools": ("shell",), "forbidden_tools": ("network",)},
        ),
        (
            'kind = "llm_judge"\nrubric = "Answer is complete."\nmax_tokens = 128',
            "llm_judge",
            {"rubric": "Answer is complete.", "max_tokens": 128},
        ),
        (
            'kind = "composite"\nchildren = ['
            '{ kind = "file_state", required_files = ["result.txt"] }, '
            '{ kind = "trajectory", required_tools = ["shell"] }]',
            "composite",
            {},
        ),
    ],
)
def test_load_eval_suite_accepts_strict_evaluator_kinds(
    tmp_path: Path,
    evaluator: str,
    kind: str,
    expected: dict[str, object],
) -> None:
    original = 'kind = "command"\ncommand = ["python", "hidden_check.py"]'
    manifest = write_eval_tree(tmp_path, manifest=SUITE_TOML.replace(original, evaluator))

    loaded = load_eval_suite(manifest).cases[0].evaluator

    assert loaded.kind == kind
    for key, value in expected.items():
        assert loaded.options[key] == value
    if kind == "composite":
        children = loaded.options["children"]
        assert tuple(child.kind for child in children) == ("file_state", "trajectory")


@pytest.mark.parametrize(
    ("evaluator", "message"),
    [
        ('kind = "file_state"\nrequired_files = ["../secret.txt"]', "workspace path"),
        ('kind = "file_state"\nexpected_sha256 = { "result.txt" = "bad" }', "SHA-256"),
        ('kind = "trajectory"\nrequired_tools = [1]', "string list"),
        ('kind = "llm_judge"\nrubric = "ok"\nmax_tokens = 257', "max_tokens"),
        ('kind = "composite"\nchildren = []', "at least one child"),
        (
            'kind = "composite"\nchildren = ['
            '{ kind = "llm_judge", rubric = "ok" }, '
            '{ kind = "file_state", required_files = ["result.txt"] }]',
            "LLM judge.*last",
        ),
        (
            'kind = "composite"\nchildren = ['
            '{ kind = "composite", children = [{ kind = "trajectory" }] }]',
            "nested composite",
        ),
    ],
)
def test_load_eval_suite_rejects_invalid_known_evaluator_schema(
    tmp_path: Path, evaluator: str, message: str
) -> None:
    original = 'kind = "command"\ncommand = ["python", "hidden_check.py"]'
    manifest = write_eval_tree(tmp_path, manifest=SUITE_TOML.replace(original, evaluator))

    with pytest.raises(ValueError, match=message):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_policy_outside_evals_policy_root(tmp_path: Path) -> None:
    text = SUITE_TOML.replace("../../policies/default.toml", "../../../outside-policy.toml")
    manifest = write_eval_tree(tmp_path, manifest=text)
    (tmp_path / "outside-policy.toml").write_text(POLICY_TOML, encoding="utf-8")

    with pytest.raises(ValueError, match="escapes policy root"):
        load_eval_suite(manifest)


def test_load_eval_suite_rejects_symlinked_policy_root(tmp_path: Path) -> None:
    manifest = write_eval_tree(tmp_path)
    policy_root = tmp_path / "evals" / "policies"
    (policy_root / "default.toml").unlink()
    policy_root.rmdir()
    external = tmp_path / "external-policies"
    external.mkdir()
    (external / "default.toml").write_text(POLICY_TOML, encoding="utf-8")
    try:
        policy_root.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    with pytest.raises(ValueError, match="policy root.*symbolic link"):
        load_eval_suite(manifest)


def test_load_eval_suite_checks_policy_root_symlink_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = write_eval_tree(tmp_path)
    policy_root = (tmp_path / "evals" / "policies").absolute()
    original_is_symlink = Path.is_symlink

    def fake_is_symlink(path: Path) -> bool:
        if path.absolute() == policy_root:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    with pytest.raises(ValueError, match="policy root.*symbolic link"):
        load_eval_suite(manifest)


def test_load_eval_suite_requires_evals_ancestor(tmp_path: Path) -> None:
    manifest = write_eval_tree(tmp_path)
    detached = tmp_path / "detached"
    detached.mkdir()
    detached_manifest = detached / "suite.toml"
    detached_manifest.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    (detached / "task.md").write_text("Fix the shell command.\n", encoding="utf-8")
    (detached / "hidden_check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    detached_fixture = detached / "fixture"
    detached_fixture.mkdir()
    (detached_fixture / "input.txt").write_text("before\n", encoding="utf-8")

    with pytest.raises(ValueError, match="evals ancestor"):
        load_eval_suite(detached_manifest)


def test_load_eval_suite_requires_policy_version(tmp_path: Path) -> None:
    manifest = write_eval_tree(tmp_path)
    policy = tmp_path / "evals" / "policies" / "default.toml"
    policy.write_text("[requirements]\nmin_valid_pairs = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="policy.*version"):
        load_eval_suite(manifest)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: (manifest.parent / "task.md").write_text("Changed task.\n", encoding="utf-8"),
        lambda manifest: (manifest.parent / "fixture" / "input.txt").write_text(
            "after\n", encoding="utf-8"
        ),
        lambda manifest: (manifest.parent / "hidden_check.py").write_text(
            "raise SystemExit(1)\n", encoding="utf-8"
        ),
        lambda manifest: (manifest.parents[2] / "policies" / "default.toml").write_text(
            POLICY_TOML.replace("min_valid_pairs = 1", "min_valid_pairs = 2"),
            encoding="utf-8",
        ),
    ],
    ids=["task", "fixture", "evaluator", "policy"],
)
def test_suite_fingerprint_changes_with_referenced_content(
    tmp_path: Path,
    mutate: Callable[[Path], int | None],
) -> None:
    manifest = write_eval_tree(tmp_path)
    original = load_eval_suite(manifest).fingerprint

    mutate(manifest)

    assert load_eval_suite(manifest).fingerprint != original
