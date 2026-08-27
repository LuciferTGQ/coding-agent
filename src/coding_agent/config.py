"""Configuration loading and the only local API-key file boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is invalid or missing."""


def _positive_int(name: str, value: str | int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return parsed


@dataclass(frozen=True, slots=True)
class Config:
    """Validated settings shared by the CLI and harness.

    The key is deliberately hidden from repr and should only be passed to the
    model client. It must never enter prompts, tools, logs, or subprocesses.
    """

    api_key: str = field(repr=False)
    workspace: Path
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    reasoning_effort: str = "high"
    thinking_enabled: bool = True
    max_steps: int = 24
    command_timeout: int = 60
    tool_output_limit: int = 12_000
    context_soft_budget: int = 120_000
    max_read_lines: int = 200
    max_write_chars: int = 200_000
    max_search_results: int = 100

    @classmethod
    def from_sources(
        cls,
        *,
        workspace: str | Path,
        key_file: str | Path | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool = True,
        max_steps: int | None = None,
        require_api_key: bool = True,
    ) -> "Config":
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise ConfigurationError(f"Workspace is not a directory: {root}")

        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        credential_path = Path(key_file) if key_file is not None else Path.cwd() / "api.txt"
        if not api_key and credential_path.is_file():
            try:
                api_key = credential_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ConfigurationError(f"Could not read local credential file: {credential_path}") from exc
        if require_api_key and not api_key:
            raise ConfigurationError(
                "DeepSeek API key is missing. Set DEEPSEEK_API_KEY; a local untracked "
                "api.txt beside the launch directory is also accepted for development."
            )

        effort = reasoning_effort or os.environ.get("DEEPSEEK_REASONING_EFFORT", "high")
        if effort not in {"low", "high", "max"}:
            raise ConfigurationError("reasoning effort must be one of: low, high, max")

        configured_steps = max_steps if max_steps is not None else os.environ.get("CODING_AGENT_MAX_STEPS", "24")
        return cls(
            api_key=api_key,
            workspace=root,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=model or os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            reasoning_effort=effort,
            thinking_enabled=thinking_enabled,
            max_steps=_positive_int("max steps", configured_steps),
            command_timeout=_positive_int(
                "command timeout", os.environ.get("CODING_AGENT_COMMAND_TIMEOUT", "60")
            ),
            tool_output_limit=_positive_int(
                "tool output limit", os.environ.get("CODING_AGENT_TOOL_OUTPUT_LIMIT", "12000")
            ),
            context_soft_budget=_positive_int(
                "context soft budget", os.environ.get("CODING_AGENT_CONTEXT_BUDGET", "120000")
            ),
        )

