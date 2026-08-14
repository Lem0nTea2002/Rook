"""工具层 registry 和通用行为测试。"""

from __future__ import annotations

from rook_agent.tools import ToolRegistry, create_builtin_registry
from rook_agent.tools.edit import create_edit_tool
from rook_agent.tools.fetch import create_fetch_tool
from rook_agent.tools.glob import create_glob_tool
from rook_agent.tools.grep import create_grep_tool
from rook_agent.tools.ls import create_ls_tool
from rook_agent.tools.tree import create_tree_tool
from rook_agent.tools.view import create_view_tool
from rook_agent.tools.write import create_write_tool
from rook_agent.tools.delete import create_delete_tool
from rook_agent.tools.apply_patch import create_apply_patch_tool
from rook_agent.tools.diagnostics import create_diagnostics_tool
from rook_agent.tools.python_exec import create_python_exec_tool
from rook_agent.tools.shell import create_shell_tool
from rook_agent.tools.task_boundary import create_task_boundary_tool
from rook_agent.tools.think import create_think_tool
from rook_agent.tools.read_multi import create_read_multi_tool
from rook_agent.tools.ask_user import create_ask_user_tool
from rook_agent.tools.todo import create_todo_tool
from rook_agent.tools.git_log import create_git_log_tool
from rook_agent.tools.git_diff import create_git_diff_tool
from rook_agent.tools.git_status import create_git_status_tool
from rook_agent.tools.web_search import create_web_search_tool
from rook_agent.providers.types import ToolDefinition
from rook_agent.tools.types import Tool, make_text_result


def test_builtin_tool_descriptions_are_agent_facing_english(tmp_path):
    registry = create_builtin_registry(
        tmp_path,
        include_mutation_tools=True,
        include_execution_tools=True,
        include_network_tools=True,
    )
    descriptions = {definition.name: definition.description for definition in registry.definitions()}

    assert descriptions["view"].startswith("Read a UTF-8 text file")
    assert "Use this instead of shell commands like cat" in descriptions["view"]
    assert descriptions["grep"].startswith("Search file contents")
    assert "literal text" in descriptions["grep"]
    assert descriptions["apply_patch"].startswith("Apply a structured patch")
    assert "Prefer this for multi-file edits" in descriptions["apply_patch"]
    assert descriptions["shell"].startswith("Run a shell command")
    assert "Prefer dedicated tools" in descriptions["shell"]
    assert descriptions["todo"].startswith("Track and plan")
    assert "complete 3-7 item plan" in descriptions["todo"]


def test_builtin_registry_contains_read_only_tools(tmp_path):
    registry = create_builtin_registry(tmp_path)

    assert registry.names() == [
        "ls", "view", "grep", "glob", "tree",
        "git_status", "git_diff", "git_log",
        "diagnostics", "think", "read_multi", "ask_user", "todo",
    ]
    assert [definition.name for definition in registry.definitions()] == registry.names()
    assert [tool.name for tool in registry.tools()] == registry.names()


def test_each_tool_has_its_own_module():
    assert create_ls_tool.__module__ == "rook_agent.tools.ls"
    assert create_view_tool.__module__ == "rook_agent.tools.view"
    assert create_grep_tool.__module__ == "rook_agent.tools.grep"
    assert create_glob_tool.__module__ == "rook_agent.tools.glob"
    assert create_tree_tool.__module__ == "rook_agent.tools.tree"
    assert create_git_status_tool.__module__ == "rook_agent.tools.git_status"
    assert create_git_diff_tool.__module__ == "rook_agent.tools.git_diff"
    assert create_write_tool.__module__ == "rook_agent.tools.write"
    assert create_edit_tool.__module__ == "rook_agent.tools.edit"
    assert create_delete_tool.__module__ == "rook_agent.tools.delete"
    assert create_fetch_tool.__module__ == "rook_agent.tools.fetch"
    assert create_web_search_tool.__module__ == "rook_agent.tools.web_search"
    assert create_apply_patch_tool.__module__ == "rook_agent.tools.apply_patch"
    assert create_diagnostics_tool.__module__ == "rook_agent.tools.diagnostics"
    assert create_python_exec_tool.__module__ == "rook_agent.tools.python_exec"
    assert create_shell_tool.__module__ == "rook_agent.tools.shell"
    assert create_task_boundary_tool.__module__ == "rook_agent.tools.task_boundary"
    assert create_think_tool.__module__ == "rook_agent.tools.think"
    assert create_read_multi_tool.__module__ == "rook_agent.tools.read_multi"
    assert create_ask_user_tool.__module__ == "rook_agent.tools.ask_user"
    assert create_todo_tool.__module__ == "rook_agent.tools.todo"
    assert create_git_log_tool.__module__ == "rook_agent.tools.git_log"


