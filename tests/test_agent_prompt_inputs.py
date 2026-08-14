from rook_agent.agent.prompt_inputs import (
    DEFAULT_PERMISSION_POLICY,
    build_system_prompt_inputs,
    provider_capabilities_for,
    read_agents_md,
)
from rook_agent.context.system_prompt import SystemPromptBuilder


def test_read_agents_md_reads_project_root_file(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("项目规则", encoding="utf-8")

    assert read_agents_md(tmp_path) == "项目规则"


def test_read_agents_md_returns_empty_when_missing(tmp_path) -> None:
    assert read_agents_md(tmp_path) == ""


def test_provider_capabilities_are_static_and_include_model() -> None:
    capabilities = provider_capabilities_for("anthropic", provider_model="claude-test")

    assert capabilities["tool_calling"] is True
    assert capabilities["parallel_tool_calls"] is False
    assert capabilities["system_prompt"] == "separate_field"
    assert capabilities["tool_schema"] == "anthropic_messages"
    assert capabilities["model"] == "claude-test"


def test_build_system_prompt_inputs_uses_permission_policy_without_tool_schema() -> None:
    inputs = build_system_prompt_inputs(
        base_rules="基础规则",
        agents_md="项目规则",
        provider_name="fake",
        provider_model="fake-model",
        permission_policy={"write": "allow"},
    )
    content = SystemPromptBuilder().build(inputs).messages[0].content

    assert "项目规则" in content
    assert "Available tools" not in content
    assert '"model": "fake-model"' in content
    assert '"write": "allow"' in content
    assert inputs.permission_policy["shell"] == DEFAULT_PERMISSION_POLICY["shell"]


def test_confirmed_project_memory_has_its_own_system_prompt_section() -> None:
    inputs = build_system_prompt_inputs(
        base_rules="基础规则",
        agents_md="",
        project_memory_context="- Rule: 使用正确参数",
        provider_name="fake",
    )

    content = SystemPromptBuilder().build(inputs).messages[0].content

    assert "Confirmed project memory:" in content
    assert "- Rule: 使用正确参数" in content


def test_shell_guidance_override_changes_prompt_and_fingerprint() -> None:
    builder = SystemPromptBuilder()
    windows = build_system_prompt_inputs(
        base_rules="基础规则",
        agents_md="",
        provider_name="fake",
        shell_guidance="Windows shell guidance",
    )
    container = build_system_prompt_inputs(
        base_rules="基础规则",
        agents_md="",
        provider_name="fake",
        shell_guidance="Linux container guidance",
    )

    content = builder.build(container).messages[0].content

    assert "Linux container guidance" in content
    assert "Windows shell guidance" not in content
    assert builder.fingerprint(windows) != builder.fingerprint(container)
