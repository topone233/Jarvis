"""What a user can change without editing the source.

Three prompts and two call parameters, plus the migration that carries the new
columns into a database that was created before they existed. The point of each
test here is not that a value round-trips through the API, but that the value
reaches the thing it is supposed to change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.prompts import BASE_INSTRUCTION, MEMORY_MANAGEMENT_INSTRUCTION
from app.provider import OpenAICompatibleProvider
from app.runtime import CoreServices
from app.schemas import ThinkingLevel
from app.secrets import InMemorySecretStore
from app.settings import COMPACT_PERCENT_DEFAULT, QUICK_PROMPTS_DEFAULT
from app.store import Store


class RecordingProvider:
    """Keeps the system message of everything it was asked, then says nothing.

    What a prompt actually is only shows up in what the model is handed, so the
    assertions are on the captured message rather than on the stored row.
    """

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> str:
        del profile, chat_model, thinking
        self.systems.append(messages[0]["content"])
        return "[]"


def test_every_prompt_starts_as_the_one_the_code_ships(client: TestClient) -> None:
    prompts = client.get("/api/settings").json()["prompts"]

    assert set(prompts) == {"system_prompt", "compaction_prompt", "memory_prompt"}
    for entry in prompts.values():
        assert entry["is_default"] is True
        assert entry["text"] == entry["default_text"]
    assert prompts["system_prompt"]["text"] == BASE_INSTRUCTION


def test_a_saved_prompt_replaces_the_text_in_force(client: TestClient) -> None:
    client.put("/api/settings", json={"system_prompt": "你是测试助手。"})

    prompts = client.get("/api/settings").json()["prompts"]

    assert prompts["system_prompt"]["text"] == "你是测试助手。"
    assert prompts["system_prompt"]["is_default"] is False
    # The built-in text is still known, which is what makes "restore" able to
    # show what it is about to put back.
    assert prompts["system_prompt"]["default_text"] == BASE_INSTRUCTION
    # Saving one prompt leaves the other two alone.
    assert prompts["compaction_prompt"]["is_default"] is True
    assert prompts["memory_prompt"]["is_default"] is True


def test_null_puts_a_prompt_back_to_the_built_in_text(client: TestClient) -> None:
    client.put("/api/settings", json={"compaction_prompt": "改写过的压缩提示词"})
    client.put("/api/settings", json={"compaction_prompt": None})

    entry = client.get("/api/settings").json()["prompts"]["compaction_prompt"]

    assert entry["is_default"] is True
    assert entry["text"] == entry["default_text"]


def test_quick_prompts_start_as_the_built_in_pair(client: TestClient) -> None:
    entry = client.get("/api/settings").json()["quick_prompts"]

    assert entry["items"] == QUICK_PROMPTS_DEFAULT
    assert entry["is_default"] is True


def test_a_saved_list_replaces_the_buttons_wholesale(client: TestClient) -> None:
    items = [{"name": "翻译", "prompt": "帮我把下面的话翻译成英文："}]
    client.put("/api/settings", json={"quick_prompts": items})

    overview = client.get("/api/settings").json()

    assert overview["quick_prompts"]["items"] == items
    assert overview["quick_prompts"]["is_default"] is False
    # Saving the list leaves the three prompts alone.
    assert overview["prompts"]["system_prompt"]["is_default"] is True


def test_an_empty_list_is_stored_as_empty_rather_than_reverting(client: TestClient) -> None:
    # No buttons is a real choice, and it has to survive a reload; the built-in
    # pair comes back only through null, which is what 恢复默认 sends.
    client.put("/api/settings", json={"quick_prompts": []})

    entry = client.get("/api/settings").json()["quick_prompts"]

    assert entry["items"] == []
    assert entry["is_default"] is False


def test_null_puts_the_built_in_pair_back(client: TestClient) -> None:
    client.put("/api/settings", json={"quick_prompts": [{"name": "翻译", "prompt": "翻译："}]})
    client.put("/api/settings", json={"quick_prompts": None})

    entry = client.get("/api/settings").json()["quick_prompts"]

    assert entry["items"] == QUICK_PROMPTS_DEFAULT
    assert entry["is_default"] is True


def test_a_quick_prompt_outside_its_shape_is_refused(client: TestClient) -> None:
    for bad in (
        [{"name": "", "prompt": "有内容"}],
        [{"name": "有名称", "prompt": ""}],
        [{"name": "太长" * 26, "prompt": "有内容"}],
        [{"name": "太长", "prompt": "x" * 2_001}],
    ):
        response = client.put("/api/settings", json={"quick_prompts": bad})
        assert response.status_code == 422

    thirteen = [{"name": f"第{index}条", "prompt": "内容"} for index in range(13)]
    assert client.put("/api/settings", json={"quick_prompts": thirteen}).status_code == 422


@pytest.mark.asyncio
async def test_the_saved_system_prompt_is_the_one_the_model_is_given(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    core.store.set_setting("system_prompt", "你是测试助手。")
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    assert bundle.messages[0]["role"] == "system"
    # The saved text opens the instruction; the memory directive may close it,
    # which the next test pins exactly.
    assert bundle.messages[0]["content"].startswith("你是测试助手。")


@pytest.mark.asyncio
async def test_a_cleared_system_prompt_sends_only_the_memory_directive(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """A cleared persona removes what the user wrote, not how the app remembers.

    An empty system message is not how to carry a cleared prompt out - an
    endpoint may reject one outright - but the memory directive is
    infrastructure, and it rides in the system message on its own.
    """
    core.store.set_setting("system_prompt", "")
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    system = [message for message in bundle.messages if message["role"] == "system"]
    assert len(system) == 1
    assert system[0]["content"] == MEMORY_MANAGEMENT_INSTRUCTION


@pytest.mark.asyncio
async def test_a_cleared_memory_prompt_sends_no_system_message(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """With nothing to say at all, nothing is sent - that is the choice honored."""
    core.store.set_setting("system_prompt", "")
    core.store.set_setting("memory_prompt", "")
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    assert all(message["role"] != "system" for message in bundle.messages)


@pytest.mark.asyncio
async def test_the_saved_memory_prompt_is_the_directive_the_context_uses(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    core.store.set_setting("memory_prompt", "自定义记忆指令。")
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    system = next(message for message in bundle.messages if message["role"] == "system")
    # The directive closes the assembled instruction: the model reads the
    # <memory> list and then, right before the conversation, how to act on it.
    assert system["content"].endswith("自定义记忆指令。")


@pytest.mark.asyncio
async def test_the_saved_compaction_prompt_is_the_one_the_summary_uses(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    recorder = RecordingProvider()
    core.context.provider = recorder  # type: ignore[assignment]
    core.store.set_setting("compaction_prompt", "改写过的压缩提示词")
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    for index in range(8):
        core.store.append_message(conversation["id"], "user", f"第 {index} 轮" + "内容" * 60)

    await core.context.compact(conversation["id"], profile, force=True)

    assert recorder.systems == ["改写过的压缩提示词"]


def capturing_provider(sent: list[dict[str, Any]], *, stream: bool) -> OpenAICompatibleProvider:
    """A real provider whose HTTP call lands in a list instead of a network."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if stream:
            body = 'data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})

    return OpenAICompatibleProvider(InMemorySecretStore(), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_max_tokens_travels_on_a_streaming_call(profile: dict[str, Any]) -> None:
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    events = [
        event
        async for event in provider.stream_chat(
            {**profile, "max_tokens": 512}, [{"role": "user", "content": "你好"}]
        )
    ]

    assert sent[0]["max_tokens"] == 512
    assert [event.kind for event in events] == ["delta", "done"]


@pytest.mark.asyncio
async def test_max_tokens_travels_on_a_blocking_call(profile: dict[str, Any]) -> None:
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=False)

    await provider.complete_chat(
        {**profile, "max_tokens": 512}, [{"role": "user", "content": "你好"}]
    )

    assert sent[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_no_limit_is_sent_when_none_was_configured(profile: dict[str, Any]) -> None:
    # Not a large number and not the window size: the endpoint has a limit of
    # its own, and it knows the model better than this code does.
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    async for _ in provider.stream_chat(
        {**profile, "max_tokens": None}, [{"role": "user", "content": "你好"}]
    ):
        pass

    assert "max_tokens" not in sent[0]


@pytest.mark.asyncio
async def test_the_compact_percent_decides_when_a_conversation_is_compacted(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    for index in range(8):
        core.store.append_message(conversation["id"], "user", f"第 {index} 轮" + "内容" * 150)
    # Roughly 2_900 estimated tokens against a budget of 4096 - 512 = 3584. Both
    # thresholds are clamped at 2_000, so the low one really is below the text
    # and the high one really is above it - the pair tests the percentage rather
    # than the floor that keeps it from firing on every short conversation.
    assert (
        await core.context.maybe_compact(conversation["id"], {**profile, "compact_percent": 95})
        is None
    )

    artifact = await core.context.maybe_compact(
        conversation["id"], {**profile, "compact_percent": 10}
    )

    assert artifact is not None
    assert artifact["end_ordinal"] < 8


def test_a_database_from_before_this_change_gains_the_call_parameters(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    database = Database(data)
    database.initialize()
    store = Store(database)
    profile = store.create_model_profile(
        {
            "name": "旧的模型",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )
    # Back to the shape it had before the two columns existed.
    with database.transaction() as connection:
        connection.execute("ALTER TABLE model_profiles DROP COLUMN max_tokens")
        connection.execute("ALTER TABLE model_profiles DROP COLUMN compact_percent")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 3")

    database.initialize()

    migrated = store.get_model_profile(profile["id"])
    assert migrated["max_tokens"] is None
    assert migrated["compact_percent"] == COMPACT_PERCENT_DEFAULT


def test_a_cleared_max_tokens_is_stored_as_no_limit(
    client: TestClient, profile: dict[str, Any]
) -> None:
    created = client.post(
        "/api/model-profiles",
        json={
            "name": "带上限的",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "max_tokens": 800,
        },
    ).json()
    assert created["max_tokens"] == 800

    cleared = client.patch(f"/api/model-profiles/{created['id']}", json={"max_tokens": None}).json()

    assert cleared["max_tokens"] is None


def test_a_call_parameter_outside_its_range_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": "越界的",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "compact_percent": 100,
        },
    )

    assert response.status_code == 422


def test_a_profile_that_never_set_the_new_parameters_gets_the_defaults(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/model-profiles",
        json={
            "name": "默认参数",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
        },
    ).json()

    assert created["max_tokens"] is None
    assert created["compact_percent"] == COMPACT_PERCENT_DEFAULT