def test_builtin_registry_can_include_mutation_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_mutation_tools=True)

    assert registry.names() == [
        "ls", "view", "grep", "glob", "tree",
        "git_status", "git_diff", "git_log",
        "diagnostics", "think", "read_multi", "ask_user", "todo",
        "write", "edit", "delete", "apply_patch",
    ]


def test_builtin_registry_can_include_network_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_network_tools=True)

    assert registry.names() == [
        "ls", "view", "grep", "glob", "tree",
        "git_status", "git_diff", "git_log",
        "diagnostics", "think", "read_multi", "ask_user", "todo",
        "fetch", "web_search",
    ]


def test_builtin_registry_can_include_execution_tools_when_explicitly_enabled(tmp_path):
    registry = create_builtin_registry(tmp_path, include_execution_tools=True)

    assert registry.names() == [
        "ls", "view", "grep", "glob", "tree",
        "git_status", "git_diff", "git_log",
        "diagnostics", "think", "read_multi", "ask_user", "todo",
        "shell", "python_exec",
    ]


def test_builtin_tool_definitions_are_generated_from_function_signatures(tmp_path):
    registry = create_builtin_registry(tmp_path)
    definitions = {definition.name: definition for definition in registry.definitions()}

    assert definitions["view"].parameters == {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    assert definitions["grep"].parameters["required"] == ["pattern"]
    assert definitions["glob"].parameters["required"] == ["pattern"]


def test_registry_returns_error_for_unknown_tool():
    registry = ToolRegistry()

    result = registry.execute("missing_tool", {})

    assert result.ok is False
    assert result.error == "未知工具：missing_tool"


def test_registry_rejects_unknown_arguments_without_calling_executor() -> None:
    calls: list[dict[str, object]] = []
    tool = create_web_search_tool()
    tool.executor = lambda **arguments: calls.append(arguments) or make_text_result(
        "web_search",
        "unexpected",
    )
    registry = ToolRegistry([tool])

    result = registry.execute(
        "web_search",
        {"query": "Rook", "max_chars": 6000},
    )

    assert result.ok is False
    assert result.data["error_code"] == "invalid_tool_arguments"
    assert "未知字段：max_chars" in result.content
    assert "context_max_characters" in result.content
    assert calls == []


def test_registry_validates_required_type_and_enum_before_executor() -> None:
    calls: list[dict[str, object]] = []
    tool = Tool(
        definition=ToolDefinition(
            name="strict",
            description="strict",
            parameters={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["fast", "deep"]},
                },
                "required": ["count", "mode"],
                "additionalProperties": False,
            },
        ),
        executor=lambda **arguments: calls.append(arguments)
        or make_text_result("strict", "unexpected"),
    )
    registry = ToolRegistry([tool])

    missing = registry.execute("strict", {"count": 1})
    wrong_type = registry.execute("strict", {"count": "1", "mode": "fast"})
    wrong_enum = registry.execute("strict", {"count": 1, "mode": "slow"})

    assert missing.data["error_code"] == "invalid_tool_arguments"
    assert "缺少必填字段：mode" in missing.content
    assert wrong_type.data["error_code"] == "invalid_tool_arguments"
    assert "字段 count 类型错误" in wrong_type.content
    assert wrong_enum.data["error_code"] == "invalid_tool_arguments"
    assert "字段 mode 必须是以下值之一" in wrong_enum.content
    assert calls == []
