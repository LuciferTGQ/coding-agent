from __future__ import annotations

from pathlib import Path
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from coding_agent.llm import AssistantResponse
from coding_agent.llm import ModelError
from coding_agent.llm import ToolCall
from coding_agent.session_runtime import SessionRuntime
from coding_agent.sessions import SessionPersistenceError, SessionStore


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


def test_transient_permission_error_retries_with_unique_temp_and_cleans_up(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state", replace_retry_delays=(0, 0))
    session = store.create(workspace=workspace)
    real_replace = os.replace
    sources: list[Path] = []

    def flaky_replace(source, target) -> None:
        sources.append(Path(source))
        if len(sources) < 3:
            raise PermissionError(5, "temporarily denied")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", flaky_replace)
    session.transcript.append({"type": "user", "text": "persist me"})
    store.save(session)

    assert len(sources) == 3
    assert len({source.name for source in sources}) == 1
    assert sources[0].name != f"{session.id}.json.tmp"
    assert store.load(session.id).transcript[-1]["text"] == "persist me"
    assert list(store.sessions_dir.glob("*.tmp")) == []


def test_persistent_permission_error_is_reported_and_keeps_previous_json(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state", replace_retry_delays=(0,))
    session = store.create(workspace=workspace, title="Original")
    monkeypatch.setattr(
        os,
        "replace",
        lambda *_: (_ for _ in ()).throw(PermissionError(5, "still denied")),
    )
    session.title = "Not committed"

    try:
        store.save(session)
    except SessionPersistenceError as exc:
        assert "after 2 attempts" in str(exc)
    else:
        raise AssertionError("expected bounded persistence failure")

    assert store.load(session.id).title == "Original"
    assert list(store.sessions_dir.glob("*.tmp")) == []


def test_runtime_save_preserves_concurrent_metadata_patch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    runtime_copy = store.load(session.id)
    runtime_copy.transcript.append({"type": "assistant", "text": "completed"})

    store.update_metadata(session.id, pinned=True, title="Pinned title")
    store.save_runtime(runtime_copy)
    loaded = store.load(session.id)

    assert loaded.pinned is True
    assert loaded.title == "Pinned title"
    assert loaded.transcript[-1]["text"] == "completed"


def test_multiple_sessions_can_save_from_threads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    sessions = [store.create(workspace=workspace, title=f"Session {index}") for index in range(6)]

    def save_many(session_id: str) -> None:
        for index in range(20):
            session = store.load(session_id)
            session.transcript.append({"type": "status", "text": str(index)})
            store.save_runtime(session)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(save_many, [session.id for session in sessions]))

    assert all(len(store.load(session.id).transcript) == 20 for session in sessions)
    assert list(store.sessions_dir.glob("*.tmp")) == []


def test_tool_executes_after_intermediate_session_save_failure(
    tmp_path: Path, monkeypatch
) -> None:
    class ToolModel:
        def __init__(self) -> None:
            self.responses = [
                AssistantResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="run",
                            name="run_command",
                            arguments=json.dumps(
                                {
                                    "argv": [
                                        sys.executable,
                                        "-c",
                                        "from pathlib import Path; Path('executed.txt').write_text('yes')",
                                    ]
                                }
                            ),
                        ),
                    ),
                    provider_message={"role": "assistant", "content": "", "tool_calls": []},
                ),
                AssistantResponse(
                    content="Done",
                    tool_calls=(),
                    provider_message={"role": "assistant", "content": "Done"},
                ),
            ]

        def complete(self, *, messages: Sequence[dict], tools: Sequence[dict]) -> AssistantResponse:
            return self.responses.pop(0)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "state")
    session = store.create(workspace=workspace)
    real_save = store.save_runtime
    calls = 0

    def fail_tool_call_save(value) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SessionPersistenceError("deterministic tool_call save failure")
        real_save(value)

    monkeypatch.setattr(store, "save_runtime", fail_tool_call_save)
    events = []
    result = SessionRuntime(store, model_factory=lambda _: ToolModel()).run_turn(
        session.id, "run it", on_event=events.append, stream=False
    )

    assert result.status == "completed"
    assert (workspace / "executed.txt").read_text(encoding="utf-8") == "yes"
    assert any(event.kind == "persistence_warning" for event in events)
    assert any(event.kind == "persistence_recovered" for event in events)
    transcript = store.load(session.id).transcript
    assert any(item.get("type") == "tool_result" for item in transcript)
    assert transcript[-1]["type"] == "assistant"


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
