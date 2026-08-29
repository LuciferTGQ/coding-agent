from __future__ import annotations

from coding_agent.prompts import build_system_prompt
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


def test_chinese_and_english_preferences_allow_explicit_user_override(tmp_path) -> None:
    chinese = build_system_prompt(tmp_path, response_language="zh")
    english = build_system_prompt(tmp_path, response_language="en")

    assert "当前首选的用户交流语言为中文" in chinese
    assert "用户在当前请求中明确要求使用其他语言" in chinese
    assert "preferred user-facing language is English" in english
    assert "unless the user explicitly requests another response language" in english
    assert "lossy history" in english
    assert "current workspace" in english


def test_language_preference_does_not_change_tool_names_or_schema(tmp_path) -> None:
    workspace = Workspace(tmp_path)
    registry = ToolRegistry(
        [*create_file_tools(workspace), create_command_tool(workspace)]
    )

    definitions = registry.get_definitions()

    assert [item["function"]["name"] for item in definitions] == [
        "list_files",
        "read_file",
        "search_text",
        "write_file",
        "edit_file",
        "run_command",
    ]
    assert all(item["type"] == "function" for item in definitions)
    assert all(item["function"]["parameters"]["type"] == "object" for item in definitions)


def test_delegation_policy_only_appears_when_subagents_are_enabled(tmp_path) -> None:
    disabled = build_system_prompt(tmp_path, subagents_enabled=False)
    enabled = build_system_prompt(tmp_path, subagents_enabled=True)

    assert "delegate_task" not in disabled
    assert "delegate_task" in enabled
    assert "Do not delegate simple tasks" in enabled
    assert "you remain responsible" in enabled
