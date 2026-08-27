from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent.config import Config, ConfigurationError


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

