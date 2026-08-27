from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent import cli, runtime
from coding_agent.config import Config
from coding_agent.llm import AssistantResponse


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    assert "--workspace" in capsys.readouterr().out


def test_missing_api_configuration_fails_without_exposing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(SystemExit) as caught:
        cli.main(["--workspace", str(tmp_path), "inspect"])

    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "DEEPSEEK_API_KEY" in error
    assert "Traceback" not in error


class FinalModel:
    def complete(self, *, messages, tools) -> AssistantResponse:
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "inspect"}
        assert len(tools) == 6
        return AssistantResponse(
            content="Nothing to change.",
            tool_calls=(),
            provider_message={"role": "assistant", "content": "Nothing to change."},
        )


def test_runtime_composition_prints_final_without_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runtime, "DeepSeekChatClient", lambda **_: FinalModel())
    config = Config(api_key="never-print-this", workspace=tmp_path)

    exit_code = runtime.run_task(config=config, task="inspect")

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Final Answer" in output and "Nothing to change" in output
    assert "never-print-this" not in output
