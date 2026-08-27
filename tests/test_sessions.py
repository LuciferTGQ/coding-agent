from __future__ import annotations

from pathlib import Path
from typing import Sequence

from coding_agent.llm import AssistantResponse
from coding_agent.session_runtime import SessionRuntime
from coding_agent.sessions import SessionStore


class FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[list[dict]] = []

    def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
        self.requests.append(list(messages))
        content = self.responses.pop(0)
        return AssistantResponse(
            content=content,
            tool_calls=(),
            provider_message={
                "role": "assistant",
                "content": content,
                "reasoning_content": "test reasoning",
            },
        )


def test_session_store_crud_and_restart(tmp_path: Path) -> None:
    root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(root)
    created = store.create(workspace=workspace, title="First")
    created.transcript.append({"type": "user", "text": "hello"})
    store.save(created)

    restarted = SessionStore(root)
    loaded = restarted.load(created.id)
    assert loaded.title == "First"
    assert loaded.workspace == str(workspace.resolve())
    assert loaded.transcript[0]["text"] == "hello"
    assert [item.id for item in restarted.list()] == [created.id]

    restarted.delete(created.id)
    assert restarted.list() == []


def test_destroy_reload_then_second_turn_receives_prior_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    model = FakeModel(["First answer", "Second answer"])

    SessionRuntime(store, model_factory=lambda _: model).run_turn(
        session.id, "Remember alpha", stream=False
    )
    del store

    restarted = SessionStore(tmp_path / "state")
    SessionRuntime(restarted, model_factory=lambda _: model).run_turn(
        session.id, "What did I ask?", stream=False
    )

    second_request = model.requests[1]
    assert [message["role"] for message in second_request] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert second_request[1]["content"] == "Remember alpha"
    assert second_request[2]["content"] == "First answer"
    loaded = restarted.load(session.id)
    assert loaded.model_context is not None
    assert any(item.get("text") == "Second answer" for item in loaded.transcript)


def test_attachment_must_be_utf8_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("context", encoding="utf-8")
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    model = FakeModel(["Read it"])

    SessionRuntime(store, model_factory=lambda _: model).run_turn(
        session.id, "Inspect", attachments=["note.txt"], stream=False
    )

    assert "note.txt" in model.requests[0][1]["content"]
    assert "context" not in model.requests[0][1]["content"]
