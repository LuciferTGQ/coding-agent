"""Bounded file browsing, search, write, and exact-edit tools."""

from __future__ import annotations

import difflib
from fnmatch import fnmatch
from pathlib import Path

from coding_agent.tools.base import Tool, ToolResult, truncate_text
from coding_agent.workspace import Workspace, WorkspaceError


def _object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def create_file_tools(
    workspace: Workspace,
    *,
    max_read_lines: int = 200,
    max_write_chars: int = 200_000,
    max_search_results: int = 100,
    diff_limit: int = 8_000,
) -> list[Tool]:
    def list_files(path: str = ".", max_depth: int = 3, max_results: int = 200) -> ToolResult:
        try:
            start = workspace.resolve_path(path, must_exist=True)
            if not start.is_dir():
                return ToolResult.failure(f"Not a directory: {workspace.display(start)}")
            limit = min(max_results, 500)
            entries: list[str] = []

            def walk(directory: Path, depth: int) -> None:
                if len(entries) >= limit or depth > max_depth:
                    return
                try:
                    children = sorted(directory.iterdir(), key=lambda item: item.name.lower())
                except OSError:
                    return
                for child in children:
                    if len(entries) >= limit:
                        return
                    if child.name in workspace.ignored_dirs or workspace.is_secret_name(child.name):
                        continue
                    try:
                        workspace.resolve_path(child.relative_to(workspace.root), must_exist=True)
                    except (OSError, WorkspaceError):
                        continue
                    label = child.relative_to(workspace.root).as_posix()
                    if child.is_dir():
                        entries.append(label + "/")
                        if not child.is_symlink():
                            walk(child, depth + 1)
                    elif child.is_file():
                        entries.append(label)

            walk(start, 1)
            suffix = "\n[entry limit reached]" if len(entries) >= limit else ""
            message = "\n".join(entries) if entries else "No visible files found."
            return ToolResult.success(
                message + suffix,
                data={"count": len(entries), "root": workspace.display(start)},
                truncated=bool(suffix),
            )
        except WorkspaceError as exc:
            return ToolResult.failure(str(exc))

    def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        try:
            target = workspace.resolve_path(path, must_exist=True, allow_root=False)
            if not target.is_file():
                return ToolResult.failure(f"Not a file: {workspace.display(target)}")
            if start_line < 1:
                return ToolResult.failure("start_line must be at least 1")
            text = target.read_text(encoding="utf-8")
            lines = text.splitlines()
            requested_end = end_line if end_line is not None else start_line + max_read_lines - 1
            if requested_end < start_line:
                return ToolResult.failure("end_line must be greater than or equal to start_line")
            actual_end = min(requested_end, start_line + max_read_lines - 1, len(lines))
            selected = lines[start_line - 1 : actual_end]
            body = "\n".join(
                f"{number:>6} | {line}"
                for number, line in enumerate(selected, start=start_line)
            )
            clipped = requested_end > actual_end and actual_end < len(lines)
            if clipped:
                body += f"\n[only lines {start_line}-{actual_end} were returned]"
            if not selected:
                body = f"No lines in requested range; file has {len(lines)} lines."
            return ToolResult.success(
                body,
                data={"path": workspace.display(target), "total_lines": len(lines)},
                truncated=clipped,
            )
        except UnicodeDecodeError:
            return ToolResult.failure("File is not valid UTF-8 text")
        except (OSError, WorkspaceError) as exc:
            return ToolResult.failure(str(exc))

    def search_text(
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        file_pattern: str | None = None,
        max_results: int = 50,
    ) -> ToolResult:
        if not query:
            return ToolResult.failure("query must not be empty")
        try:
            start = workspace.resolve_path(path, must_exist=True)
            limit = min(max_results, max_search_results)
            needle = query if case_sensitive else query.casefold()
            matches: list[str] = []
            for file_path in workspace.iter_files(start):
                relative = file_path.relative_to(workspace.root).as_posix()
                if file_pattern and not (
                    fnmatch(relative, file_pattern) or fnmatch(file_path.name, file_pattern)
                ):
                    continue
                try:
                    lines = file_path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for number, line in enumerate(lines, start=1):
                    haystack = line if case_sensitive else line.casefold()
                    if needle in haystack:
                        matches.append(f"{relative}:{number}:{line.strip()}")
                        if len(matches) >= limit:
                            return ToolResult.success(
                                "\n".join(matches) + "\n[result limit reached]",
                                data={"count": len(matches)},
                                truncated=True,
                            )
            if not matches:
                return ToolResult.success("No matches found.", data={"count": 0})
            return ToolResult.success("\n".join(matches), data={"count": len(matches)})
        except WorkspaceError as exc:
            return ToolResult.failure(str(exc))

    def write_file(path: str, content: str, overwrite: bool = False) -> ToolResult:
        if len(content) > max_write_chars:
            return ToolResult.failure(f"content exceeds the {max_write_chars} character limit")
        try:
            target = workspace.resolve_path(path, allow_root=False)
            if target.exists() and not overwrite:
                return ToolResult.failure("File already exists; set overwrite=true to replace it")
            old = target.read_text(encoding="utf-8") if target.exists() else ""
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            diff = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=(workspace.display(target) if old else "/dev/null"),
                    tofile=workspace.display(target),
                )
            )
            diff, clipped = truncate_text(diff, diff_limit)
            return ToolResult.success(
                diff or f"Wrote {workspace.display(target)} (content unchanged).",
                data={"path": workspace.display(target), "characters": len(content)},
                truncated=clipped,
                changed=old != content,
            )
        except (OSError, UnicodeDecodeError, WorkspaceError) as exc:
            return ToolResult.failure(str(exc))

    def edit_file(path: str, old_text: str, new_text: str) -> ToolResult:
        if not old_text:
            return ToolResult.failure("old_text must not be empty")
        try:
            target = workspace.resolve_path(path, must_exist=True, allow_root=False)
            if not target.is_file():
                return ToolResult.failure(f"Not a file: {workspace.display(target)}")
            original = target.read_text(encoding="utf-8")
            count = original.count(old_text)
            if count == 0:
                return ToolResult.failure("old_text was not found; read the file and retry precisely")
            if count > 1:
                return ToolResult.failure(
                    f"old_text is ambiguous: it appears {count} times; include more context"
                )
            updated = original.replace(old_text, new_text, 1)
            if len(updated) > max_write_chars:
                return ToolResult.failure(f"updated file exceeds the {max_write_chars} character limit")
            target.write_text(updated, encoding="utf-8")
            diff = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=workspace.display(target),
                    tofile=workspace.display(target),
                )
            )
            diff, clipped = truncate_text(diff, diff_limit)
            return ToolResult.success(
                diff,
                data={"path": workspace.display(target)},
                truncated=clipped,
                changed=True,
            )
        except UnicodeDecodeError:
            return ToolResult.failure("File is not valid UTF-8 text")
        except (OSError, WorkspaceError) as exc:
            return ToolResult.failure(str(exc))

    common_path = {"type": "string", "description": "Workspace-relative path"}
    return [
        Tool(
            "list_files",
            "List visible workspace files with bounded depth and count. Start here to explore.",
            _object_schema(
                {
                    "path": common_path,
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                }
            ),
            list_files,
        ),
        Tool(
            "read_file",
            "Read a bounded UTF-8 line range with line numbers. Read only relevant ranges.",
            _object_schema(
                {
                    "path": common_path,
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            read_file,
        ),
        Tool(
            "search_text",
            "Search literal text and return concise path:line:text matches; then use read_file.",
            _object_schema(
                {
                    "query": {"type": "string"},
                    "path": common_path,
                    "case_sensitive": {"type": "boolean"},
                    "file_pattern": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                ["query"],
            ),
            search_text,
        ),
        Tool(
            "write_file",
            "Create a UTF-8 text file. Existing files require overwrite=true; prefer edit_file.",
            _object_schema(
                {
                    "path": common_path,
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                ["path", "content"],
            ),
            write_file,
        ),
        Tool(
            "edit_file",
            "Replace old_text that occurs exactly once and return a bounded unified diff.",
            _object_schema(
                {
                    "path": common_path,
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                ["path", "old_text", "new_text"],
            ),
            edit_file,
        ),
    ]


def create_read_only_file_tools(
    workspace: Workspace,
    *,
    max_read_lines: int = 200,
    max_search_results: int = 100,
) -> list[Tool]:
    """Return the browsing subset used by temporary investigation agents."""

    return create_file_tools(
        workspace,
        max_read_lines=max_read_lines,
        max_search_results=max_search_results,
    )[:3]
