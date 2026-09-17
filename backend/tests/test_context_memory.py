from __future__ import annotations

import json
from typing import Any

import pytest

from app.errors import ProviderError
from app.prompts import PROMPT_VERSION
from app.runtime import CoreServices


def say(
    core: CoreServices, profile: dict[str, Any], content: str, project_id: str | None = None
) -> str:
    """A real user message to source a memory from.

    The source reference is foreign-keyed to the messages table, so a memory
    write has to point at a message that exists - which is exactly the
    production shape: the producer passes the just-stored user message's id.
    """
    conversation = core.store.create_conversation("对话", project_id, profile["id"], False)
    message = core.store.append_message(conversation["id"], "user", content)
    return message["id"]


def save_call(
    core: CoreServices,
    content: str,
    source_message_id: str,
    *,
    key: str = "键",
    kind: str = "fact",
    project_id: str | None = None,
) -> dict[str, Any]:
    """Write one memory the way a reply's tool call would."""
    results = core.memory.apply_tool_calls(
        [
            {
                "id": "call_1",
                "name": "save_memory",
                "arguments": json.dumps(
                    {"kind": kind, "key": key, "content": content}, ensure_ascii=False
                ),
            }
        ],
        user_content=content,
        user_message_id=source_message_id,
        project_id=project_id,
    )
    assert results and results[0]["action"] == "created"
    return results[0]["memory"]


def without_ranking(profile: dict[str, Any]) -> dict[str, Any]:
    """A profile with no embedding model: dedup runs, ranking falls back."""
    return {**profile, "embedding_model": None}


