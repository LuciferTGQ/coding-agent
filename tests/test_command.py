from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from coding_agent.tools.command import create_command_tool
from coding_agent.tools.registry import ToolRegistry
from coding_agent.workspace import Workspace


def _run(tmp_path: Path, argv: list[str], **arguments):
    registry = ToolRegistry(
        [create_command_tool(Workspace(tmp_path), default_timeout=2, output_limit=4000)]
    )
    payload = {"argv": argv, **arguments}
    return registry.execute("run_command", json.dumps(payload))


def test_command_success_and_no_output(tmp_path: Path) -> None:
    result = _run(tmp_path, [sys.executable, "-c", "pass"])
    assert result.ok
    assert result.data["exit_code"] == 0
    assert "produced no output" in result.message


def test_command_failure_returns_stderr_and_exit_code(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); raise SystemExit(3)"],
    )
    assert not result.ok
    assert result.data["exit_code"] == 3
    assert "bad" in result.message


def test_command_timeout(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=1,
    )
    assert not result.ok
    assert result.data["timed_out"] is True


def test_command_cwd_escape_and_shell_wrapper_are_blocked(tmp_path: Path) -> None:
    escaped = _run(tmp_path, [sys.executable, "--version"], cwd="..")
    shell = _run(tmp_path, ["powershell", "-Command", "Get-ChildItem"])
    assert not escaped.ok and "escapes" in escaped.message
    assert not shell.ok and "blocked" in shell.message.lower()


def test_dangerous_git_history_commands_are_blocked(tmp_path: Path) -> None:
    result = _run(tmp_path, ["git", "reset", "--hard"])
    assert not result.ok and "blocked" in result.message.lower()


def test_subprocess_environment_filters_agent_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-secret")
    result = _run(
        tmp_path,
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('DEEPSEEK_API_KEY', 'missing'))",
        ],
    )
    assert result.ok
    assert "missing" in result.message
    assert "dummy-secret" not in result.message


def test_output_is_truncated(tmp_path: Path) -> None:
    result = _run(tmp_path, [sys.executable, "-c", "print('x' * 10000)"])
    assert result.ok and result.truncated
    assert "output truncated" in result.message

