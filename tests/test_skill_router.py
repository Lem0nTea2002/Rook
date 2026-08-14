from rook_agent.skills.models import SkillCatalog, SkillDefinition, SkillSource
from rook_agent.skills.router import SkillRouter


def test_explicit_skill_path_selects_skill() -> None:
    catalog = SkillCatalog(skills=[_skill("brief", "skills/brief.md", "写日报")])

    decision = SkillRouter().route("请使用 skills/brief.md 来做", agents_md="", catalog=catalog)

    assert decision.reason == "explicit"
    assert decision.confidence == "high"
    assert decision.selected is not None
    assert decision.selected.path == "skills/brief.md"


def test_agents_route_selects_project_skill_for_info_database_brief() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill("global-family-office-news-brief", "skills/global-family-office-news-brief.md", "全球家办资讯简报"),
            _skill("sensitive-claim-review", "skills/sensitive-claim-review.md", "敏感 claim 复核"),
        ]
    )
    agents_md = (
        "| 用户意图 | 优先读取 |\n"
        "|---|---|\n"
        "| “今天/某天全球家办有什么新闻”“帮我找资讯并总结” | `skills/global-family-office-news-brief.md` |\n"
    )

    decision = SkillRouter().route("按框架跑一次今天的全球家办资讯简报", agents_md=agents_md, catalog=catalog)

    assert decision.reason == "agents_route"
    assert decision.confidence == "high"
    assert decision.selected is not None
    assert decision.selected.path == "skills/global-family-office-news-brief.md"


def test_agents_route_preserves_skill_order_within_matching_line() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill("litigation-review", "skills/litigation-review.md", "诉讼复核"),
            _skill("sensitive-claim-review", "skills/sensitive-claim-review.md", "敏感 claim 复核"),
        ]
    )
    agents_md = (
        "| 用户意图 | 优先读取 |\n"
        "|---|---|\n"
        "| “复核这条诉讼/丑闻/指控” | `skills/sensitive-claim-review.md`，必要时再读 `skills/litigation-review.md` |\n"
    )

    decision = SkillRouter().route("帮我复核这条诉讼 claim", agents_md=agents_md, catalog=catalog)

    assert decision.reason == "agents_route"
    assert decision.confidence == "high"
    assert decision.selected is not None
    assert decision.selected.path == "skills/sensitive-claim-review.md"


def test_metadata_match_selects_global_skill() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill(
                "fetch-tweet",
                "fetch-tweet/SKILL.md",
                "Fetch X/Twitter posts.",
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
            )
        ]
    )

    decision = SkillRouter().route("帮我读取这个 x.com 帖子内容", agents_md="", catalog=catalog)

    assert decision.reason == "metadata_match"
    assert decision.confidence == "high"
    assert decision.selected is not None
    assert decision.selected.name == "fetch-tweet"


def test_ambiguous_matches_do_not_silently_choose() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill("source-verification", "skills/source-verification.md", "来源核验"),
            _skill("second-hop-verification", "skills/second-hop-verification.md", "二跳追证 来源核验"),
        ]
    )

    decision = SkillRouter().route("帮我做来源核验", agents_md="", catalog=catalog)

    assert decision.reason == "ambiguous"
    assert decision.confidence == "medium"
    assert decision.selected is None
    assert [candidate.path for candidate in decision.candidates] == [
        "skills/second-hop-verification.md",
        "skills/source-verification.md",
    ]


def test_no_match_returns_none() -> None:
    catalog = SkillCatalog(skills=[_skill("brief", "skills/brief.md", "写日报")])

    decision = SkillRouter().route("跑一下 pytest", agents_md="", catalog=catalog)

    assert decision.reason == "none"
    assert decision.confidence == "none"
    assert decision.selected is None
    assert decision.candidates == []


def test_project_skill_wins_over_global_skill_with_same_name() -> None:
    project = _skill("fetch-tweet", ".agents/skills/fetch-tweet/SKILL.md", "项目 tweet 规则")
    global_skill = _skill(
        "fetch-tweet",
        "fetch-tweet/SKILL.md",
        "Fetch X/Twitter posts.",
        source=SkillSource.GLOBAL_AGENT_SKILL,
        root="/Users/x/.agents/skills",
    )
    catalog = SkillCatalog(skills=[global_skill, project])

    decision = SkillRouter().route("fetch-tweet 读取这个帖子", agents_md="", catalog=catalog)

    assert decision.reason == "explicit"
    assert decision.selected is project


