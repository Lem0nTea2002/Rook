"""工具注册和执行入口。"""

from __future__ import annotations

from typing import Any

from rook_agent.providers.types import ToolDefinition
from rook_agent.tools.schema_validation import validate_tool_arguments
from rook_agent.tools.types import Tool, ToolResult, make_error_result


class ToolRegistry:
    """保存所有可用工具，并提供统一执行入口。

    agent 主循环不应该直接调用某个具体工具函数，而是通过 registry 根据
    模型返回的 tool name 找到对应工具，再把参数传进去执行。
    """

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """注册一个工具。

        同名工具会让 tool calling 变得不可预测，所以这里直接拒绝重复注册。
        """

        if tool.name in self._tools:
            raise ValueError(f"工具已存在：{tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> list[ToolDefinition]:
        """返回所有模型可见的工具 schema。"""

        # 注意这里返回的是“给模型看的声明”，不是 executor 本身。provider 会把这些
        # ToolDefinition 转成 OpenAI/Anthropic 等具体协议里的 tool schema。
        return [tool.definition for tool in self._tools.values()]

    def names(self) -> list[str]:
        """返回当前已注册工具名称。"""

        return list(self._tools.keys())

    def tools(self) -> list[Tool]:
        """返回当前已注册工具对象。"""

        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        """按名称返回工具对象。"""

        return self._tools.get(name)

    def execute(self, name: str, arguments: dict[str, Any] | str | None = None) -> ToolResult:
        """执行指定工具。

        provider 层解析 tool call 时，如果 JSON 参数解析失败，`arguments` 可能是字符串。
        这种情况不能直接执行，应该返回结构化失败结果交给 agent 处理。
        """

        tool = self._tools.get(name)
        if tool is None:
            # 未知工具也写成 ToolResult，而不是抛异常打断 loop。模型下一轮能看到错误并
            # 选择换一个工具或给用户解释。
            return make_error_result(name, f"未知工具：{name}")

        invalid = self.validate(name, arguments)
        if invalid is not None:
            return invalid
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RuntimeError("工具参数校验通过后必须是对象")

        try:
            return tool.executor(**arguments)
        except TypeError as exc:
            # 参数名缺失或多传时通常会走到这里。
            return make_error_result(name, f"工具参数错误：{exc}", arguments=arguments)
        except Exception as exc:  # noqa: BLE001
            # 工具失败不应该直接打断整个 agent loop，而是作为失败结果返回给模型。
            return make_error_result(name, f"工具执行失败：{exc}", arguments=arguments)

    def validate(
        self,
        name: str,
        arguments: dict[str, Any] | str | None = None,
    ) -> ToolResult | None:
        """只校验工具名和参数，不执行工具，也不触发权限策略。"""

        tool = self._tools.get(name)
        if tool is None:
            return make_error_result(name, f"未知工具：{name}", error_code="unknown_tool")
        resolved_arguments: object = {} if arguments is None else arguments
        message = validate_tool_arguments(resolved_arguments, tool.definition.parameters)
        if message is None:
            return None
        return make_error_result(
            name,
            f"invalid_tool_arguments\n{message}",
            error_code="invalid_tool_arguments",
        )
