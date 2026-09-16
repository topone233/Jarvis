from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
import app.runs as runs_module
from app.config import BootstrapStore
from app.main import create_app
from app.provider import ProviderEvent
from app.runtime import CoreServices, Runtime
from app.schemas import ThinkingLevel


class MemoryReplyProvider:
    """A model whose answer ends with a memory block.

    The block is how a run remembers now: the main model appends it, the run
    strips it from what it shows, and the actions inside it are carried out
    once the answer is complete.
    """

    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> AsyncIterator[ProviderEvent]:
        del profile, messages, chat_model, thinking
        yield ProviderEvent("delta", {"text": "好的，记住了。"})
        yield ProviderEvent(
            "delta",
            {
                "text": "\n```memory\n"
                '{"write": [{"kind": "preference", "key": "回复风格",'
                ' "content": "喜欢简洁回答", "confidence": 0.9}]}\n```\n'
            },
        )
        yield ProviderEvent("usage", {"usage": {"prompt_tokens": 12, "completion_tokens": 6}})
        yield ProviderEvent("done", {})

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> str:
        del profile, messages, chat_model, thinking
        return "## 背景\n已压缩的历史。"

    async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile
        return [[float(len(text)), 1.0] for text in texts]

    async def list_models(self, profile: dict[str, Any]) -> list[Any]:
        del profile
        return [{"id": "mock-chat"}]


def test_project_conversation_and_streaming_run(client: TestClient, core: CoreServices) -> None:
    profile = client.post(
        "/api/model-profiles",
        json={
            "name": "Mock",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "embedding_model": "mock-embedding",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
    ).json()
    project = client.post("/api/projects", json={"name": "核心能力"}).json()
    conversation = client.post(
        "/api/conversations",
        json={"project_id": project["id"], "model_profile_id": profile["id"]},
    ).json()

    core.provider = MemoryReplyProvider()  # type: ignore[assignment]
    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "我喜欢简洁回答。", "thinking": "high"},
    )

    assert response.status_code == 200
    assert "event: run.started" in response.text
    # A reply that finishes before the client attaches arrives whole in the
    # terminal event instead of as deltas; the client renders the same answer
    # either way, which is what makes reconnecting safe.
    assert "event: message.completed" in response.text
    # The memory block is the model's business, never the user's: neither the
    # deltas nor the finished answer carry it.
    assert "```memory" not in response.text
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert messages[-1]["content"] == "好的，记住了。"
    memories = client.get("/api/memories").json()
    assert memories[0]["content"] == "喜欢简洁回答"
    assert memories[0]["source_excerpt"] == "我喜欢简洁回答。"
    # The retrieval cache is plumbing: hundreds of floats the screen never
    # shows, so the API leaves them behind.
    assert "embedding_json" not in memories[0]
    assert "embedding_model" not in memories[0]


