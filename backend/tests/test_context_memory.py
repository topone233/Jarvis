from __future__ import annotations

from typing import Any

import pytest

from app.memory import parse_memory_block, split_memory_block
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


def test_a_reply_without_a_block_costs_nothing() -> None:
    """The common case: no block, nothing to parse, nothing to apply."""
    assert split_memory_block("普通回答。") == ("普通回答。", None)
    assert split_memory_block("```python\nprint(1)\n```") == ("```python\nprint(1)\n```", None)


def test_the_block_is_recognized_only_at_the_very_end() -> None:
    """A mid-answer fence is the answer's own content, not a memory block."""
    text = '前面```memory\n{"write":[]}\n后面。'
    assert split_memory_block(text) == (text, None)


def test_an_open_block_is_held_back_while_streaming() -> None:
    """The fence needs no closing mark, so a half-written blob stays hidden."""
    visible, body = split_memory_block('回答。\n```memory\n{"write": [{"kind":')
    assert visible == "回答。\n"
    assert body == '{"write": [{"kind":'


def test_a_closed_block_is_split_from_the_reply() -> None:
    visible, body = split_memory_block('回答。\n```memory\n{"write": []}\n```\n')
    assert visible == "回答。\n"
    assert body == '{"write": []}\n```\n'


def test_the_parser_takes_the_object_and_leaves_the_prose() -> None:
    parsed = parse_memory_block('说明文字 {"write": [], "forget": []} 结尾\n```')
    assert parsed == {"write": [], "forget": []}
    assert parse_memory_block("这不是 JSON") == {}
    assert parse_memory_block("[1, 2, 3]") == {}


@pytest.mark.asyncio
async def test_a_write_confirms_the_same_fact_and_supersedes_a_changed_one(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    def reply(write: str) -> str:
        return f'好的。\n```memory\n{{"write": [{write}]}}\n```\n'

    first = core.memory.apply_reply(
        reply=reply(
            '{"kind": "preference", "key": "回复风格", "content": "喜欢简洁回答",'
            ' "confidence": 0.9}'
        ),
        user_content="我喜欢简洁回答。",
        user_message_id=say(core, profile, "我喜欢简洁回答。"),
        project_id=None,
    )
    second = core.memory.apply_reply(
        reply=reply('{"kind": "preference", "key": "回复风格", "content": "喜欢简洁回答"}'),
        user_content="我说过我喜欢简洁。",
        user_message_id=say(core, profile, "我说过我喜欢简洁。"),
        project_id=None,
    )
    third = core.memory.apply_reply(
        reply=reply('{"kind": "preference", "key": "回复风格", "content": "喜欢详细回答"}'),
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
async def test_invalid_entries_are_dropped_silently(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    """An unknown kind or a missing key is not a memory, whatever it calls itself."""
    result = core.memory.apply_reply(
        reply=(
            '```memory\n{"write": ['
            '{"kind": "guess", "key": "键", "content": "内容"},'
            '{"kind": "fact", "key": "", "content": "没有键"}]}\n```'
        ),
        user_content="记一下。",
        user_message_id="msg_1",
        project_id=None,
    )

    assert result == []
    assert core.store.list_memories() == []


@pytest.mark.asyncio
async def test_a_forget_deletes_the_memory_its_entry_names(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    core.memory.apply_reply(
        reply=(
            '```memory\n{"write": ['
            '{"kind": "preference", "key": "主题", "content": "喜欢深色主题"},'
            '{"kind": "fact", "key": "编辑器", "content": "项目用 React"}]}\n```'
        ),
        user_content="记一下。",
        user_message_id=say(core, profile, "记一下。"),
        project_id=None,
    )

    result = core.memory.apply_reply(
        reply='```memory\n{"forget": [{"key": "主题", "content": "喜欢深色主题"}]}\n```',
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
    result = core.memory.apply_reply(
        reply='```memory\n{"forget": [{"key": "不存在的键", "content": "内容"}]}\n```',
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

    Two memories may share a key - a global one and a project one. The entry's
    content is what picks between them; the key alone would be too broad.
    """
    core.memory.apply_reply(
        reply=(
            '```memory\n{"write": ['
            '{"kind": "preference", "key": "主题", "content": "全局偏好"}]}\n```'
        ),
        user_content="记一下。",
        user_message_id=say(core, profile, "记一下。"),
        project_id=None,
    )
    project = core.store.create_project("项目", is_pinned=False)
    core.memory.apply_reply(
        reply=(
            '```memory\n{"write": [{"kind": "fact", "key": "主题", "content": "项目偏好"}]}\n```'
        ),
        user_content="记一下。",
        user_message_id=say(core, profile, "记一下。", project_id=project["id"]),
        project_id=project["id"],
    )

    result = core.memory.apply_reply(
        reply='```memory\n{"forget": [{"key": "主题", "content": "项目偏好"}]}\n```',
        user_content="忘掉项目里的那条。",
        user_message_id=say(core, profile, "忘掉项目里的那条。", project_id=project["id"]),
        project_id=project["id"],
    )

    assert [item["action"] for item in result] == ["forgotten"]
    assert [memory["content"] for memory in core.store.list_memories()] == ["全局偏好"]
