from __future__ import annotations

from typing import Any

import pytest

from app.runtime import CoreServices


@pytest.mark.asyncio
async def test_compaction_keeps_provenance(core: CoreServices, profile: dict[str, Any]) -> None:
    conversation = core.store.create_conversation("新对话", None, profile["id"], False)
    for index in range(8):
        core.store.append_message(
            conversation["id"],
            "user" if index % 2 == 0 else "assistant",
            f"第 {index} 轮：我们决定必须保留来源。" + "内容" * 450,
        )

    artifact = await core.context.compact(conversation["id"], profile, force=True)

    assert artifact is not None
    assert artifact["source_message_ids"]
    assert artifact["end_ordinal"] < 8
    assert "压缩" in artifact["content"]
    assert len(core.store.list_messages(conversation["id"])) == 8


@pytest.mark.asyncio
async def test_memory_supersedes_only_same_explicit_key(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    first = await core.memory.extract_and_store(
        profile=profile,
        user_content="我喜欢简洁回答。",
        user_message_id=core.store.append_message(
            core.store.create_conversation("新对话", None, profile["id"], False)["id"],
            "user",
            "我喜欢简洁回答。",
        )["id"],
        project_id=None,
    )
    second = await core.memory.extract_and_store(
        profile=profile,
        user_content="我的回复风格现在希望简洁。",
        user_message_id=core.store.append_message(
            core.store.create_conversation("另一个对话", None, profile["id"], False)["id"],
            "user",
            "我的回复风格现在希望简洁。",
        )["id"],
        project_id=None,
    )

    assert first[0]["action"] == "created"
    assert second[0]["action"] == "confirmed"
    memory = core.store.list_memories()[0]
    assert memory["confirmation_count"] == 2
