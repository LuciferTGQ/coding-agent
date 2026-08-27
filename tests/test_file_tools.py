from __future__ import annotations

import json
from pathlib import Path

from coding_agent.tools.file_tools import create_file_tools
from coding_agent.tools.registry import ToolRegistry
from coding_agent.workspace import Workspace


def _registry(tmp_path: Path, **kwargs) -> ToolRegistry:
    return ToolRegistry(create_file_tools(Workspace(tmp_path), **kwargs))


def _run(registry: ToolRegistry, name: str, **arguments):
    return registry.execute(name, json.dumps(arguments))


def test_list_hides_noise_and_secrets(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "api.txt").write_text("secret", encoding="utf-8")
    (tmp_path / ".env.local").write_text("secret", encoding="utf-8")

    result = _run(_registry(tmp_path), "list_files")

    assert result.ok
    assert "src/main.py" in result.message
    assert "api.txt" not in result.message
    assert ".env.local" not in result.message
    assert ".git" not in result.message


def test_read_has_line_numbers_and_clips(tmp_path: Path) -> None:
    (tmp_path / "long.txt").write_text("\n".join(f"line {i}" for i in range(1, 8)), encoding="utf-8")

    result = _run(
        _registry(tmp_path, max_read_lines=3),
        "read_file",
        path="long.txt",
        start_line=2,
        end_line=7,
    )

    assert result.ok and result.truncated
    assert "2 | line 2" in result.message
    assert "4 | line 4" in result.message
    assert "line 5" not in result.message


def test_read_secret_is_denied(tmp_path: Path) -> None:
    (tmp_path / "api.txt").write_text("do-not-read", encoding="utf-8")
    result = _run(_registry(tmp_path), "read_file", path="api.txt")
    assert not result.ok
    assert "local secret" in result.message
    assert "do-not-read" not in result.message


def test_search_is_literal_bounded_and_excludes_secrets(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("Needle\nneedle\nneedle", encoding="utf-8")
    (tmp_path / "api.txt").write_text("needle", encoding="utf-8")

    result = _run(
        _registry(tmp_path, max_search_results=2),
        "search_text",
        query="needle",
        path=".",
        max_results=10,
    )

    assert result.ok and result.truncated
    assert result.data["count"] == 2
    assert "api.txt" not in result.message


def test_write_requires_overwrite_and_returns_diff(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    created = _run(registry, "write_file", path="new.txt", content="hello\n")
    refused = _run(registry, "write_file", path="new.txt", content="bye\n")
    replaced = _run(
        registry, "write_file", path="new.txt", content="bye\n", overwrite=True
    )

    assert created.ok and created.changed
    assert not refused.ok
    assert replaced.ok and "-hello" in replaced.message and "+bye" in replaced.message


def test_edit_exact_match_and_diff(tmp_path: Path) -> None:
    target = tmp_path / "code.py"
    target.write_text("value = 1\n", encoding="utf-8")

    result = _run(
        _registry(tmp_path),
        "edit_file",
        path="code.py",
        old_text="value = 1",
        new_text="value = 2",
    )

    assert result.ok and result.changed
    assert "-value = 1" in result.message and "+value = 2" in result.message
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_edit_rejects_missing_and_ambiguous_text(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("same\nsame\n", encoding="utf-8")
    registry = _registry(tmp_path)

    assert not _run(
        registry, "edit_file", path="code.py", old_text="missing", new_text="x"
    ).ok
    ambiguous = _run(
        registry, "edit_file", path="code.py", old_text="same", new_text="x"
    )
    assert not ambiguous.ok and "ambiguous" in ambiguous.message