def vocab_provider(
    core: CoreServices, monkeypatch: pytest.MonkeyPatch, vocab: tuple[str, ...]
) -> None:
    """embed() as a one-hot bag of words: similarity = shared vocab words."""

    async def embed(profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile
        return [[1.0 if word in text else 0.0 for word in vocab] for text in texts]

    monkeypatch.setattr(core.context.provider, "embed", embed)


@pytest.mark.asyncio
async def test_a_call_confirms_the_same_fact_and_supersedes_a_changed_one(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    def call(content: str) -> dict[str, Any]:
        return {
            "id": "call_1",
            "name": "save_memory",
            "arguments": json.dumps(
                {"kind": "preference", "key": "回复风格", "content": content},
                ensure_ascii=False,
            ),
        }

    first = core.memory.apply_tool_calls(
        [call("喜欢简洁回答")],
        user_content="我喜欢简洁回答。",
        user_message_id=say(core, profile, "我喜欢简洁回答。"),
        project_id=None,
    )
    second = core.memory.apply_tool_calls(
        [call("喜欢简洁回答")],
        user_content="我说过我喜欢简洁。",
        user_message_id=say(core, profile, "我说过我喜欢简洁。"),
        project_id=None,
    )
    third = core.memory.apply_tool_calls(
        [call("喜欢详细回答")],
        user_content="现在想要详细的。",
        user_message_id=say(core, profile, "现在想要详细的。"),
        project_id=None,
    )

    # Each call returns the list of actions it carried out; one write each.
    assert [item["action"] for results in (first, second, third) for item in results] == [
        "created",
        "confirmed",
        "superseded",
    ]
    memories = core.store.list_memories()
    assert len(memories) == 1
    assert memories[0]["content"] == "喜欢详细回答"
    # The successor starts its own count; the confirmed one belonged to the
    # memory it replaced.
    assert memories[0]["confirmation_count"] == 1
    assert memories[0]["source_excerpt"] == "现在想要详细的。"


@pytest.mark.asyncio
async def test_calls_the_model_was_never_given_are_ignored(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """An unknown tool name or broken arguments is not a memory action."""
    result = core.memory.apply_tool_calls(
        [
            {"id": "call_1", "name": "delete_everything", "arguments": "{}"},
            {"id": "call_2", "name": "save_memory", "arguments": "这不是 JSON"},
            {
                "id": "call_3",
                "name": "save_memory",
                "arguments": json.dumps(
                    {"kind": "guess", "key": "键", "content": "内容"}, ensure_ascii=False
                ),
            },
            {
                "id": "call_4",
                "name": "save_memory",
                "arguments": json.dumps(
                    {"kind": "fact", "key": "", "content": "没有键"}, ensure_ascii=False
                ),
            },
        ],
        user_content="记一下。",
        user_message_id="msg_1",
        project_id=None,
    )

    assert result == []
    assert core.store.list_memories() == []


@pytest.mark.asyncio
async def test_a_forget_deletes_the_memory_its_call_names(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    save_call(core, "喜欢深色主题", say(core, profile, "记一下。"), key="主题", kind="preference")
    save_call(core, "项目用 React", say(core, profile, "记一下。"), key="编辑器", kind="fact")

    result = core.memory.apply_tool_calls(
        [
            {
                "id": "call_1",
                "name": "forget_memory",
                "arguments": json.dumps(
                    {"key": "主题", "content": "喜欢深色主题"}, ensure_ascii=False
                ),
            }
        ],
        user_content="忘掉我喜欢深色主题这件事。",
        user_message_id=say(core, profile, "忘掉我喜欢深色主题这件事。"),
        project_id=None,
    )

    assert [item["action"] for item in result] == ["forgotten"]
    remaining = core.store.list_memories()
    assert [memory["content"] for memory in remaining] == ["项目用 React"]
    # 软删除：行还在库里，只是不再激活，也没有进入回收站之外的地方。
    deleted = core.database.fetchone(
        "SELECT status, deleted_at FROM memories WHERE content = '喜欢深色主题'"
    )
    assert deleted is not None
    assert deleted["status"] == "deleted"
    assert deleted["deleted_at"] is not None


@pytest.mark.asyncio
async def test_a_forget_that_names_nothing_does_nothing(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """A key the model was never shown is a miss, not a deletion."""
    result = core.memory.apply_tool_calls(
        [
            {
                "id": "call_1",
                "name": "forget_memory",
                "arguments": json.dumps({"key": "不存在的键", "content": "内容"}),
            }
        ],
        user_content="忘掉。",
        user_message_id="msg_1",
        project_id=None,
    )

    assert result == []


@pytest.mark.asyncio
async def test_a_forget_stays_inside_its_own_visibility(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """A project conversation cannot forget a memory it was never shown.

    Two memories may share a key - a global one and a project one. The call's
    content is what picks between them; the key alone would be too broad.
    """
    save_call(core, "全局偏好", say(core, profile, "记一下。"), key="主题", kind="preference")
    project = core.store.create_project("项目", is_pinned=False)
    save_call(
        core,
        "项目偏好",
        say(core, profile, "记一下。", project_id=project["id"]),
        key="主题",
        kind="fact",
        project_id=project["id"],
    )

    result = core.memory.apply_tool_calls(
        [
            {
                "id": "call_1",
                "name": "forget_memory",
                "arguments": json.dumps({"key": "主题", "content": "项目偏好"}, ensure_ascii=False),
            }
        ],
        user_content="忘掉项目里的那条。",
        user_message_id=say(core, profile, "忘掉项目里的那条。", project_id=project["id"]),
        project_id=project["id"],
    )

    assert [item["action"] for item in result] == ["forgotten"]
    assert [memory["content"] for memory in core.store.list_memories()] == ["全局偏好"]


# --- Injection: dedup against what the model can already see ------------------


@pytest.mark.asyncio
async def test_a_memory_whose_source_is_still_visible_is_not_injected(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """The source message is in this turn's window; injecting again is a copy."""
    conversation = core.store.create_conversation("对话", None, profile["id"], False)
    message = core.store.append_message(conversation["id"], "user", "我喜欢简洁回答。")
    save_call(core, "喜欢简洁回答", message["id"], key="回复风格", kind="preference")
    core.store.append_message(conversation["id"], "user", "后来又说了点别的。")

    bundle = await core.context.build(conversation["id"], without_ranking(profile), "继续。")

    assert bundle.memories == []
    assert bundle.memory_mode == "fallback"


@pytest.mark.asyncio
async def test_a_memory_folded_into_the_artifact_is_not_injected(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """Once the compaction artifact has absorbed the source, it counts as present.

    The user chose the strict reading: the lossy summary stands in for the
    original, so the memory stays hidden even though no raw message shows it.
    """
    conversation = core.store.create_conversation("对话", None, profile["id"], False)
    message = core.store.append_message(conversation["id"], "user", "我喜欢简洁回答。")
    save_call(core, "喜欢简洁回答", message["id"], key="回复风格", kind="preference")
    core.store.create_context_artifact(
        conversation_id=conversation["id"],
        start_ordinal=1,
        end_ordinal=message["ordinal"],
        content="## 背景\n用户喜欢简洁回答。",
        decision_anchors=[],
        source_message_ids=[message["id"]],
        prompt_version=PROMPT_VERSION,
        token_estimate=10,
    )

    bundle = await core.context.build(conversation["id"], without_ranking(profile), "继续。")

    assert bundle.memories == []


@pytest.mark.asyncio
async def test_a_memory_whose_source_left_the_window_is_injected_again(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """Budget-dropped source: the memory is the fact's only carrier again.

    Neither visible (the newest message alone nearly fills the window) nor
    absorbed (there is no artifact) - so the dedup must let it through.
    """
    conversation = core.store.create_conversation("对话", None, profile["id"], False)
    message = core.store.append_message(conversation["id"], "user", "我喜欢简洁回答。")
    save_call(core, "喜欢简洁回答", message["id"], key="回复风格", kind="preference")
    core.store.append_message(conversation["id"], "user", "闲聊。" * 1200)
    tight = {**without_ranking(profile), "context_window": 900, "output_token_reserve": 0}

    bundle = await core.context.build(conversation["id"], tight, "继续。")

    assert [memory["content"] for memory in bundle.memories] == ["喜欢简洁回答"]
    assert bundle.memory_mode == "fallback"


@pytest.mark.asyncio
async def test_a_memory_from_another_conversation_is_injected(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """A foreign source is never visible here, so the memory always injects."""
    save_call(core, "喜欢简洁回答", say(core, profile, "我喜欢简洁回答。"), key="回复风格")
    other = core.store.create_conversation("别处", None, profile["id"], False)

    bundle = await core.context.build(other["id"], without_ranking(profile), "你好")

    assert [memory["content"] for memory in bundle.memories] == ["喜欢简洁回答"]


# --- Injection: relevance ranking ---------------------------------------------


@pytest.mark.asyncio
async def test_only_memories_relevant_to_the_query_are_injected(
    core: CoreServices, profile: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    vocab_provider(core, monkeypatch, ("苹果", "香蕉", "天气"))
    save_call(core, "用户最喜欢吃苹果", say(core, profile, "我最喜欢吃苹果。"), key="水果")
    save_call(core, "用户关心天气", say(core, profile, "我每天看天气预报。"), key="天气")
    conversation = core.store.create_conversation("对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "今晚买点苹果")

    assert [memory["content"] for memory in bundle.memories] == ["用户最喜欢吃苹果"]
    assert bundle.memory_mode == "relevance"


@pytest.mark.asyncio
async def test_a_turn_unrelated_to_every_memory_injects_none(
    core: CoreServices, profile: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user chose the strict reading: below the floor means not injected."""
    vocab_provider(core, monkeypatch, ("苹果", "香蕉", "天气"))
    save_call(core, "用户最喜欢吃苹果", say(core, profile, "我最喜欢吃苹果。"), key="水果")
    conversation = core.store.create_conversation("对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "讲个笑话")

    assert bundle.memories == []
    assert bundle.memory_mode == "relevance"


@pytest.mark.asyncio
async def test_a_forget_request_lists_everything(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """The forget flow copies entries from the 记忆 list, so nothing may hide.

    The visible-source memory would be deduped on an ordinary turn; a turn
    that means to forget must still name it. Ranking is skipped with it.
    """
    conversation = core.store.create_conversation("对话", None, profile["id"], False)
    message = core.store.append_message(conversation["id"], "user", "我喜欢简洁回答。")
    save_call(core, "喜欢简洁回答", message["id"], key="回复风格", kind="preference")
    save_call(core, "用户关心天气", say(core, profile, "我每天看天气预报。"), key="天气")

    bundle = await core.context.build(
        conversation["id"], without_ranking(profile), "忘掉之前说过的话"
    )

    assert sorted(memory["content"] for memory in bundle.memories) == [
        "喜欢简洁回答",
        "用户关心天气",
    ]
    assert bundle.memory_mode == "forget_bypass"


@pytest.mark.asyncio
async def test_a_failed_embed_falls_back_to_the_deduped_list(
    core: CoreServices, profile: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An embedding endpoint being down must not take the turn down with it."""

    async def broken(profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile, texts
        raise ProviderError("嵌入服务不可用。")

    monkeypatch.setattr(core.context.provider, "embed", broken)
    save_call(core, "喜欢简洁回答", say(core, profile, "我喜欢简洁回答。"), key="回复风格")
    conversation = core.store.create_conversation("对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    assert [memory["content"] for memory in bundle.memories] == ["喜欢简洁回答"]
    assert bundle.memory_mode == "fallback"


@pytest.mark.asyncio
async def test_a_stale_embedding_is_recomputed_under_the_current_model(
    core: CoreServices, profile: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    vocab_provider(core, monkeypatch, ("苹果", "香蕉", "天气"))
    memory = save_call(core, "用户最喜欢吃苹果", say(core, profile, "我最喜欢吃苹果。"), key="水果")
    core.store.update_memory_embedding(memory["id"], [0.5, 0.5, 0.5], "old-model")
    conversation = core.store.create_conversation("对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "今晚买点苹果")

    assert [item["content"] for item in bundle.memories] == ["用户最喜欢吃苹果"]
    row = core.database.fetchone(
        "SELECT embedding_json, embedding_model FROM memories WHERE id = ?", (memory["id"],)
    )
    assert row["embedding_model"] == profile["embedding_model"]
    assert json.loads(row["embedding_json"]) == [1.0, 0.0, 0.0]


# --- Injection: the retrieval cache -------------------------------------------


@pytest.mark.asyncio
async def test_editing_a_memory_drops_its_cached_vector(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    memory = save_call(core, "旧的内容", say(core, profile, "记一下。"), key="键")
    core.store.update_memory_embedding(memory["id"], [1.0, 0.0], profile["embedding_model"])

    core.store.update_memory(memory["id"], {"content": "新的内容"})

    row = core.database.fetchone(
        "SELECT embedding_json, embedding_model FROM memories WHERE id = ?", (memory["id"],)
    )
    assert row["embedding_json"] is None
    assert row["embedding_model"] is None


@pytest.mark.asyncio
async def test_one_embed_call_serves_memories_and_knowledge(
    core: CoreServices, profile: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The query is embedded once per turn and shared, not once per consumer."""
    calls: list[list[str]] = []
    original = core.provider.embed

    async def counting(profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return await original(profile, texts)

    monkeypatch.setattr(core.provider, "embed", counting)
    save_call(core, "喜欢简洁回答", say(core, profile, "我喜欢简洁回答。"), key="回复风格")
    conversation = core.store.create_conversation("对话", None, profile["id"], False)

    bundle = await core.context.build(conversation["id"], profile, "你好")

    assert len(calls) == 1
    assert calls[0][0] == "你好"
    assert [memory["content"] for memory in bundle.memories] == ["喜欢简洁回答"]
    assert bundle.memory_mode == "relevance"


def test_memories_table_has_embedding_cache_columns(core: CoreServices) -> None:
    columns = {row["name"] for row in core.database.fetchall("PRAGMA table_info(memories)")}
    assert {"embedding_json", "embedding_model"} <= columns