def test_project_skill_wins_over_global_skill_with_same_metadata_match() -> None:
    project = _skill(
        "fetch-tweet",
        ".agents/skills/fetch-tweet/SKILL.md",
        "Fetch X/Twitter posts.",
        source=SkillSource.PROJECT_AGENT_SKILL,
    )
    global_skill = _skill(
        "fetch-tweet",
        "fetch-tweet/SKILL.md",
        "Fetch X/Twitter posts.",
        source=SkillSource.GLOBAL_AGENT_SKILL,
        root="/Users/x/.agents/skills",
    )
    catalog = SkillCatalog(skills=[global_skill, project])

    decision = SkillRouter().route("帮我读取这个 x.com 帖子内容", agents_md="", catalog=catalog)

    assert decision.reason == "metadata_match"
    assert decision.confidence == "high"
    assert decision.selected is project


def test_global_metadata_requires_name_or_trigger_signal() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill(
                "lark-event",
                "lark-event/SKILL.md",
                (
                    "Use for real-time event listening and bounded CLI task execution "
                    "with timeout handling."
                ),
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
            )
        ]
    )

    decision = SkillRouter().route(
        (
            "You are running a real offline coding task. Inspect the workflow, "
            "make bounded changes, run tests, and respect the timeout."
        ),
        agents_md="",
        catalog=catalog,
    )

    assert decision.selected is None
    assert decision.reason == "global_below_threshold"


def test_global_trigger_can_route_without_skill_name() -> None:
    catalog = SkillCatalog(
        skills=[
            SkillDefinition(
                name="release-auditor",
                path="release-auditor/SKILL.md",
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
                description="Audit release metadata.",
                triggers=("verify release manifest",),
            )
        ]
    )

    decision = SkillRouter().route(
        "Please verify this release manifest before publishing.",
        agents_md="",
        catalog=catalog,
    )

    assert decision.reason == "metadata_match"
    assert decision.selected is not None
    assert decision.selected.name == "release-auditor"


def test_ten_coding_tasks_do_not_route_generic_global_skills() -> None:
    catalog = SkillCatalog(
        skills=[
            _skill(
                "lark-event",
                "lark-event/SKILL.md",
                "Use for real-time event listening, streaming, CLI tasks and timeout handling.",
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
            ),
            _skill(
                "grilling",
                "grilling/SKILL.md",
                "Grill the user about a plan or design before implementation and tests.",
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
            ),
            _skill(
                "playwright",
                "playwright/SKILL.md",
                "Use for automating a real browser from the terminal during a task.",
                source=SkillSource.GLOBAL_AGENT_SKILL,
                root="/Users/x/.agents/skills",
            ),
        ]
    )
    tasks = (
        "Harden the offline GitHub Actions workflow and run its tests.",
        "Preserve the approved Dependabot configuration without changing files.",
        "Compare two RAG evaluation JSON reports.",
        "Represent a skipped quality metric without inventing a score.",
        "Create release.json from the repository metadata.",
        "Fix a Python path traversal regression and add a unit test.",
        "Refactor the CLI status renderer while preserving JSON output.",
        "Diagnose a Windows subprocess timeout from the captured log.",
        "Add a deterministic validator for a TOML evaluation suite.",
        "Review the rollback transaction and report any unsafe overwrite.",
    )

    decisions = [
        SkillRouter().route(task, agents_md="", catalog=catalog)
        for task in tasks
    ]

    assert all(decision.selected is None for decision in decisions)
    assert {
        decision.reason
        for decision in decisions
    } <= {"none", "global_below_threshold"}


def _skill(
    name: str,
    path: str,
    description: str,
    *,
    source: SkillSource = SkillSource.PROJECT_MARKDOWN,
    root: str = "/repo",
) -> SkillDefinition:
    return SkillDefinition(name=name, path=path, source=source, root=root, description=description)
