from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JARVIS_BOOTSTRAP_DIR", str(Path.cwd() / ".test-bootstrap"))

from app.config import BootstrapStore
from app.main import create_app
from app.provider import ProviderEvent
from app.runtime import CoreServices, Runtime
from app.schemas import ThinkingLevel
from app.secrets import InMemorySecretStore


class FakeProvider:
    def __init__(self) -> None:
        # What the last streaming call was handed, for assertions on the wire.
        self.last_tools: list[dict[str, Any]] | None = None
        # Every rerank call, in order - a search that should rerank proves it
        # by this record, and a reorder test swaps this method out.
        self.rerank_calls: list[dict[str, Any]] = []
        # The real provider owns the keyring; the endpoints that save API keys
        # go through the same attribute, so the fake carries an in-memory one.
        self.secrets = InMemorySecretStore()

    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del profile, messages, chat_model, thinking
        self.last_tools = tools
        yield ProviderEvent("delta", {"text": "这是"})
        yield ProviderEvent("delta", {"text": "测试回复。"})
        yield ProviderEvent("usage", {"usage": {"prompt_tokens": 12, "completion_tokens": 6}})
        yield ProviderEvent("done", {})

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> str:
        del profile, messages, chat_model, thinking
        return (
            "## 背景\n已压缩的历史。\n\n## 决定与约束\n保留用户偏好。\n\n## 未完成事项\n继续实现。"
        )

    async def embed(self, spec: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del spec
        return [[float(len(text)), 1.0] for text in texts]

    async def rerank(
        self, spec: dict[str, Any], query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        """Stable passthrough: same order in, score 1.0 across the board.

        A test that needs actual reshuffling monkeypatches this method; the
        default must not reorder because the hybrid-search tests assert the
        mixed order the candidate stage produced.
        """
        self.rerank_calls.append(
            {"spec": spec, "query": query, "documents": documents, "top_n": top_n}
        )
        del spec, query
        return [(index, 1.0) for index in range(min(top_n, len(documents)))]

    async def list_models(self, profile: dict[str, Any]) -> list[Any]:
        del profile
        return [{"id": "mock-chat"}, {"id": "mock-chat-mini"}]

    async def test_connection(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "models": await self.list_models(profile)}


@pytest.fixture
def core(tmp_path: Path) -> CoreServices:
    # Made here rather than left to the app, which no longer creates the
    # directory it was pointed at: choosing one is what makes it exist.
    data = tmp_path / "data"
    data.mkdir()
    services = CoreServices.create(data, InMemorySecretStore())
    fake = FakeProvider()
    services.provider = fake  # type: ignore[assignment]
    services.knowledge.provider = fake  # type: ignore[assignment]
    services.context.provider = fake  # type: ignore[assignment]
    return services


@pytest.fixture
def profile(core: CoreServices) -> dict[str, Any]:
    return core.store.create_model_profile(
        {
            "name": "测试模型",
            "base_url": "http://mock.local/v1",
            "chat_model": "mock-chat",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )


@pytest.fixture
def use_embedding(core: CoreServices):
    """Turn the retrieval embedding config on, optionally naming the model.

    Whether knowledge import/search and memory ranking embed is no longer a
    property of a chat profile - it is the global retrieval config - so the
    tests that want vectors call this, and the ones that do not, do not.
    """

    def _use(model: str = "mock-embedding") -> None:
        core.store.set_retrieval_spec(
            "embedding",
            {"base_url": "http://mock.local/v1", "model": model},
            has_api_key=False,
        )

    return _use


@pytest.fixture
def client(core: CoreServices, tmp_path: Path) -> Iterator[TestClient]:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = core
    # Entered as a context manager on purpose: without it each request gets its
    # own event loop, and a run detached from the response that started it would
    # be torn down the moment that response finished.
    with TestClient(create_app(runtime)) as test_client:
        yield test_client
