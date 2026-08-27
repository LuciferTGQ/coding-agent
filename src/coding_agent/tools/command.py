"""Bounded local command execution without an intermediate shell."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any

from coding_agent.tools.base import Tool, ToolResult, truncate_text
from coding_agent.workspace import Workspace, WorkspaceError


BLOCKED_EXECUTABLES = frozenset(
    {
        "bash",
        "bash.exe",
        "sh",
        "sh.exe",
        "zsh",
        "fish",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "wsl",
        "wsl.exe",
        "sudo",
        "su",
        "shutdown",
        "shutdown.exe",
        "reboot",
        "format",
        "format.com",
        "diskpart",
        "diskpart.exe",
    }
)


def _filtered_environment() -> dict[str, str]:
    filtered: dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        secret = (
            upper.endswith("_TOKEN")
            or upper.endswith("_SECRET")
            or upper.endswith("_PASSWORD")
            or upper.endswith("_API_KEY")
            or upper in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}
        )
        if not secret:
            filtered[name] = value
    filtered.setdefault("PYTHONIOENCODING", "utf-8")
    return filtered


def _safety_error(argv: list[str]) -> str | None:
    if not argv or not argv[0].strip():
        return "argv must contain an executable"
    executable = Path(argv[0]).name.lower()
    if executable in BLOCKED_EXECUTABLES:
        return f"Command blocked: shell wrapper or dangerous executable '{executable}' is not allowed"
    lowered = [part.lower() for part in argv[1:]]
    if executable in {"git", "git.exe"}:
        if "clean" in lowered:
            return "Command blocked: git clean can destructively remove workspace files"
        if "reset" in lowered and "--hard" in lowered:
            return "Command blocked: git reset --hard rewrites workspace state"
        if "push" in lowered and any(part in {"-f", "--force", "--force-with-lease"} for part in lowered):
            return "Command blocked: force-pushing history is not allowed"
        if "rebase" in lowered or "--amend" in lowered:
            return "Command blocked: rewriting Git history is not allowed"
    return None


def _looks_like_verification(argv: list[str]) -> bool:
    lowered = [Path(argv[0]).name.lower(), *(part.lower() for part in argv[1:])]
    joined = " ".join(lowered)
    markers = (
        "pytest",
        "unittest",
        "compileall",
        "ruff",
        "mypy",
        " py_compile",
        "npm test",
        "npm run test",
        "pnpm test",
        "cargo test",
        "cargo check",
        "cargo build",
        "go test",
        "mvn test",
        "gradle test",
    )
    if any(marker in joined for marker in markers):
        return True
    executable = lowered[0]
    return executable.startswith("python") and any(
        part.endswith(".py") for part in lowered[1:]
    )


def create_command_tool(
    workspace: Workspace,
    *,
    default_timeout: int = 60,
    output_limit: int = 12_000,
) -> Tool:
    def run_command(
        argv: list[str], cwd: str = ".", timeout_seconds: int | None = None
    ) -> ToolResult:
        safety_error = _safety_error(argv)
        if safety_error:
            return ToolResult.failure(safety_error)
        timeout = timeout_seconds if timeout_seconds is not None else default_timeout
        timeout = min(timeout, default_timeout)
        try:
            working_directory = workspace.resolve_path(cwd, must_exist=True)
            if not working_directory.is_dir():
                return ToolResult.failure(f"Command cwd is not a directory: {cwd}")
        except WorkspaceError as exc:
            return ToolResult.failure(str(exc))

        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=working_directory,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=_filtered_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            combined, clipped = truncate_text(
                f"stdout:\n{stdout}\nstderr:\n{stderr}", output_limit
            )
            return ToolResult.failure(
                f"Command timed out after {timeout}s.\n{combined}",
                data={"exit_code": None, "duration_seconds": round(duration, 3), "timed_out": True},
                truncated=clipped,
            )
        except (OSError, ValueError) as exc:
            return ToolResult.failure(f"Command could not start: {type(exc).__name__}: {exc}")

        duration = time.monotonic() - started
        stdout, stdout_clipped = truncate_text(completed.stdout, output_limit // 2)
        stderr, stderr_clipped = truncate_text(completed.stderr, output_limit // 2)
        details = [
            f"exit_code: {completed.returncode}",
            f"duration_seconds: {duration:.3f}",
        ]
        if stdout:
            details.append(f"stdout:\n{stdout}")
        if stderr:
            details.append(f"stderr:\n{stderr}")
        if not stdout and not stderr:
            details.append("Command produced no output.")
        message = "\n".join(details)
        data: dict[str, Any] = {
            "exit_code": completed.returncode,
            "duration_seconds": round(duration, 3),
            "timed_out": False,
        }
        if completed.returncode == 0:
            return ToolResult.success(
                message,
                data=data,
                truncated=stdout_clipped or stderr_clipped,
                verification=_looks_like_verification(argv),
            )
        return ToolResult.failure(
            f"Command exited with code {completed.returncode}.\n{message}",
            data=data,
            truncated=stdout_clipped or stderr_clipped,
        )

    return Tool(
        "run_command",
        "Run argv directly (shell=False) inside the workspace. Use for tests, builds, lint, and programs.",
        {
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Executable and arguments, e.g. ['python','-m','pytest','-q']",
                },
                "cwd": {"type": "string", "description": "Workspace-relative directory"},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": default_timeout,
                },
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        run_command,
    )

