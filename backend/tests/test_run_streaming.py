from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.provider import ProviderEvent
from app.runs import RunChoice, RunService
from app.runtime import CoreServices
from app.schemas import ThinkingLevel


class PacedProvider:
    """A provider that stops partway so a test can inspect a run still going.

    It emits its whole script, then parks on ``release`` until the test lets it
    finish. ``gated`` says the script has been emitted.
    """

    def __init__(
        self, chunks: list[str], reasoning: list[str] | None = None, tail: list[str] | None = None
    ) -> None:
        self.chunks = chunks
        self.reasoning = reasoning or []
        self.tail = tail or []
        self.release = asyncio.Event()
        self.gated = asyncio.Event()

    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> AsyncIterator[ProviderEvent]:
        del profile, messages, chat_model, thinking
        for text in self.reasoning:
            yield ProviderEvent("reasoning", {"text": text})
        for chunk in self.chunks:
            yield ProviderEvent("delta", {"text": chunk})
        self.gated.set()
        await self.release.wait()
        for chunk in self.tail:
            yield ProviderEvent("delta", {"text": chunk})

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> str:
        del profile, messages, chat_model, thinking
        return "[]"

    async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile
        return [[float(len(text)), 1.0] for text in texts]


def _parse(sse: str) -> list[tuple[str, dict[str, Any]]]:
    """Every event fully received in ``sse``; a half-arrived block is ignored."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in sse.split("\n\n"):
        lines = [line for line in block.splitlines() if line]
        if len(lines) == 2:
            events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return events


def _answered(sse: str) -> str:
    """The answer text a client would have rendered from this stream so far."""
    return "".join(payload["delta"] for name, payload in _parse(sse) if name == "message.delta")


@pytest.fixture
def paced(core: CoreServices) -> Any:
    """Install a provider that stops partway, and let the test drive it."""

    def make(
        chunks: list[str], reasoning: list[str] | None = None, tail: list[str] | None = None
    ) -> PacedProvider:
        provider = PacedProvider(chunks, reasoning, tail)
        core.provider = provider  # type: ignore[assignment]
        core.memory.provider = provider  # type: ignore[assignment]
        return provider

    return make


def _begin(
    core: CoreServices, profile: dict[str, Any], content: str = "你好。"
) -> tuple[RunService, dict[str, Any]]:
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    service = RunService(core)
    run, run_profile, choice = service.start(
        conversation["id"], content=content, model_profile_id=None, choice=RunChoice()
    )
    service.launch(run, profile=run_profile, choice=choice)
    return service, run


async def _settle(core: CoreServices, run_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = core.store.get_run(run_id)
        if run["status"] not in {"running", "cancelling"}:
            return run
        await asyncio.sleep(0.01)
    raise AssertionError("这一轮没有在预期时间内结束。")


async def test_a_run_finishes_with_nobody_listening(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """The answer must not depend on a client staying connected.

    Nothing consumes the stream here, which is the state a browser leaves behind
    when it reloads mid-answer.
    """
    provider = paced(["第一段。", "第二段。"])
    _, run = _begin(core, profile)

    provider.release.set()
    done = await _settle(core, run["id"])

    assert done["status"] == "completed"
    assert core.store.get_message(run["assistant_message_id"])["content"] == "第一段。第二段。"


async def test_the_partial_answer_is_on_disk_before_the_run_ends(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """A crash at this moment costs nothing already checkpointed."""
    provider = paced(["甲" * 500])
    _, run = _begin(core, profile)
    await provider.gated.wait()

    assert core.store.get_message(run["assistant_message_id"])["content"] == "甲" * 500
    assert core.store.get_run(run["id"])["status"] == "running"

    provider.release.set()
    assert (await _settle(core, run["id"]))["status"] == "completed"


async def test_thinking_is_on_disk_before_the_run_ends(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """Reasoning arrives long before the answer text, and must persist too."""
    provider = paced(["回答。"], reasoning=["先想一下。" * 200])
    _, run = _begin(core, profile)
    await provider.gated.wait()

    metadata = core.store.get_message(run["assistant_message_id"])["metadata"]
    assert metadata["reasoning"] == "先想一下。" * 200

    provider.release.set()
    assert (await _settle(core, run["id"]))["status"] == "completed"


async def test_the_run_is_findable_before_a_single_token_arrives(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """A reload in the opening moment still finds the run.

    Until the first checkpoint writes content, the assistant message is empty.
    Without the run id on it there would be nothing for a client to attach to,
    so a reload would leave an empty bubble over a run that is producing fine.
    """
    provider = paced([])
    _, run = _begin(core, profile)

    message = core.store.get_message(run["assistant_message_id"])
    assert message["content"] == ""
    assert message["metadata"]["run_id"] == run["id"]

    provider.release.set()
    await _settle(core, run["id"])


async def test_regenerating_points_the_message_at_its_new_run(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """Regenerate clears the old answer, so the pointer has to be replaced.

    Clearing the metadata is what stops the model continuing from the reply
    being replaced; the new run id then has to go back on, or the same reload
    window reopens on every regeneration.
    """
    provider = paced(["第一段。"])
    _, run = _begin(core, profile)
    provider.release.set()
    await _settle(core, run["id"])

    new_run, _, _ = RunService(core).start_regenerate(
        run["assistant_message_id"], model_profile_id=None, choice=RunChoice()
    )

    message = core.store.get_message(run["assistant_message_id"])
    assert new_run["id"] != run["id"]
    assert message["content"] == ""
    assert message["metadata"]["run_id"] == new_run["id"]


async def test_a_subscriber_arriving_mid_answer_receives_all_of_it(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """Reconnecting must not lose the text produced while nobody watched.

    The late subscriber gets the backlog in its first flush and then carries on
    live, so the client renders one continuous answer.
    """
    provider = paced(["第一段。", "第二段。"])
    service, run = _begin(core, profile)

    early = service.follow(run["id"])
    backlog = ""
    while "第二段。" not in _answered(backlog):
        backlog += await anext(early)
    await early.aclose()

    late = service.follow(run["id"])
    caught_up = ""
    while not _answered(caught_up):
        caught_up += await anext(late)
    assert _answered(caught_up) == "第一段。第二段。"

    provider.release.set()
    rest = ""
    async for chunk in late:
        rest += chunk
    assert "message.completed" in [name for name, _ in _parse(rest)]


async def test_a_subscriber_does_not_see_text_twice(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """Catching up replays the backlog once, not once per publish."""
    provider = paced(["第一段。", "第二段。"])
    service, run = _begin(core, profile)

    stream = service.follow(run["id"])
    seen = ""
    while "第二段。" not in _answered(seen):
        seen += await anext(stream)

    provider.release.set()
    async for chunk in stream:
        seen += chunk

    assert _answered(seen) == "第一段。第二段。"


async def test_two_clients_watching_together_see_the_same_answer(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """Two tabs on one conversation must not steal events from each other."""
    provider = paced(["第一段。", "第二段。"])
    service, run = _begin(core, profile)

    async def watch() -> str:
        text = ""
        async for chunk in service.follow(run["id"]):
            text += chunk
        return text

    watchers = asyncio.gather(watch(), watch())
    await asyncio.sleep(0.05)
    provider.release.set()
    left, right = await watchers

    assert _answered(left) == "第一段。第二段。"
    assert _answered(right) == "第一段。第二段。"


async def test_stopping_a_live_run_keeps_what_it_had_already_written(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    provider = paced(["第一段。"], tail=["不会出现。"])
    service, run = _begin(core, profile)
    await provider.gated.wait()

    assert service.cancel(run["id"])["status"] == "cancelling"
    provider.release.set()

    assert (await _settle(core, run["id"]))["status"] == "cancelled"
    assert core.store.get_message(run["assistant_message_id"])["content"] == "第一段。"


async def test_stopping_a_finished_run_leaves_the_record_alone(
    core: CoreServices, profile: dict[str, Any], paced: Any
) -> None:
    """The stop button races the last token; losing that race must be harmless."""
    provider = paced(["回答。"])
    service, run = _begin(core, profile)
    provider.release.set()
    assert (await _settle(core, run["id"]))["status"] == "completed"

    assert service.cancel(run["id"])["status"] == "completed"

    # Nothing left flagged, so a later startup will not close it as interrupted.
    assert core.store.interrupt_orphaned_runs() == 0
    assert core.store.get_run(run["id"])["status"] == "completed"


def test_a_run_stranded_by_a_shutdown_is_reported_as_interrupted(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """Killing the process leaves a run 'running' with no writer left."""
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    user = core.store.append_message(conversation["id"], "user", "你好。")
    assistant = core.store.append_message(conversation["id"], "assistant", "", parent_id=user["id"])
    run = core.store.create_run(conversation["id"], user["id"], assistant["id"], profile["id"])

    assert core.store.interrupt_orphaned_runs() == 1

    repaired = core.store.get_run(run["id"])
    assert repaired["status"] == "interrupted"
    assert repaired["completed_at"] is not None
    assert repaired["error_message"]


@pytest.mark.parametrize("status", ["running", "cancelling"])
def test_both_in_flight_statuses_are_repaired(
    core: CoreServices, profile: dict[str, Any], status: str
) -> None:
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    user = core.store.append_message(conversation["id"], "user", "你好。")
    assistant = core.store.append_message(conversation["id"], "assistant", "", parent_id=user["id"])
    run = core.store.create_run(conversation["id"], user["id"], assistant["id"], profile["id"])
    core.store.update_run(run["id"], status=status)

    core.store.interrupt_orphaned_runs()

    assert core.store.get_run(run["id"])["status"] == "interrupted"


def test_a_finished_run_is_left_alone(core: CoreServices, profile: dict[str, Any]) -> None:
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    user = core.store.append_message(conversation["id"], "user", "你好。")
    assistant = core.store.append_message(conversation["id"], "assistant", "", parent_id=user["id"])
    run = core.store.create_run(conversation["id"], user["id"], assistant["id"], profile["id"])
    core.store.update_run(run["id"], status="completed", completed=True)

    assert core.store.interrupt_orphaned_runs() == 0
    assert core.store.get_run(run["id"])["status"] == "completed"


def test_reattaching_to_a_finished_run_replays_its_outcome(
    client: TestClient, profile: dict[str, Any]
) -> None:
    """A client asking about an old run gets the terminal event, not an error."""
    conversation = client.post(
        "/api/conversations", json={"model_profile_id": profile["id"]}
    ).json()
    client.post(f"/api/conversations/{conversation['id']}/runs", json={"content": "你好。"})
    answer = client.get(f"/api/conversations/{conversation['id']}/messages").json()[-1]
    run_id = answer["metadata"]["run_id"]

    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"

    replay = client.get(f"/api/runs/{run_id}/stream")

    assert replay.status_code == 200
    events = dict(_parse(replay.text))
    # No deltas: there is nothing left to stream, so the whole answer rides in
    # the terminal event and the client needs no special case for it. The steps
    # come too, or a reloaded page would show the answer with no sign of how it
    # was produced.
    assert list(events) == ["run.started", "audit", "message.completed"]
    assert events["message.completed"]["content"] == answer["content"]
