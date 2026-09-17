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

    async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile
        return [[float(len(text)), 1.0] for text in texts]

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
            "embedding_model": "mock-embedding",
            "context_window": 4096,
            "output_token_reserve": 512,
            "is_default": True,
        },
        has_api_key=False,
    )


@pytest.fixture
def client(core: CoreServices, tmp_path: Path) -> Iterator[TestClient]:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = core
    # Entered as a context manager on purpose: without it each request gets its
    # own event loop, and a run detached from the response that started it would
    # be torn down the moment that response finished.
    with TestClient(create_app(runtime)) as test_client:
        yield test_client
