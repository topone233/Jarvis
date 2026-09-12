from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.runtime import CoreServices

REPLY = "这是测试回复。"


def _open_conversation(client: TestClient, profile: dict[str, Any]) -> dict[str, Any]:
    return client.post("/api/conversations", json={"model_profile_id": profile["id"]}).json()


def _messages(client: TestClient, conversation_id: str) -> list[dict[str, Any]]:
    return client.get(f"/api/conversations/{conversation_id}/messages").json()


def _run(client: TestClient, conversation_id: str, content: str) -> None:
    response = client.post(f"/api/conversations/{conversation_id}/runs", json={"content": content})
    assert response.status_code == 200


def test_delete_message_hides_it_and_files_it_in_the_trash(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "你好。")
    before = _messages(client, conversation["id"])

    response = client.delete(f"/api/messages/{before[-1]['id']}")

    assert response.status_code == 204
    assert [item["id"] for item in _messages(client, conversation["id"])] == [before[0]["id"]]
    assert any(item["entity_type"] == "message" for item in client.get("/api/trash").json())


def test_regenerate_replaces_the_reply_in_place(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "你好。")
    before = _messages(client, conversation["id"])
    reply = before[-1]

    response = client.post(f"/api/messages/{reply['id']}/regenerate")

    assert response.status_code == 200
    assert "event: run.started" in response.text
    assert "event: message.completed" in response.text
    after = _messages(client, conversation["id"])
    assert len(after) == len(before)
    assert after[-1]["id"] == reply["id"]
    assert after[-1]["content"] == REPLY
    # The superseded attempt stays on record for the audit trail.
    runs = core.database.fetchall("SELECT id FROM assistant_runs")
    assert len(runs) == 2


def test_regenerate_accepts_an_explicit_model_selection(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "你好。")
    reply = _messages(client, conversation["id"])[-1]

    response = client.post(
        f"/api/messages/{reply['id']}/regenerate",
        json={"model_profile_id": profile["id"], "reasoning_level": "high"},
    )

    assert response.status_code == 200
    assert _messages(client, conversation["id"])[-1]["content"] == REPLY


def test_regenerate_rejects_a_reply_that_is_not_the_latest(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "第一问。")
    first_reply = _messages(client, conversation["id"])[-1]
    _run(client, conversation["id"], "第二问。")

    response = client.post(f"/api/messages/{first_reply['id']}/regenerate")

    assert response.status_code == 422
    assert response.json()["code"] == "domain_validation"
    assert _messages(client, conversation["id"])[-1]["content"] == REPLY


def test_regenerate_rejects_a_user_message(client: TestClient, profile: dict[str, Any]) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "你好。")
    question = _messages(client, conversation["id"])[0]

    response = client.post(f"/api/messages/{question['id']}/regenerate")

    assert response.status_code == 422
    assert response.json()["code"] == "domain_validation"


def test_deleting_a_message_leaves_the_rest_of_the_conversation_intact(
    client: TestClient, profile: dict[str, Any]
) -> None:
    """Messages are deleted independently, so removing a turn is purely local.

    Deleting a question must not touch its answer, and must not renumber,
    reorder, or hide the turns around it. Nothing downstream reads ordinals as a
    contiguous sequence, so the resulting gap is harmless.
    """
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "第一问。")
    first = _messages(client, conversation["id"])
    _run(client, conversation["id"], "第二问。")
    second = _messages(client, conversation["id"])

    response = client.delete(f"/api/messages/{first[0]['id']}")

    assert response.status_code == 204
    remaining = _messages(client, conversation["id"])
    assert [message["id"] for message in remaining] == [
        first[1]["id"],
        second[2]["id"],
        second[3]["id"],
    ]
    assert [message["content"] for message in remaining] == [REPLY, "第二问。", REPLY]


def test_deleting_an_answer_leaves_its_question_in_place(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = _open_conversation(client, profile)
    _run(client, conversation["id"], "你好。")
    question, answer = _messages(client, conversation["id"])

    client.delete(f"/api/messages/{answer['id']}")

    assert [message["id"] for message in _messages(client, conversation["id"])] == [question["id"]]
