"""The notepad plugin, through its mounted API.

These tests use the real plugin - it is the one the app ships - against a
tmp data directory. What they pin down is the file contract: a note is a
readable markdown file, an external edit (Typora, any editor) is picked up
and protected by the conflict check, deletion is a move into ``.deleted/``,
and the AI tagger is a background status, never a failed request.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

NOTES = "/api/plugins/notepad/notes"


def _notes_dir(core: Any) -> Path:
    return core.database.data_directory / "notes"


def test_a_created_note_is_a_readable_markdown_file(client: TestClient, core: Any) -> None:
    response = client.post(NOTES, json={"title": "部署笔记", "content": "# 标题\n\n正文内容"})
    assert response.status_code == 201
    note = response.json()

    files = list(_notes_dir(core).glob("*.md"))
    assert len(files) == 1
    assert files[0].name.startswith("部署笔记-")
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "title: 部署笔记" in text
    assert "# 标题" in text
    assert note["title"] == "部署笔记"
    assert note["tag_status"] == "failed"  # no default profile in this fixture


def test_an_empty_note_is_refused(client: TestClient) -> None:
    response = client.post(NOTES, json={"title": "  ", "content": "  "})
    assert response.status_code == 422


def test_list_search_and_tag_filtering(client: TestClient) -> None:
    client.post(NOTES, json={"title": "nginx 配置", "content": "反向代理的设置笔记"})
    client.post(NOTES, json={"title": "买菜清单", "content": "西红柿、鸡蛋"})

    listed = client.get(NOTES).json()["items"]
    assert [item["title"] for item in listed] == ["买菜清单", "nginx 配置"]

    hits = client.get(NOTES, params={"q": "代理"}).json()["items"]
    assert [item["title"] for item in hits] == ["nginx 配置"]

    # A single coincidental bigram from the query is not a hit: 买菜 shares
    # nothing with 服务, and the floor keeps it out.
    misses = client.get(NOTES, params={"q": "服务"}).json()["items"]
    assert misses == []


def test_tag_filter_and_tags_endpoint(client: TestClient, core: Any) -> None:
    note = client.post(NOTES, json={"title": "有标签的", "content": "内容"}).json()
    client.patch(f"{NOTES}/{note['id']}", json={"tags": ["部署", "nginx"]})

    tags = client.get("/api/plugins/notepad/tags").json()["items"]
    # Same count sorts by tag text; latin codepoints sort before CJK.
    assert tags == [{"tag": "nginx", "count": 1}, {"tag": "部署", "count": 1}]

    hits = client.get(NOTES, params={"tag": "部署"}).json()["items"]
    assert [item["id"] for item in hits] == [note["id"]]

    other = client.get(NOTES, params={"tag": "不存在"}).json()["items"]
    assert other == []


def test_an_external_edit_is_seen_and_protected(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = client.post(NOTES, json={"title": "外部编辑", "content": "第一版"}).json()

    # Typora's move: rewrite the file behind the app's back, and push the
    # mtime safely past the one-second tolerance.
    path = next(_notes_dir(core).glob("*.md"))
    path.write_text(
        path.read_text(encoding="utf-8").replace("第一版", "外部改过的第二版"),
        encoding="utf-8",
    )
    stamp = time.time() + 5
    os.utime(path, (stamp, stamp))

    seen = client.get(f"{NOTES}/{note['id']}").json()
    assert "外部改过的第二版" in seen["content"]
    assert seen["updated_at"] != note["updated_at"]  # raised to the mtime

    # The editor that loaded before the edit now saves: refused, not clobbered.
    stale = client.patch(
        f"{NOTES}/{note['id']}",
        json={"content": "编辑器里的第三版", "base_updated_at": note["updated_at"]},
    )
    assert stale.status_code == 409

    fresh = client.patch(
        f"{NOTES}/{note['id']}",
        json={"content": "编辑器里的第三版", "base_updated_at": seen["updated_at"]},
    )
    assert fresh.status_code == 200
    assert fresh.json()["content"] == "编辑器里的第三版"


def test_delete_restore_and_purge(client: TestClient, core: Any) -> None:
    note = client.post(NOTES, json={"title": "要删的", "content": "内容"}).json()

    assert client.delete(f"{NOTES}/{note['id']}").status_code == 204
    assert client.get(NOTES).json()["items"] == []
    deleted = client.get(f"{NOTES}/deleted").json()["items"]
    assert [item["id"] for item in deleted] == [note["id"]]

    restored = client.post(f"{NOTES}/{note['id']}/restore")
    assert restored.status_code == 200
    assert [item["id"] for item in client.get(NOTES).json()["items"]] == [note["id"]]

    client.delete(f"{NOTES}/{note['id']}")
    assert client.delete(f"{NOTES}/{note['id']}/purge").status_code == 204
    assert client.get(f"{NOTES}/deleted").json()["items"] == []
    assert list((_notes_dir(core) / ".deleted").glob("*.md")) == []


def test_manual_tags_win_over_a_pending_tagger(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tagger(monkeypatch, core, '["部署", "nginx"]', delay=0.2)
    profile = core.store.create_model_profile(
        {
            "name": "默认",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )
    assert profile["is_default"] is True

    note = client.post(NOTES, json={"title": "竞速", "content": "内容"}).json()
    assert note["tag_status"] == "pending"

    # The hand-set list lands while the background call is still sleeping.
    patched = client.patch(f"{NOTES}/{note['id']}", json={"tags": ["手工"]})
    assert patched.json()["tag_status"] == "done"

    deadline = time.time() + 5
    while time.time() < deadline:
        current = client.get(f"{NOTES}/{note['id']}").json()
        if current["tags"] == ["手工"] and current["tag_status"] == "done":
            break
        time.sleep(0.05)
    assert current["tags"] == ["手工"], "后台任务不得覆盖手动标签"


def test_the_tagger_tags_a_new_note(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen_prompts: list[list[dict[str, Any]]] = []

    async def reply(profile: dict[str, Any], messages: list[dict[str, Any]], **_: Any) -> str:
        seen_prompts.append(messages)
        return '好的，这是标签：["部署", "nginx", "反向代理"]'

    monkeypatch.setattr(core.provider, "complete_chat", reply)
    core.store.create_model_profile(
        {
            "name": "默认",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )

    note = client.post(NOTES, json={"title": "nginx 上游超时", "content": "proxy_read_timeout 60s;"}).json()
    assert note["tag_status"] == "pending"

    deadline = time.time() + 5
    current = note
    while time.time() < deadline:
        current = client.get(f"{NOTES}/{note['id']}").json()
        if current["tag_status"] == "done":
            break
        time.sleep(0.05)
    assert current["tag_status"] == "done"
    assert current["tags"] == ["部署", "nginx", "反向代理"]
    # The suggestion is metadata: the note's place in the list does not move.
    assert current["updated_at"] == note["updated_at"]
    assert "proxy_read_timeout" in seen_prompts[0][1]["content"]


def test_tagging_failure_is_a_status_and_retry_works(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(profile: dict[str, Any], messages: list[dict[str, Any]], **_: Any) -> str:
        raise RuntimeError("模型服务请求失败，未能完成生成。")

    monkeypatch.setattr(core.provider, "complete_chat", broken)
    core.store.create_model_profile(
        {
            "name": "默认",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )

    note = client.post(NOTES, json={"title": "会失败的", "content": "内容"}).json()
    deadline = time.time() + 5
    current = note
    while time.time() < deadline:
        current = client.get(f"{NOTES}/{note['id']}").json()
        if current["tag_status"] == "failed":
            break
        time.sleep(0.05)
    assert current["tag_status"] == "failed"

    # Retry with a working model finishes the job.
    _use_tagger(monkeypatch, core, '["恢复", "重试"]')
    retried = client.post(f"{NOTES}/{note['id']}/retag")
    assert retried.status_code == 200
    assert retried.json()["tags"] == ["恢复", "重试"]


def test_retag_without_a_profile_names_the_reason(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = client.post(NOTES, json={"title": "无模型", "content": "内容"}).json()
    response = client.post(f"{NOTES}/{note['id']}/retag")
    assert response.status_code == 422
    assert "默认模型" in response.json()["detail"]


def test_auto_tag_off_leaves_the_note_alone(
    client: TestClient, core: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[int] = []

    async def reply(profile: dict[str, Any], messages: list[dict[str, Any]], **_: Any) -> str:
        called.append(1)
        return '["不应出现"]'

    monkeypatch.setattr(core.provider, "complete_chat", reply)
    core.store.create_model_profile(
        {
            "name": "默认",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )
    client.put("/api/plugins/notepad/config", json={"settings": {"auto_tag": False}})

    note = client.post(NOTES, json={"title": "免打标", "content": "内容"}).json()
    assert note["tag_status"] == "none"
    time.sleep(0.2)
    assert called == []


def _use_tagger(
    monkeypatch: pytest.MonkeyPatch, core: Any, reply_text: str, *, delay: float = 0.0
) -> None:
    async def reply(profile: dict[str, Any], messages: list[dict[str, Any]], **_: Any) -> str:
        if delay:
            await asyncio.sleep(delay)
        return reply_text

    monkeypatch.setattr(core.provider, "complete_chat", reply)
