from __future__ import annotations

from pathlib import Path
import sys

import pytest

import coding_agent.config as config_module
from coding_agent.config import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    Config,
    ConfigurationError,
    application_directory,
)


def test_environment_key_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key_file = tmp_path / "api.txt"
    key_file.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")

    config = Config.from_sources(workspace=tmp_path, key_file=key_file)

    assert config.api_key == "environment-secret"
    assert "environment-secret" not in repr(config)


def test_untracked_local_key_is_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    key_file = tmp_path / "api.txt"
    key_file.write_text("local-secret\n", encoding="utf-8")

    config = Config.from_sources(workspace=tmp_path, key_file=key_file)

    assert config.api_key == "local-secret"


def test_application_directory_key_is_used_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    application_root = tmp_path / "application"
    workspace = tmp_path / "workspace"
    application_root.mkdir()
    workspace.mkdir()
    (application_root / "api.txt").write_text("application-secret\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "application_directory", lambda: application_root)

    config = Config.from_sources(workspace=workspace)

    assert config.api_key == "application-secret"


def test_environment_key_precedes_application_directory_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application_root = tmp_path / "application"
    workspace = tmp_path / "workspace"
    application_root.mkdir()
    workspace.mkdir()
    (application_root / "api.txt").write_text("application-secret", encoding="utf-8")
    monkeypatch.setattr(config_module, "application_directory", lambda: application_root)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")

    config = Config.from_sources(workspace=workspace)

    assert config.api_key == "environment-secret"


def test_frozen_application_directory_is_executable_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "dist" / "CodingAgent.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert application_directory() == executable.parent.resolve()


def test_missing_key_has_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        Config.from_sources(workspace=tmp_path, key_file=tmp_path / "missing.txt")


def test_invalid_workspace_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Workspace"):
        Config.from_sources(
            workspace=tmp_path / "missing", require_api_key=False
        )


def test_invalid_positive_integer_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODING_AGENT_MAX_STEPS", "0")

    with pytest.raises(ConfigurationError, match="greater than zero"):
        Config.from_sources(workspace=tmp_path, require_api_key=False)


def test_supported_models_and_default_are_validated(tmp_path: Path) -> None:
    assert DEFAULT_MODEL == "deepseek-v4-flash"
    assert SUPPORTED_MODELS == ("deepseek-v4-flash", "deepseek-v4-pro")
    assert Config.from_sources(workspace=tmp_path, require_api_key=False).model == DEFAULT_MODEL
    assert (
        Config.from_sources(
            workspace=tmp_path,
            model="deepseek-v4-pro",
            require_api_key=False,
        ).model
        == "deepseek-v4-pro"
    )
    with pytest.raises(ConfigurationError, match="model must be one of"):
        Config.from_sources(
            workspace=tmp_path,
            model="deepseek-v4-unknown",
            require_api_key=False,
        )
