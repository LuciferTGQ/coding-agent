"""Composition root for the finished harness.

The implementation is filled in once the model, tool, and agent-loop modules
are available. Keeping composition here prevents the Config credential from
spreading through unrelated layers.
"""

from __future__ import annotations

from coding_agent.config import Config


def run_task(*, config: Config, task: str, verbose: bool = False) -> int:
    del config, task, verbose
    raise RuntimeError("The coding-agent runtime is not implemented yet")

