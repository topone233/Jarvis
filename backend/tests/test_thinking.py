from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.provider import OpenAICompatibleProvider, ProviderEvent
from app.schemas import ThinkingLevel
from app.secrets import InMemorySecretStore
from app.store import Store

# The four fields the provider sets itself, whatever the dial says. Everything
# else in a request body came from somewhere the user controls.
CORE = {"model", "messages", "stream", "stream_options"}

# A profile as the composer would leave it. `thinking_off` is the one fragment
# that still reaches the wire. `thinking_on` is read by nobody since the dial
# arrived, and it is deliberately loud here - a switch and a strength of its own -
# so that any test which lets it through fails on the value, not on a key name.
FRAGMENTS = {
    "thinking_on": {"enable_thinking": True, "reasoning_effort": "medium"},
    "thinking_off": {"enable_thinking": False},
}


def capturing_provider(sent: list[dict[str, Any]], *, stream: bool) -> OpenAICompatibleProvider:
    """A real provider whose HTTP call lands in a list instead of a network."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if stream:
            body = 'data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})

    return OpenAICompatibleProvider(InMemorySecretStore(), transport=httpx.MockTransport(handler))


async def send_streaming(
    provider: OpenAICompatibleProvider, profile: dict[str, Any], **kwargs: Any
) -> list[ProviderEvent]:
    """Run one streaming call to completion, events discarded."""
    return [
        event
        async for event in provider.stream_chat(
            profile, [{"role": "user", "content": "你好"}], **kwargs
        )
    ]


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("off", {"enable_thinking": False}),
        ("low", {"reasoning_effort": "low"}),
        ("high", {"reasoning_effort": "high"}),
        ("max", {"reasoning_effort": "max"}),
    ],
)
@pytest.mark.asyncio
async def test_the_four_positions_of_the_dial(
    profile: dict[str, Any], level: ThinkingLevel, expected: dict[str, Any]
) -> None:
    """One dial, four positions, and only one of them is the profile's business.

    "off" is the fragment the user wrote, because what to say to a provider that
    is not to think has no single spelling. The three strengths are this
    server's own name for it, so they send that name and nothing else - not even
    the profile's `thinking_on` fragment, which as written here would have said
    "medium" and turned on a switch of its own.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(provider, {**profile, **FRAGMENTS}, thinking=level)

    assert {key: value for key, value in sent[0].items() if key not in CORE} == expected


@pytest.mark.asyncio
async def test_a_call_that_names_no_position_gets_the_off_fragment(
    profile: dict[str, Any],
) -> None:
    """The default is off, and "off" is a thing the profile gets to describe.

    Nothing passes ``thinking`` here, which is exactly the situation of every
    caller that is not the composer: compaction, memory extraction, and any code
    written before the dial was one.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(provider, {**profile, **FRAGMENTS})

    assert {key: value for key, value in sent[0].items() if key not in CORE} == {
        "enable_thinking": False
    }


@pytest.mark.asyncio
async def test_a_blocking_call_carries_the_fragments_too(profile: dict[str, Any]) -> None:
    """Compaction and memory extraction go through complete_chat, not stream_chat.

    Both are internal callers that never think, so what they send is the off
    fragment - which is the whole point of the default.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=False)

    await provider.complete_chat({**profile, **FRAGMENTS}, [{"role": "user", "content": "你好"}])

    assert sent[0]["enable_thinking"] is False
    assert sent[0]["stream"] is False


@pytest.mark.asyncio
async def test_a_profile_with_nothing_to_say_adds_nothing_at_all(profile: dict[str, Any]) -> None:
    """Empty means "this endpoint needs no instructions", not "send empty keys"."""
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(provider, {**profile, "thinking_on": {}, "thinking_off": {}})

    assert set(sent[0]) == CORE


@pytest.mark.asyncio
async def test_a_strength_does_not_need_the_profile_to_say_anything(
    profile: dict[str, Any],
) -> None:
    """The three strengths are this server's spelling, not the profile's.

    Which is why the slider is drawn for every profile rather than only for the
    ones with a fragment in them: there is nothing here a profile could have
    failed to configure.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(
        provider, {**profile, "thinking_on": {}, "thinking_off": {}}, thinking="max"
    )

    assert sent[0]["reasoning_effort"] == "max"


@pytest.mark.asyncio
async def test_a_fragment_cannot_take_over_the_request(profile: dict[str, Any]) -> None:
    """What a fragment may change stops at the edges of the request.

    A profile describes a dialect; it does not get to pick which model answers,
    what it is asked, or whether the reply is streamed - all three of which would
    turn a settings field into something that silently redirects the app.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(
        provider,
        {
            **profile,
            "thinking_off": {
                "model": "somebody-elses-model",
                "messages": [],
                "stream": False,
                "stream_options": {},
            },
        },
    )

    assert sent[0]["model"] == profile["chat_model"]
    assert sent[0]["messages"] == [{"role": "user", "content": "你好"}]
    assert sent[0]["stream"] is True
    assert sent[0]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_a_dialect_that_is_not_a_flat_map_survives_unchanged(
    profile: dict[str, Any],
) -> None:
    """Nothing here knows what a fragment means, and that has to stay true.

    The three spellings in use today are a flat boolean, a flat string and a
    nested object, and a fourth will exist by the time anyone reads this.
    """
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)
    nested = {"thinking": {"type": "disabled", "budget_tokens": 0}}

    await send_streaming(provider, {**profile, "thinking_off": nested})

    assert sent[0]["thinking"] == {"type": "disabled", "budget_tokens": 0}


