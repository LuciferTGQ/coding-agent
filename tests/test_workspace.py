from __future__ import annotations

import os
from pathlib import Path

import pytest

from coding_agent.workspace import SecretPathError, Workspace, WorkspaceError


def test_normal_and_nested_paths(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "file.py"
    nested.parent.mkdir()
    nested.write_text("pass", encoding="utf-8")
    workspace = Workspace(tmp_path)

    assert workspace.resolve_path("src/file.py", must_exist=True) == nested.resolve()


@pytest.mark.parametrize("path", ["../outside.txt", "../../outside.txt"])
def test_parent_escape_is_denied(tmp_path: Path, path: str) -> None:
    with pytest.raises(WorkspaceError, match="escapes"):
        Workspace(tmp_path).resolve_path(path)


def test_absolute_path_is_denied(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="workspace-relative"):
        Workspace(tmp_path).resolve_path(tmp_path / "file.txt")


@pytest.mark.parametrize("name", ["api.txt", ".env", ".env.local", "credentials.json"])
def test_secret_paths_are_denied(tmp_path: Path, name: str) -> None:
    with pytest.raises(SecretPathError, match="local secret"):
        Workspace(tmp_path).resolve_path(name)


def test_env_example_is_not_treated_as_secret(tmp_path: Path) -> None:
    assert Workspace(tmp_path).resolve_path(".env.example") == tmp_path / ".env.example"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlink_escape_is_denied_when_supported(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted")

    with pytest.raises(WorkspaceError, match="escapes"):
        Workspace(tmp_path).resolve_path("escape/file.txt")

