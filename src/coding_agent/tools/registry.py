"""Bridge between JSON tool calls and local Python handlers."""

from __future__ import annotations

import json
from typing import Any, Iterable

from coding_agent.tools.base import Tool, ToolResult


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = (), *, output_limit: int = 12_000) -> None:
        self._tools: dict[str, Tool] = {}
        self.output_limit = output_limit
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get_definitions(self) -> list[dict[str, Any]]:
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: str) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.failure(f"Unknown tool: {name}")
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return ToolResult.failure(
                f"Invalid JSON arguments for {name}: {exc.msg} at character {exc.pos}"
            )
        if not isinstance(parsed, dict):
            return ToolResult.failure(f"Arguments for {name} must be a JSON object")

        validation_error = self._validate(parsed, tool.parameters)
        if validation_error:
            return ToolResult.failure(f"Invalid arguments for {name}: {validation_error}")
        try:
            result = tool.handler(**parsed)
            if not isinstance(result, ToolResult):
                return ToolResult.failure(f"Tool {name} returned an invalid result")
            return result.bounded(self.output_limit)
        except Exception as exc:
            return ToolResult.failure(f"Tool {name} failed: {type(exc).__name__}: {exc}").bounded(
                self.output_limit
            )

    @staticmethod
    def _validate(arguments: dict[str, Any], schema: dict[str, Any]) -> str | None:
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in arguments:
                return f"missing required argument '{name}'"
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(arguments) - set(properties))
            if unexpected:
                return f"unexpected argument '{unexpected[0]}'"

        expected_types = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, value in arguments.items():
            spec = properties.get(name)
            if spec is None:
                continue
            schema_type = spec.get("type")
            expected = expected_types.get(schema_type)
            if expected is not None:
                wrong_bool = schema_type in {"integer", "number"} and isinstance(value, bool)
                if wrong_bool or not isinstance(value, expected):
                    return f"argument '{name}' must be {schema_type}"
            if "enum" in spec and value not in spec["enum"]:
                allowed = ", ".join(map(str, spec["enum"]))
                return f"argument '{name}' must be one of: {allowed}"
            if isinstance(value, int) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    return f"argument '{name}' must be at least {spec['minimum']}"
                if "maximum" in spec and value > spec["maximum"]:
                    return f"argument '{name}' must be at most {spec['maximum']}"
            if schema_type == "array" and spec.get("items", {}).get("type") == "string":
                if not all(isinstance(item, str) for item in value):
                    return f"every item in argument '{name}' must be string"
        return None