@pytest.mark.asyncio
async def test_the_model_the_composer_picked_is_the_one_asked(profile: dict[str, Any]) -> None:
    sent: list[dict[str, Any]] = []
    provider = capturing_provider(sent, stream=True)

    await send_streaming(provider, profile, chat_model="mock-chat-mini")
    await send_streaming(provider, profile)

    assert sent[0]["model"] == "mock-chat-mini"
    # Naming no model means the profile's own, which is what every internal
    # caller does and therefore has to keep working.
    assert sent[1]["model"] == profile["chat_model"]


def test_the_fragments_come_back_out_of_the_database_as_they_went_in(
    client: TestClient,
) -> None:
    """The stored form and the sent form are the same object.

    A round trip through JSON is the only place a fragment can quietly change
    shape, and it would change it for the user who typed it, not for this code.
    """
    created = client.post(
        "/api/model-profiles",
        json={
            "name": "带思考的模型",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            **FRAGMENTS,
            "is_default": True,
        },
    ).json()

    assert created["thinking_on"] == FRAGMENTS["thinking_on"]
    assert created["thinking_off"] == FRAGMENTS["thinking_off"]

    updated = client.patch(
        f"/api/model-profiles/{created['id']}",
        json={"thinking_off": {"thinking": {"type": "disabled"}}},
    ).json()

    assert updated["thinking_off"] == {"thinking": {"type": "disabled"}}
    # Editing one fragment leaves the other one where it was.
    assert updated["thinking_on"] == FRAGMENTS["thinking_on"]

    # And back out of the list, which is what the settings screen reads.
    listed = [
        item for item in client.get("/api/model-profiles").json() if item["id"] == created["id"]
    ]
    assert listed[0]["thinking_off"] == {"thinking": {"type": "disabled"}}


def test_the_composer_reads_the_model_list_from_the_service(
    client: TestClient, profile: dict[str, Any]
) -> None:
    models = client.get(f"/api/model-profiles/{profile['id']}/models").json()["models"]

    assert [model["id"] for model in models] == ["mock-chat", "mock-chat-mini"]


def test_an_empty_model_name_is_refused(client: TestClient, profile: dict[str, Any]) -> None:
    """A dropdown that came back empty must not become an empty model name.

    The provider would answer that with an error about a model it cannot find,
    which is a worse way to learn what happened than a 422 here.
    """
    conversation = client.post(
        "/api/conversations", json={"model_profile_id": profile["id"]}
    ).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "你好。", "chat_model": ""},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation"


def test_the_old_boolean_is_refused_by_name(client: TestClient, profile: dict[str, Any]) -> None:
    """A client from before the dial sends `true`, and has to be told so.

    Renaming the field would have been quieter and worse: pydantic drops fields
    it does not know about, so the run would have gone out with thinking off and
    nothing on screen to say why.
    """
    conversation = client.post(
        "/api/conversations", json={"model_profile_id": profile["id"]}
    ).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "你好。", "thinking": True},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation"


LEGACY_SCHEMA = """
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE model_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    protocol TEXT NOT NULL,
    chat_model TEXT NOT NULL,
    embedding_model TEXT,
    context_window INTEGER NOT NULL,
    output_token_reserve INTEGER NOT NULL,
    max_tokens INTEGER,
    compact_percent INTEGER NOT NULL DEFAULT 72,
    reasoning_levels_json TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    has_api_key INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
INSERT INTO schema_migrations(version, applied_at) VALUES (3, '2026-01-01T00:00:00Z');
INSERT INTO model_profiles(
    id, name, base_url, protocol, chat_model, context_window, output_token_reserve,
    reasoning_levels_json, is_default, created_at, updated_at
) VALUES (
    'p_legacy', '旧配置', 'http://mock.local/v1', 'chat_completions', 'mock-chat', 4096, 512,
    '["low","high"]', 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
);
"""


def test_an_older_database_gains_the_fragments_and_loses_the_levels(tmp_path: Path) -> None:
    """The two columns arrive, the one they replace goes, and rows keep working.

    The levels list is dropped rather than left in place because it never held
    anything - no client could set it - and a column that still exists is a
    column someone will later fill.
    """
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "jarvis.sqlite3") as connection:
        connection.executescript(LEGACY_SCHEMA)

    database = Database(data)
    database.initialize()

    columns = {row["name"] for row in database.fetchall("PRAGMA table_info(model_profiles)")}
    assert {"thinking_on_json", "thinking_off_json"} <= columns
    assert "reasoning_levels_json" not in columns

    profile = Store(database).get_model_profile("p_legacy")
    assert profile["thinking_on"] == {}
    assert profile["thinking_off"] == {}
    assert "reasoning_levels" not in profile
