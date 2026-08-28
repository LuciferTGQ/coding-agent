"""Launch the Coding Agent desktop interface from the repository root."""

from __future__ import annotations

import sys

try:
    from coding_agent.gui.app import main
except ModuleNotFoundError as exc:
    print(
        'Could not start the GUI. Install the project first with: '
        'python -m pip install -e ".[dev]"',
        file=sys.stderr,
    )
    raise SystemExit(1) from exc


if __name__ == "__main__":
    raise SystemExit(main())
