from pathlib import Path

from rook_agent.evalops.candidates import CandidateStore
from rook_agent.evalops.models import (
    AgentTarget,
    AgentType,
    PromotionDecision,
    PromotionStatus,
    SkillBundle,
)
from rook_agent.evalops.registry import PromotionRegistry
from rook_agent.evalops.release import SkillReleaseService
from rook_agent.skills.discovery import discover_all_skills, discover_project_skills
from rook_agent.skills.models import SkillSource


def test_discovers_project_markdown_skills_and_uses_index_as_context(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "INDEX.md").write_text(
        "# Skill Index\n\n| Skill | 触发场景 |\n|---|---|\n| `daily-brief.md` | 今日资讯 |\n",
        encoding="utf-8",
    )
    (skills_dir / "daily-brief.md").write_text("# Daily Brief\n\n生成日报。", encoding="utf-8")

    catalog = discover_project_skills(tmp_path)

    assert catalog.index_content.startswith("# Skill Index")
    assert [skill.path for skill in catalog.skills] == ["skills/daily-brief.md"]
    skill = catalog.skills[0]
    assert skill.name == "daily-brief"
    assert skill.description == "Daily Brief"
    assert skill.source == SkillSource.PROJECT_MARKDOWN
    assert skill.root == str(tmp_path)


def test_discovers_project_agent_skill_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "fetch-tweet"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: fetch-tweet\ndescription: Fetch X/Twitter posts.\n---\n\n# Fetch Tweet\n",
        encoding="utf-8",
    )

    catalog = discover_project_skills(tmp_path)

    assert len(catalog.skills) == 1
    skill = catalog.skills[0]
    assert skill.name == "fetch-tweet"
    assert skill.description == "Fetch X/Twitter posts."
    assert skill.path == ".agents/skills/fetch-tweet/SKILL.md"
    assert skill.source == SkillSource.PROJECT_AGENT_SKILL


def test_invalid_json_quoted_frontmatter_value_fails_closed(tmp_path: Path) -> None:
    skill_dir = tmp_path / ".agents" / "skills" / "safe-fallback"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: safe-fallback\ndescription: "unterminated\n---\n\n'
        "# Safe Fallback\n",
        encoding="utf-8",
    )

    catalog = discover_project_skills(tmp_path)

    assert catalog.skills[0].name == "safe-fallback"
    assert catalog.skills[0].description == "Safe Fallback"


def test_discovers_frontmatter_triggers(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "daily-brief.md").write_text(
        "---\n"
        "name: daily-brief\n"
        "description: Generate daily brief.\n"
        "triggers: 今日资讯, daily news\n"
        "---\n\n"
        "# Daily Brief\n",
        encoding="utf-8",
    )

    catalog = discover_project_skills(tmp_path)

    assert catalog.skills[0].triggers == ("今日资讯", "daily news")


def test_discovers_global_agent_skills_from_default_roots(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    skill_dir = home / ".agents" / "skills" / "mail"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mail\ndescription: Send and search email.\n---\n\n# Mail\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    catalog = discover_all_skills(tmp_path)

    assert len(catalog.skills) == 1
    skill = catalog.skills[0]
    assert skill.name == "mail"
    assert skill.source == SkillSource.GLOBAL_AGENT_SKILL
    assert skill.root == str(home / ".agents" / "skills")
    assert skill.path == "mail/SKILL.md"


def test_discovers_global_agent_skills_from_codex_default_root(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    skill_dir = home / ".codex" / "skills" / "imagegen"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: imagegen\ndescription: Generate images.\n---\n\n# ImageGen\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    catalog = discover_all_skills(tmp_path)

    assert [(skill.name, skill.source, skill.root, skill.path) for skill in catalog.skills] == [
        ("imagegen", SkillSource.GLOBAL_AGENT_SKILL, str(home / ".codex" / "skills"), "imagegen/SKILL.md")
    ]


def test_extra_global_skill_roots_and_disable_global_skills(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    extra_root = tmp_path / "extra-skills"
    extra_root.mkdir()
    (extra_root / "brief.md").write_text("# Brief Writer\n\n写简报。", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ROOK_SKILL_ROOTS", str(extra_root))

    enabled = discover_all_skills(tmp_path)

    assert [(skill.name, skill.source) for skill in enabled.skills] == [
        ("brief", SkillSource.GLOBAL_MARKDOWN)
    ]

    monkeypatch.setenv("ROOK_DISABLE_GLOBAL_SKILLS", "1")
    disabled = discover_all_skills(tmp_path)

    assert disabled.skills == []


def test_catalog_fingerprint_changes_when_skill_metadata_changes(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_path = skills_dir / "review.md"
    skill_path.write_text("# Review\n\n初版。", encoding="utf-8")

    before = discover_project_skills(tmp_path).fingerprint
    skill_path.write_text("# Review Updated\n\n新版。", encoding="utf-8")
    after = discover_project_skills(tmp_path).fingerprint

    assert before != after


def test_promoted_candidate_is_not_discovered_until_rook_approval(tmp_path: Path) -> None:
    store, registry, service, candidate, decision = _managed_candidate(
        tmp_path, AgentType.ROOK
    )
    registry.record(decision)

    assert discover_project_skills(tmp_path).skills == []

    service.approve(
        skill_name=candidate.bundle.name,
        decision_id=decision.decision_id,
        current_target=decision.target,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint="normalizer",
        approver="reviewer",
        reason="approve Rook runtime use",
    )

    skill = discover_project_skills(tmp_path).skills[0]
    assert skill.name == "managed-discovery"
    assert skill.source is SkillSource.PROJECT_MANAGED
    assert skill.version == 1
    assert skill.content_hash == candidate.content_hash


def test_codex_only_approval_does_not_leak_into_rook_catalog(tmp_path: Path) -> None:
    _store, registry, service, candidate, decision = _managed_candidate(
        tmp_path, AgentType.CODEX
    )
    registry.record(decision)

    service.approve(
        skill_name=candidate.bundle.name,
        decision_id=decision.decision_id,
        current_target=decision.target,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint="normalizer",
        approver="reviewer",
        reason="approve Codex only",
    )

    assert (tmp_path / ".agents/skills/managed-discovery/SKILL.md").is_file()
    assert discover_project_skills(tmp_path).skills == []


def _managed_candidate(tmp_path: Path, agent_type: AgentType):
    store = CandidateStore(tmp_path / ".rook" / "skill-registry")
    candidate = store.create(
        SkillBundle(
            name="managed-discovery",
            description="Managed discovery test.",
            triggers=("managed",),
            procedure=("Perform the managed workflow.",),
            verification=("Verify it.",),
            pitfalls=(),
            evidence_refs=(),
        )
    )
    target = AgentTarget(
        type=agent_type,
        executable=agent_type.value,
        version="1",
        model="model",
        adapter_version="evalops-v1",
    )
    decision = PromotionDecision(
        skill_name=candidate.bundle.name,
        skill_version=candidate.version,
        target=target,
        status=PromotionStatus.PROMOTED,
        reason_code="success_uplift",
        policy_version="1",
        scorecard_hash="score",
        created_at="2026-07-17T00:00:00Z",
        decision_id=f"decision-{agent_type.value}",
        skill_content_hash=candidate.content_hash,
        suite_fingerprint="suite",
        policy_fingerprint="policy",
        normalizer_fingerprint="normalizer",
    )
    registry = PromotionRegistry(tmp_path)
    service = SkillReleaseService(
        project_root=tmp_path,
        candidates=store,
        registry=registry,
    )
    return store, registry, service, candidate, decision
