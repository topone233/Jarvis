"""Retrieval model config: the settings API, the migration, and the rerank stage.

The embedding and rerank models are one global config each - endpoint, model
name, and a keyring key - deliberately apart from the chat profiles. These
tests cover the wire contract (absent kind untouched, null kind cleared,
empty-string key deleted), the column-gated migration that moves an old
profile's embedding model into the new home, and the rerank stage: reorder
when it works, degrade to the mixed order when it does not.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FakeProvider
from fastapi.testclient import TestClient

from app.database import Database
from app.errors import ProviderError
from app.knowledge import ImportItem
from app.runtime import CoreServices
from app.secrets import InMemorySecretStore
from app.store import Store

EMBEDDING = {"base_url": "http://emb.local/v1", "model": "emb-1"}
RERANK = {"base_url": "http://rr.local/v1", "model": "rr-1"}


# --- the settings API ----------------------------------------------------------


def test_settings_start_empty(client: TestClient) -> None:
    response = client.get("/api/retrieval-settings")
    assert response.status_code == 200
    assert response.json() == {"embedding": None, "rerank": None}


def test_put_saves_both_kinds_normalizes_and_reports_keys(client: TestClient) -> None:
    response = client.put(
        "/api/retrieval-settings",
        json={
            "embedding": {
                **EMBEDDING,
                "base_url": EMBEDDING["base_url"] + "/",
                "api_key": "sk-emb",
            },
            "rerank": RERANK,
        },
    )
    assert response.status_code == 200
    body = response.json()
    # The trailing slash is stripped, the key is counted but never echoed.
    assert body["embedding"] == {
        "base_url": EMBEDDING["base_url"],
        "model": "emb-1",
        "has_api_key": True,
    }
    assert body["rerank"] == {"base_url": RERANK["base_url"], "model": "rr-1", "has_api_key": False}


def test_an_absent_kind_is_left_untouched(client: TestClient) -> None:
    client.put("/api/retrieval-settings", json={"embedding": EMBEDDING})
    response = client.put("/api/retrieval-settings", json={"rerank": RERANK})
    assert response.status_code == 200
    body = response.json()
    assert body["embedding"]["model"] == "emb-1"
    assert body["rerank"]["model"] == "rr-1"


def test_saving_the_endpoint_without_a_key_keeps_the_stored_key(client: TestClient) -> None:
    client.put("/api/retrieval-settings", json={"embedding": {**EMBEDDING, "api_key": "sk-emb"}})
    response = client.put(
        "/api/retrieval-settings", json={"embedding": {**EMBEDDING, "model": "emb-2"}}
    )
    assert response.status_code == 200
    assert response.json()["embedding"] == {
        "base_url": EMBEDDING["base_url"],
        "model": "emb-2",
        "has_api_key": True,
    }


def test_an_empty_string_deletes_the_key(client: TestClient) -> None:
    client.put("/api/retrieval-settings", json={"embedding": {**EMBEDDING, "api_key": "sk-emb"}})
    response = client.put(
        "/api/retrieval-settings", json={"embedding": {**EMBEDDING, "api_key": ""}}
    )
    assert response.status_code == 200
    assert response.json()["embedding"]["has_api_key"] is False


def test_null_clears_the_config_and_its_key(client: TestClient, core: CoreServices) -> None:
    client.put("/api/retrieval-settings", json={"embedding": {**EMBEDDING, "api_key": "sk-emb"}})
    response = client.put("/api/retrieval-settings", json={"embedding": None})
    assert response.status_code == 200
    body = response.json()
    assert body["embedding"] is None
    assert body["rerank"] is None
    # The keyring entry went with it: a cleared config must not leave a key
    # behind that a future config silently picks up.
    secrets = core.provider.secrets
    assert isinstance(secrets, InMemorySecretStore)
    assert "retrieval-embedding" not in secrets.values


def test_a_key_is_written_to_the_kind_identifier(client: TestClient, core: CoreServices) -> None:
    client.put("/api/retrieval-settings", json={"rerank": {**RERANK, "api_key": "sk-rr"}})
    secrets = core.provider.secrets
    assert isinstance(secrets, InMemorySecretStore)
    assert secrets.values["retrieval-rerank"] == "sk-rr"


# --- the test endpoints ---------------------------------------------------------


def test_the_embedding_test_reports_dimensions(client: TestClient) -> None:
    response = client.post(
        "/api/retrieval-settings/embedding/test",
        json={**EMBEDDING, "api_key": "sk-emb"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # The fake embeds to [length, 1.0]: two dimensions.
    assert body["dimensions"] == 2


def test_the_embedding_test_falls_back_to_the_stored_config(client: TestClient) -> None:
    client.put("/api/retrieval-settings", json={"embedding": {**EMBEDDING, "api_key": "sk-emb"}})
    response = client.post("/api/retrieval-settings/embedding/test", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_the_embedding_test_without_any_config_is_refused(client: TestClient) -> None:
    response = client.post("/api/retrieval-settings/embedding/test")
    assert response.status_code == 422


def test_the_rerank_test_answers_ok(client: TestClient) -> None:
    response = client.post("/api/retrieval-settings/rerank/test", json=RERANK)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_a_broken_endpoint_surfaces_as_provider_error(
    client: TestClient, core: CoreServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(spec: dict[str, str], texts: list[str]) -> list[list[float]]:
        del spec, texts
        raise ProviderError("嵌入服务不可用。")

    monkeypatch.setattr(core.provider, "embed", broken)
    response = client.post("/api/retrieval-settings/embedding/test", json=EMBEDDING)
    assert response.status_code == 502
    assert "嵌入服务不可用。" in response.json()["detail"]


# --- the migration ---------------------------------------------------------------


def test_migration_moves_the_profile_embedding_model_into_retrieval_settings(
    tmp_path: Path,
) -> None:
    """The legacy shape is manufactured, not checked in: a fresh database gets
    the embedding_model column re-added and filled - the exact shape an
    upgraded database has - and re-initializing moves the default profile's
    value into the retrieval settings, without the key (the keyring was keyed
    by profile id and cannot follow)."""
    database = Database(tmp_path)
    database.initialize()
    store = Store(database)
    store.create_model_profile(
        {
            "name": "旧配置",
            "base_url": "http://old.local/v1",
            "chat_model": "old-chat",
            "context_window": 8192,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )
    with database.transaction() as connection:
        connection.execute("ALTER TABLE model_profiles ADD COLUMN embedding_model TEXT")
        connection.execute("UPDATE model_profiles SET embedding_model = 'old-embedding'")

    database.initialize()

    columns = {row["name"] for row in database.fetchall("PRAGMA table_info(model_profiles)")}
    assert "embedding_model" not in columns
    spec = store.get_retrieval_spec("embedding")
    assert spec is not None
    assert spec["base_url"] == "http://old.local/v1"
    assert spec["model"] == "old-embedding"
    assert spec["has_api_key"] is False


def test_migration_without_a_stored_model_only_drops_the_column(tmp_path: Path) -> None:
    database = Database(tmp_path)
    database.initialize()
    store = Store(database)
    store.create_model_profile(
        {
            "name": "旧配置",
            "base_url": "http://old.local/v1",
            "chat_model": "old-chat",
            "context_window": 8192,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )
    with database.transaction() as connection:
        connection.execute("ALTER TABLE model_profiles ADD COLUMN embedding_model TEXT")

    database.initialize()

    columns = {row["name"] for row in database.fetchall("PRAGMA table_info(model_profiles)")}
    assert "embedding_model" not in columns
    assert store.get_retrieval_spec("embedding") is None


# --- the rerank stage ------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerank_decides_the_final_order_and_score(
    core: CoreServices, use_embedding: Callable[[], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whichever candidate the reranker scores highest comes back first, with
    its relevance_score as the hit's score and "reranked" as its source."""
    use_embedding()
    core.store.set_retrieval_spec("rerank", RERANK, has_api_key=False)
    await core.knowledge.import_items(
        [
            ImportItem(filename="甲.txt", content="第一个文档的内容".encode()),
            ImportItem(filename="乙.txt", content="第二个文档的内容".encode()),
            ImportItem(filename="丙.txt", content="第三个文档的内容".encode()),
        ],
        project_id=None,
    )

    async def scores_by_name(
        spec: dict[str, str], query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        del spec, query, top_n
        ranks = {"第一个": 0.1, "第二个": 0.5, "第三个": 0.9}

        def score_of(document: str) -> float:
            for marker, value in ranks.items():
                if marker in document:
                    return value
            return 0.0

        return sorted(
            ((index, score_of(document)) for index, document in enumerate(documents)),
            key=lambda item: -item[1],
        )

    monkeypatch.setattr(core.knowledge.provider, "rerank", scores_by_name)

    results = await core.knowledge.search("文档的内容", project_id=None)

    assert [result["title"] for result in results] == ["丙", "乙", "甲"]
    assert [result["score"] for result in results] == [0.9, 0.5, 0.1]
    assert all(result["source"] == "reranked" for result in results)


@pytest.mark.asyncio
async def test_search_without_a_rerank_config_skips_the_stage(
    core: CoreServices, use_embedding: Callable[[], None]
) -> None:
    use_embedding()
    await core.knowledge.import_items(
        [ImportItem(filename="笔记.txt", content="关于检索的内容".encode())],
        project_id=None,
    )

    results = await core.knowledge.search("检索", project_id=None)

    assert results
    fake = core.provider
    assert isinstance(fake, FakeProvider)
    assert fake.rerank_calls == []
    assert results[0]["source"] != "reranked"


@pytest.mark.asyncio
async def test_a_rerank_failure_degrades_to_the_unreranked_results(
    core: CoreServices, use_embedding: Callable[[], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead reranker must not take retrieval down with it: the candidates
    come back in the mixed order, cut to the limit, sources unlabeled."""
    use_embedding()
    core.store.set_retrieval_spec("rerank", RERANK, has_api_key=False)
    await core.knowledge.import_items(
        [ImportItem(filename="笔记.txt", content="关于检索的内容".encode())],
        project_id=None,
    )

    async def broken(
        spec: dict[str, str], query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        del spec, query, documents, top_n
        raise ProviderError("重排服务不可用。")

    monkeypatch.setattr(core.knowledge.provider, "rerank", broken)

    results = await core.knowledge.search("检索", project_id=None)

    assert results
    assert results[0]["source"] != "reranked"
