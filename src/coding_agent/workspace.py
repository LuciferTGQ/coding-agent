"""Central workspace and local-secret path boundary."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


class WorkspaceError(ValueError):
    """Raised when a requested path violates the workspace boundary."""


class SecretPathError(WorkspaceError):
    """Raised when the agent attempts to access a configured secret path."""


DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)


class Workspace:
    """Resolve every agent-visible path against one immutable root."""

    def __init__(
        self,
        root: str | Path,
        *,
        ignored_dirs: frozenset[str] = DEFAULT_IGNORED_DIRS,
        extra_secret_names: tuple[str, ...] = (),
    ) -> None:
        resolved = Path(root).expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace root is not a directory: {resolved}")
        self.root = resolved
        self.ignored_dirs = ignored_dirs
        self._secret_names = {
            "api.txt",
            ".env",
            ".git-credentials",
            "credentials.json",
            *extra_secret_names,
        }

    def resolve_path(
        self,
        path: str | Path,
        *,
        must_exist: bool = False,
        allow_root: bool = True,
    ) -> Path:
        raw = Path(path)
        if "\x00" in str(path):
            raise WorkspaceError("Access denied: path contains a null byte.")
        if raw.is_absolute():
            raise WorkspaceError("Access denied: tool paths must be workspace-relative.")

        candidate = (self.root / raw).resolve(strict=False)
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("Access denied: path escapes the workspace.") from exc
        if not allow_root and not relative.parts:
            raise WorkspaceError("Access denied: a file path is required.")
        self._deny_secret_parts(raw.parts)
        self._deny_secret_parts(relative.parts)
        if must_exist and not candidate.exists():
            raise WorkspaceError(f"Path does not exist: {self.display(candidate)}")
        return candidate

    def display(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return "<outside workspace>"
        return relative.as_posix() or "."

    def is_secret_name(self, name: str) -> bool:
        lowered = name.lower()
        if lowered in {item.lower() for item in self._secret_names}:
            return True
        return lowered.startswith(".env.") and lowered != ".env.example"

    def iter_files(self, start: Path) -> Iterator[Path]:
        """Yield readable files without following directory symlinks."""

        if start.is_file():
            yield start
            return
        stack = [start]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
            except OSError:
                continue
            child_dirs: list[Path] = []
            for entry in entries:
                if self.is_secret_name(entry.name) or entry.name in self.ignored_dirs:
                    continue
                try:
                    self.resolve_path(entry.relative_to(self.root), must_exist=True)
                except (OSError, WorkspaceError):
                    continue
                if entry.is_dir():
                    if not entry.is_symlink():
                        child_dirs.append(entry)
                elif entry.is_file():
                    yield entry
            stack.extend(reversed(child_dirs))

    def _deny_secret_parts(self, parts: tuple[str, ...]) -> None:
        if any(self.is_secret_name(part) for part in parts):
            raise SecretPathError(
                "Access denied: this path is configured as a local secret file."
            )

