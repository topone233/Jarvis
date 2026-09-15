"""Pasted images: from data URL to file to provider payload and back out.

The pieces that matter are the joins, not the bytes: an invalid image fails
before any row exists, the stored message names its files, the provider
receives the multimodal shape (and the token estimate says what it cost), the
serving endpoint answers only for the message that owns the image, and
deleting the conversation deletes the files with it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.errors import ValidationError
from app.images import MAX_IMAGE_BYTES, parse_images, write_images
from app.runtime import CoreServices
from app.tokens import IMAGE_TOKEN_ESTIMATE, estimate_tokens

# A real 1x1 PNG, small enough to paste into every test that needs one.
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_a_data_url_that_is_not_an_image_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_images(["data:text/html;base64,PGI+aGk8L2I+"])


def test_corrupt_base64_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_images(["data:image/png;base64,不!!"])


def test_an_oversized_image_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_images([f"data:image/png;base64,{'A' * (MAX_IMAGE_BYTES + 1)}"])


def test_an_image_run_stores_the_file_and_names_it_in_the_message(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    conversation = client.post("/api/conversations", json={}).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "", "images": [TINY_PNG]},
    )
    assert response.status_code == 200

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    user = next(message for message in messages if message["role"] == "user")
    filenames = user["metadata"]["images"]
    assert len(filenames) == 1
    stored = core.store.database.objects_directory / filenames[0]
    assert stored.is_file()
    assert stored.read_bytes().startswith(b"\x89PNG")


def test_an_empty_message_with_no_images_is_refused(client: TestClient) -> None:
    response = client.post("/api/conversations/whatever/runs", json={"content": "   "})

    assert response.status_code == 422


def test_an_image_without_text_still_gets_a_conversation_title(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = client.post("/api/conversations", json={}).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "", "images": [TINY_PNG]},
    )
    assert response.status_code == 200

    named = client.get(f"/api/conversations/{conversation['id']}").json()
    assert named["title"] != "新对话"


def test_the_image_endpoint_serves_only_the_message_that_owns_it(
    client: TestClient, profile: dict[str, Any]
) -> None:
    conversation = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "看看这个", "images": [TINY_PNG]},
    )
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    user = next(message for message in messages if message["role"] == "user")

    served = client.get(f"/api/conversations/{conversation['id']}/messages/{user['id']}/images/0")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"

    assert (
        client.get(
            f"/api/conversations/{conversation['id']}/messages/{user['id']}/images/1"
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/conversations/{'0' * 32}/messages/{user['id']}/images/0").status_code
        == 404
    )


@pytest.mark.asyncio
async def test_the_provider_receives_the_multimodal_shape_and_the_estimate_counts_it(
    core: CoreServices, profile: dict[str, Any]
) -> None:
    class FakeEmbedder:
        async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
            del profile
            return [[float(len(text)), 1.0] for text in texts]

    core.provider = FakeEmbedder()  # type: ignore[assignment]
    core.knowledge.provider = FakeEmbedder()  # type: ignore[assignment]
    with_images = core.store.create_conversation("有图", None, profile["id"], False)
    without = core.store.create_conversation("无图", None, profile["id"], False)
    filenames = write_images(core.store.database, "msg-img-1", [("image/png", b"pngbytes")])
    core.store.append_message(
        with_images["id"],
        "user",
        "看看这个",
        metadata={"images": filenames},
        message_id="msg-img-1",
    )
    core.store.append_message(without["id"], "user", "看看这个")

    bundle = await core.context.build(with_images["id"], profile, "看看这个")
    plain = await core.context.build(without["id"], profile, "看看这个")

    user_payload = next(m for m in bundle.messages if m["role"] == "user")
    assert isinstance(user_payload["content"], list)
    assert user_payload["content"][0] == {"type": "text", "text": "看看这个"}
    assert user_payload["content"][1]["type"] == "image_url"
    assert user_payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

    # One image costs the constant and nothing else: the two conversations are
    # identical except for it, so the whole difference is the image.
    assert bundle.input_token_estimate - plain.input_token_estimate == IMAGE_TOKEN_ESTIMATE


def test_the_estimate_constant_matches_the_frontend_arithmetic() -> None:
    # The composer's ring counts the same way the backend's compaction does;
    # the two constants drifting apart would make the two budgets disagree.
    text = "一" * 10
    assert estimate_tokens(text) == 12


def test_deleting_the_conversation_deletes_its_images(
    client: TestClient, core: CoreServices, profile: dict[str, Any]
) -> None:
    conversation = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conversation['id']}/runs",
        json={"content": "", "images": [TINY_PNG]},
    )
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    user = next(message for message in messages if message["role"] == "user")
    stored = core.store.database.objects_directory / user["metadata"]["images"][0]
    assert stored.is_file()

    client.delete(f"/api/conversations/{conversation['id']}")

    assert not stored.exists()
