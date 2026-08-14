"""工具参数的确定性 JSON Schema 子集校验。"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any


def validate_tool_arguments(
    arguments: object,
    schema: dict[str, Any],
) -> str | None:
    """在权限判断和 executor 前校验工具参数，返回稳定的中文错误。"""

    if not isinstance(arguments, dict):
        return "工具参数不是合法对象"
    if not schema or schema.get("type") != "object":
        return None

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = schema.get("required")
    if not isinstance(required, list):
        required = []

    missing = [str(name) for name in required if name not in arguments]
    if missing:
        return f"缺少必填字段：{', '.join(missing)}"

    if schema.get("additionalProperties", False) is False:
        unknown = [str(name) for name in arguments if name not in properties]
        if unknown:
            field = unknown[0]
            available = sorted(str(name) for name in properties)
            suggestion = get_close_matches(field, available, n=1, cutoff=0.35)
            lines = [f"未知字段：{field}"]
            if suggestion:
                lines.append(f"你是否想用：{suggestion[0]}")
            if available:
                lines.append(f"可用字段：{', '.join(available)}")
            return "\n".join(lines)

    for name, value in arguments.items():
        property_definition = properties.get(name)
        if not isinstance(property_definition, dict):
            continue
        expected_type = property_definition.get("type")
        if isinstance(expected_type, str) and not _matches_type(value, expected_type):
            return f"字段 {name} 类型错误：需要 {expected_type}"
        allowed = property_definition.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            values = ", ".join(str(item) for item in allowed)
            return f"字段 {name} 必须是以下值之一：{values}"
        if property_definition.get("format") == "path":
            if not isinstance(value, str) or "\x00" in value:
                return f"字段 {name} 不是合法路径"
    return None


def _matches_type(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True