def test_a_run_without_memory_actions_has_no_memory_step(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    """The ordinary answer runs no memory step at all.

    This is the whole point of the redesign: the step used to appear on every
    run, measuring an extraction call that usually returned an empty list.
    """
    conversation = client.post("/api/conversations", json={"title": "普通"}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/runs", json={"content": "hi"})

    assert response.status_code == 200
    events = client.get(f"/api/runs/{_run_id(response.text)}/events").json()
    assert [event["stage"] for event in events if event["stage"] == "memory_write"] == []


def test_the_memory_step_reports_what_it_did(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    conversation = client.post("/api/conversations", json={"title": "明细"}).json()
    core.provider = MemoryReplyProvider()  # type: ignore[assignment]
    response = client.post(f"/api/conversations/{conversation['id']}/runs", json={"content": "记"})
    events = client.get(f"/api/runs/{_run_id(response.text)}/events").json()

    records = [event for event in events if event["stage"] == "memory_write"]
    assert [(event["state"]) for event in records] == ["running", "completed"]
    completed = records[-1]
    assert completed["payload"]["count"] == 1
    assert completed["payload"]["items"][0]["action"] == "created"


def _run_id(sse: str) -> str:
    return json.loads(_frames(sse, "run.started")[0])["run_id"]


def test_deleting_a_conversation_erases_it_with_its_trail(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    """Deletion is permanent: the conversation takes its history with it.

    Messages, the run log, the audit events, feedback - all gone, by the
    user's decision that a removed conversation leaves nothing behind. The
    memory it produced stays, but loses the pointer to the message that
    sourced it, because that message no longer exists.
    """
    conversation = client.post(
        "/api/conversations", json={"model_profile_id": profile["id"]}
    ).json()
    core.provider = MemoryReplyProvider()  # type: ignore[assignment]
    client.post(f"/api/conversations/{conversation['id']}/runs", json={"content": "记"})
    reply = client.get(f"/api/conversations/{conversation['id']}/messages").json()[-1]
    feedback = client.post(f"/api/messages/{reply['id']}/feedback", json={"kind": "up"})
    assert feedback.status_code == 201

    memory = core.store.list_memories()[0]
    assert memory["source_message_id"] == reply["parent_id"]

    deleted = client.delete(f"/api/conversations/{conversation['id']}")
    assert deleted.status_code == 204

    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404
    assert client.get(f"/api/conversations/{conversation['id']}/messages").status_code == 404
    assert core.database.fetchall("SELECT * FROM assistant_runs") == []
    assert core.database.fetchall("SELECT * FROM run_events") == []
    assert core.database.fetchall("SELECT * FROM messages") == []
    assert core.database.fetchall("SELECT * FROM feedback") == []
    assert (
        core.database.fetchall(
            "SELECT * FROM trash_items WHERE entity_type IN ('conversation', 'message')"
        )
        == []
    )
    kept = core.store.list_memories()[0]
    assert kept["content"] == "喜欢简洁回答"
    assert kept["source_message_id"] is None


class QuietProvider:
    """A model that says nothing for a while before it answers.

    The silence is the point: it is what every subscriber sees while a model is
    thinking, and it is the state the heartbeat exists to make visible.
    """

    def __init__(self, quiet: float) -> None:
        self.quiet = quiet

    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> AsyncIterator[ProviderEvent]:
        del profile, messages, chat_model, thinking
        await asyncio.sleep(self.quiet)
        yield ProviderEvent("delta", {"text": "ready."})
        yield ProviderEvent("done", {})


def test_a_quiet_run_still_says_something(
    client: TestClient,
    core: CoreServices,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence and death have to look different from the client.

    A model thinking for a minute and a process that died a minute ago both
    arrive as "no bytes" - so the stream sends a comment line while it waits.
    Nothing in the protocol requires it, and every SSE parser ignores it, which
    is exactly why it can be the liveness signal. The body stays ASCII because
    httpx guesses the charset of a short response, and a guess that lands on
    anything but UTF-8 turns the answer into mojibake this test is not about.
    """
    monkeypatch.setattr(runs_module, "HEARTBEAT_SECONDS", 0.02)
    core.provider = QuietProvider(quiet=0.3)  # type: ignore[assignment]
    conversation = client.post("/api/conversations", json={"title": "安静"}).json()

    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "think for a while"},
    ) as response:
        assert response.status_code == 200
        body = "".join(response.iter_lines())

    assert ": keep-alive" in body
    assert "ready." in body


def _frames(body: str, name: str) -> list[str]:
    """The data lines of every ``name`` frame, in the order they arrived."""
    found: list[str] = []
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("event: "):
            current = line.removeprefix("event: ")
        elif line.startswith("data: ") and current == name:
            found.append(line.removeprefix("data: "))
    return found


def test_the_audit_trail_of_a_conversation_comes_back_whole(
    client: TestClient, profile: dict[str, Any]
) -> None:
    """What a reloaded page needs to redraw execution steps it never watched.

    Every run in the conversation is included, keyed by run id, because steps
    belong to an answer and only the newest answer is still being watched.
    """
    conversation = client.post("/api/conversations", json={"title": "轨迹"}).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/runs", json={"content": "记下轨迹"}
    )
    run_id = json.loads(_frames(response.text, "run.started")[0])["run_id"]

    trail = client.get(f"/api/conversations/{conversation['id']}/run-events").json()
    events = trail[run_id]

    stages = [(event["stage"], event["state"]) for event in events]
    assert ("model_stream", "running") in stages
    assert ("model_stream", "completed") in stages
    # Ordered, because a duration is one record's timestamp minus another's.
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)


def test_a_live_audit_event_carries_its_own_timestamp(
    client: TestClient,
    core: CoreServices,
    profile: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The screen times a stage while it runs, and the live event is all it has.

    Reading the trail back is the other half - both have to agree on the clock
    they are using, or a stage would change duration the moment the page is
    reloaded.

    The model is told to stay quiet so the run is still going while the response
    is read; the keep-alive comment is the proof it was, since only the live
    branch ever emits one.
    """
    monkeypatch.setattr(runs_module, "HEARTBEAT_SECONDS", 0.02)
    core.provider = QuietProvider(quiet=0.3)  # type: ignore[assignment]
    conversation = client.post("/api/conversations", json={"title": "计时"}).json()

    with client.stream(
        "POST", f"/api/conversations/{conversation['id']}/runs", json={"content": "计时"}
    ) as response:
        body = "".join(f"{line}\n" for line in response.iter_lines())

    assert ": keep-alive" in body
    audits = [json.loads(data) for data in _frames(body, "audit")]
    assert audits, "这一轮没有产生任何审计事件"
    for event in audits:
        assert event["created_at"]
        assert event["stage"]


def test_a_finished_run_replays_the_steps_it_ran(
    client: TestClient, profile: dict[str, Any]
) -> None:
    """A run that is already over is still worth the whole trail.

    The producer drops its broadcast the instant it finishes, so reattaching a
    moment later takes the replay branch. A page that attaches a second too late
    is the ordinary case rather than the corner, and without this the steps
    would be there or not depending on a race the user cannot see.
    """
    conversation = client.post("/api/conversations", json={"title": "回放"}).json()
    posted = client.post(f"/api/conversations/{conversation['id']}/runs", json={"content": "回放"})
    run_id = json.loads(_frames(posted.text, "run.started")[0])["run_id"]

    body = client.get(f"/api/runs/{run_id}/stream").text

    audits = [json.loads(data) for data in _frames(body, "audit")]
    stages = [(event["stage"], event["state"]) for event in audits]
    assert ("context_retrieval", "completed") in stages
    assert ("model_stream", "completed") in stages
    assert all(event["created_at"] for event in audits)
    # Still the replay branch, not a live one that happened to be caught early.
    assert _frames(body, "message.completed")


def test_import_search_and_restore_document(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "知识库"}).json()
    imported = client.post(
        "/api/knowledge/import",
        data={"project_id": project["id"]},
        files={
            "files": ("source.py", b"def context_retrieval():\n    return 'Jarvis'", "text/plain")
        },
    )

    assert imported.status_code == 201
    document = imported.json()["items"][-1]["document"]
    found = client.get(
        "/api/knowledge/search",
        params={"project_id": project["id"], "query": "context retrieval"},
    )
    assert found.status_code == 200
    assert found.json()["items"]

    deleted = client.delete(f"/api/knowledge/documents/{document['id']}")
    assert deleted.status_code == 204
    trash = client.get("/api/trash").json()
    document_trash = next(item for item in trash if item["entity_type"] == "knowledge_document")
    restored = client.post(f"/api/trash/{document_trash['id']}/restore")
    assert restored.status_code == 200


def test_a_built_frontend_is_served_by_the_same_process(
    core: CoreServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One process should serve the whole app once the frontend has been built.

    The directory is faked here rather than built, because what is under test is
    the routing: the SPA fallback, the assets mount, and the rule that a mistyped
    API path must not answer with a page of HTML.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main_module, "FRONTEND_DIST", dist)

    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = core
    with TestClient(create_app(runtime)) as client:
        # A client-side route is answered by the app itself.
        assert client.get("/c/abc").text == "<!doctype html><div id=root></div>"
        assert client.get("/assets/app.js").text == "console.log(1)"

        missing = client.get("/api/nope")
        assert missing.status_code == 404
        assert missing.json()["code"] == "not_found"


def test_health_says_where_the_data_lives(core: CoreServices, tmp_path: Path) -> None:
    """The setup screen displays this path, so it has to be the one in use.

    It used to start from an empty box instead, so opening that screen and
    pressing next pointed the app at a fresh directory - and every conversation
    lives inside the directory, so they all appeared to vanish.
    """
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = core
    with TestClient(create_app(runtime)) as client:
        body = client.get("/api/health").json()

    assert body["configured"] is True
    assert body["data_directory"] == str(core.database.data_directory)


def test_health_says_there_is_no_directory_before_setup(tmp_path: Path) -> None:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    with TestClient(create_app(runtime)) as client:
        body = client.get("/api/health").json()

    assert body["configured"] is False
    assert body["data_directory"] is None


def test_setup_refuses_a_directory_that_is_not_there(tmp_path: Path) -> None:
    """A path that does not exist is not a data directory.

    Making one used to be the app's decision, so a typo produced a brand new
    directory with nothing in it - which looks exactly like a working setup whose
    conversations have all gone. Choosing a directory that is already there is
    the difference between the two.
    """
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    missing = tmp_path / "typo"
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/setup", json={"data_directory": str(missing)})

    assert response.status_code == 422
    assert "不存在" in response.json()["detail"]
    assert not missing.exists()
    assert runtime.configured is False


def test_setup_refuses_a_file(tmp_path: Path) -> None:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    notes = tmp_path / "notes.txt"
    notes.write_text("not a directory", encoding="utf-8")
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/setup", json={"data_directory": str(notes)})

    assert response.status_code == 422
    assert "不是目录" in response.json()["detail"]


def test_setup_takes_a_directory_that_exists(tmp_path: Path) -> None:
    """The half that must keep working: an existing directory is accepted."""
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    chosen = tmp_path / "data"
    chosen.mkdir()
    with TestClient(create_app(runtime)) as client:
        response = client.post("/api/setup", json={"data_directory": str(chosen)})
        body = client.get("/api/health").json()

    assert response.status_code == 200
    assert body["configured"] is True
    assert body["data_directory_error"] is None
    assert Path(body["data_directory"]) == chosen
    assert (chosen / "jarvis.sqlite3").exists()


def test_health_explains_a_data_directory_that_went_away(tmp_path: Path) -> None:
    """The directory is chosen, then disappears - an unmounted drive, say.

    The app used to recreate it and start with an empty database, which is
    indistinguishable from every conversation having been deleted. It now says
    which directory is missing, and leaves the disk alone.
    """
    bootstrap = BootstrapStore(tmp_path / "bootstrap")
    chosen = tmp_path / "data"
    chosen.mkdir()
    bootstrap.select_data_directory(str(chosen))
    shutil.rmtree(chosen)

    runtime = Runtime(bootstrap)
    with TestClient(create_app(runtime)) as client:
        body = client.get("/api/health").json()

    assert body["configured"] is False
    # Named rather than blank: the settings screen shows this, and an empty box
    # would ask the user to choose a directory they had already chosen.
    assert Path(body["data_directory"]) == chosen
    assert "不存在" in body["data_directory_error"]
    assert not chosen.exists()


def test_a_body_sent_without_a_content_type_is_told_so(client: TestClient) -> None:
    """A 422 is the answer; a 500 is not.

    The validation handler echoes the offending input, and for an unparsed body
    that input is the raw `bytes` - which `json.dumps` refuses, so the response
    meant to explain the problem failed to serialise and became a 500 with no
    explanation in it at all. This is exactly what a client whose headers got
    dropped by a proxy, or by a bug of its own, would have seen.
    """
    response = client.post(
        "/api/conversations",
        content=b'{"title": "x"}',
        headers={"Content-Type": "text/plain;charset=UTF-8"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "request_validation"
    assert payload["detail"]
