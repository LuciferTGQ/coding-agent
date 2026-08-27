"""Reset the repeatable demo workspace from its intentionally buggy template."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT_ROOT / "examples" / "buggy_project"


def prepare(destination: Path) -> Path:
    resolved = destination.expanduser().resolve()
    if resolved == PROJECT_ROOT or PROJECT_ROOT not in resolved.parents:
        raise ValueError("Demo destination must be a child of the repository root")
    if resolved.exists():
        shutil.rmtree(resolved)
    shutil.copytree(TEMPLATE, resolved)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=PROJECT_ROOT / ".demo-workspace",
        help="Child directory to replace with a clean demo copy",
    )
    args = parser.parse_args()
    destination = prepare(args.destination)
    print(f"Prepared demo workspace: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

