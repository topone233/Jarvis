from __future__ import annotations

from fastapi.testclient import TestClient


def test_project_conversation_and_streaming_run(client: TestClient) -> None:
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

    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "我喜欢简洁回答。", "reasoning_level": "low"},
    )

    assert response.status_code == 200
    assert "event: run.started" in response.text
    # A reply that finishes before the client attaches arrives whole in the
    # terminal event instead of as deltas; the client renders the same answer
    # either way, which is what makes reconnecting safe.
    assert "event: message.completed" in response.text
    assert "这是测试回复。" in response.text
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert messages[-1]["content"] == "这是测试回复。"
    memories = client.get("/api/memories").json()
    assert memories[0]["content"] == "喜欢简洁回答"


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
