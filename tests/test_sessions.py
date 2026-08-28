from __future__ import annotations

from pathlib import Path
import json
from typing import Sequence

from coding_agent.llm import AssistantResponse
from coding_agent.llm import ModelError
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


def test_optional_sidebar_metadata_is_backward_compatible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    path = store.sessions_dir / f"{session.id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("pinned", None)
    payload.pop("unread", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.load(session.id)

    assert loaded.pinned is False
    assert loaded.unread is False
    assert loaded.workspace == str(workspace.resolve())
    assert loaded.transcript == session.transcript
    assert loaded.model_context == session.model_context


def test_pinned_sessions_sort_before_newer_normal_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    pinned = store.create(workspace=workspace, title="Pinned")
    pinned.pinned = True
    store.save(pinned)
    normal = store.create(workspace=workspace, title="Newer normal")

    ordered = store.list()

    assert [session.id for session in ordered] == [pinned.id, normal.id]


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


def test_nested_credential_field_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    session.transcript.append({"type": "status", "api_key": "must-not-save"})

    try:
        store.save(session)
    except ValueError as exc:
        assert "Credentials" in str(exc)
    else:
        raise AssertionError("expected credential field rejection")


def test_model_failure_is_persisted_as_status(tmp_path: Path) -> None:
    class FailingModel:
        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            raise ModelError("provider unavailable")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    runtime = SessionRuntime(store, model_factory=lambda _: FailingModel())

    try:
        runtime.run_turn(session.id, "try once", stream=False)
    except Exception:
        pass
    else:
        raise AssertionError("expected model failure")

    loaded = store.load(session.id)
    assert loaded.transcript[-1]["type"] == "status"
    assert loaded.transcript[-1]["kind"] == "error"
    assert loaded.transcript[-1]["ok"] is False


def test_gui_language_updates_stable_prompt_without_changing_protocol(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    model = FakeModel(["完成"])
    configs = []

    def factory(config):
        configs.append(config)
        return model

    SessionRuntime(
        store, model_factory=factory, language="zh", max_steps=9
    ).run_turn(
        session.id, "检查项目", stream=False
    )

    system = model.requests[0][0]
    assert system["role"] == "system"
    assert "当前首选的用户交流语言为中文" in system["content"]
    assert "list_files" in system["content"]
    assert configs[0].max_steps == 9


def test_switching_session_language_keeps_workspace_and_conversation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace, preferred_language="zh")
    model = FakeModel(["第一轮", "Second turn"])

    SessionRuntime(store, model_factory=lambda _: model, language="zh").run_turn(
        session.id, "Please inspect this project.", stream=False
    )
    SessionRuntime(store, model_factory=lambda _: model, language="en").run_turn(
        session.id, "帮我继续检查", stream=False
    )

    loaded = store.load(session.id)
    assert loaded.preferred_language == "en"
    assert loaded.workspace == str(workspace.resolve())
    assert len(loaded.model_context["turns"]) == 2
    assert "preferred user-facing language is English" in model.requests[1][0]["content"]
    assert model.requests[1][1]["content"] == "Please inspect this project."
