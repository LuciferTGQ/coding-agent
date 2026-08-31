"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from coding_agent import __version__
from coding_agent.config import SUPPORTED_MODELS, Config, ConfigurationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coding-agent",
        description="Run a DeepSeek-powered coding agent inside a bounded workspace.",
    )
    parser.add_argument("task", nargs="?", help="Natural-language programming task")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root")
    parser.add_argument("--max-steps", type=int, help="Maximum model turns")
    parser.add_argument("--model", choices=SUPPORTED_MODELS, help="DeepSeek model name")
    parser.add_argument(
        "--reasoning-effort", choices=("low", "high", "max"), help="Thinking effort"
    )
    parser.add_argument("--no-thinking", action="store_true", help="Disable thinking mode")
    parser.add_argument("--verbose", action="store_true", help="Show detailed tool progress")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.task:
        parser.error("a task is required")

    try:
        config = Config.from_sources(
            workspace=args.workspace,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            thinking_enabled=not args.no_thinking,
            max_steps=args.max_steps,
        )
    except ConfigurationError as exc:
        parser.error(str(exc))

    # Imported lazily so configuration failures remain fast and dependency-light.
    from coding_agent.runtime import run_task

    return run_task(config=config, task=args.task, verbose=args.verbose)
