from __future__ import annotations

from coding_agent.tools.base import Tool, ToolResult
from coding_agent.tools.registry import ToolRegistry


SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer", "minimum": 1},
        "mode": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def _registry() -> ToolRegistry:
    return ToolRegistry(
        [Tool("demo", "demo tool", SCHEMA, lambda **kwargs: ToolResult.success(str(kwargs)))]
    )


def test_definition_uses_chat_completions_shape() -> None:
    definition = _registry().get_definitions()[0]
    assert definition["type"] == "function"
    assert definition["function"]["parameters"] == SCHEMA


def test_invalid_json_is_feedback() -> None:
    assert not _registry().execute("demo", "{").ok


def test_unknown_tool_is_feedback() -> None:
    assert "Unknown tool" in _registry().execute("missing", "{}").message


def test_missing_wrong_type_enum_and_unexpected_are_rejected() -> None:
    registry = _registry()
    assert "missing required" in registry.execute("demo", "{}").message
    assert "must be integer" in registry.execute("demo", '{"name":"x","count":"1"}').message
    assert "must be one of" in registry.execute("demo", '{"name":"x","mode":"c"}').message
    assert "unexpected" in registry.execute("demo", '{"name":"x","extra":1}').message


def test_handler_exception_is_feedback() -> None:
    tool = Tool("boom", "boom", {"type": "object", "properties": {}}, lambda: 1 / 0)
    result = ToolRegistry([tool]).execute("boom", "{}")
    assert not result.ok
    assert "ZeroDivisionError" in result.message

