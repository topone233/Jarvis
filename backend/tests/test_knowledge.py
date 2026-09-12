from __future__ import annotations

from typing import Any

import pytest

from app.knowledge import ImportItem
from app.runtime import CoreServices
from app.utils import segment_for_index


@pytest.mark.asyncio
async def test_import_and_hybrid_search(core: CoreServices, profile: dict[str, Any]) -> None:
    project = core.store.create_project("Jarvis", False)
    imported = await core.knowledge.import_items(
        [
            ImportItem(
                filename="architecture.md",
                content="Jarvis 使用 SQLite 保存本地知识，并支持上下文压缩。".encode(),
                relative_path="notes/architecture.md",
            )
        ],
        project_id=project["id"],
        profile=profile,
    )

    assert imported[-1]["status"] == "ready"
    document = imported[-1]["document"]
    assert document["chunk_count"] == 1

    results = await core.knowledge.search(
        "SQLite 上下文",
        project_id=project["id"],
        profile=profile,
    )

    assert results
    assert results[0]["document_id"] == document["id"]
    assert "SQLite" in results[0]["content"]


@pytest.mark.asyncio
async def test_chinese_query_matches_a_sub_phrase(core: CoreServices) -> None:
    """Keyword search must reach inside a CJK run, not only match whole runs."""
    project = core.store.create_project("中文检索", False)
    await core.knowledge.import_items(
        [
            ImportItem(
                filename="notes.md",
                content="Jarvis 使用 SQLite 保存本地知识，并支持上下文压缩。".encode(),
            )
        ],
        project_id=project["id"],
        profile=None,
    )

    for query in ("上下文", "压缩", "上下文压缩"):
        results = await core.knowledge.search(query, project_id=project["id"], profile=None)
        assert results, f"未命中：{query}"

    assert not await core.knowledge.search("记忆", project_id=project["id"], profile=None)


def test_segment_for_index_expands_cjk_runs_into_bigrams() -> None:
    assert segment_for_index("上下文") == "上下 下文"
    assert segment_for_index("记忆") == "记忆"
    assert segment_for_index("SQLite 上下文压缩") == "SQLite 上下 下文 文压 压缩"
    assert segment_for_index("plain english text") == "plain english text"


@pytest.mark.asyncio
async def test_import_never_writes_the_source_file(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="note.txt", content=b"original content")],
        project_id=None,
        profile=None,
    )
    document = imported[-1]["document"]

    assert document["stored_path"].startswith(str(core.database.objects_directory))
    assert core.database.objects_directory.joinpath(
        document["stored_path"].split("\\")[-1]
    ).exists()
