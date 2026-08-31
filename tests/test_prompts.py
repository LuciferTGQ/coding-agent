from __future__ import annotations

from coding_agent.prompts import build_system_prompt
from coding_agent.tools import ToolRegistry, create_command_tool, create_file_tools
from coding_agent.workspace import Workspace


def test_chinese_preference_covers_reasoning_progress_and_final(tmp_path) -> None:
    chinese = build_system_prompt(tmp_path, response_language="zh")

    assert "使用中文进行思考" in chinese
    assert "用户可见的进度说明" in chinese
    assert "最终回答" in chinese
    assert "reasoning_content" in chinese
    assert "用户在当前请求中明确要求使用其他语言" in chinese
    assert (
        "代码、命令、路径、文件名、Tool 名称、JSON 和 Provider 协议字段保持原样"
        in chinese
    )
    assert str(tmp_path.resolve()) not in chinese


def test_english_preference_covers_reasoning_progress_and_final(tmp_path) -> None:
    english = build_system_prompt(tmp_path, response_language="en")

    assert "preferred language for this session is English" in english
    assert "reasoning, user-visible progress, and the final answer" in english
    assert "reasoning_content" in english
    assert "explicit request in the current user message" in english
    assert "code, commands, paths" in english
    assert "tool names, JSON, or provider protocol fields" in english
    assert "lossy history" in english
    assert "current workspace" in english
    assert str(tmp_path.resolve()) not in english


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
