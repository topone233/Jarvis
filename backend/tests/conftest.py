from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JARVIS_BOOTSTRAP_DIR", str(Path.cwd() / ".test-bootstrap"))

from app.config import BootstrapStore
from app.main import create_app
from app.provider import ProviderEvent
from app.runtime import CoreServices, Runtime
from app.secrets import InMemorySecretStore


class FakeProvider:
    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        reasoning_level: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        del profile, messages, reasoning_level
        yield ProviderEvent("delta", {"text": "这是"})
        yield ProviderEvent("delta", {"text": "测试回复。"})
        yield ProviderEvent("usage", {"usage": {"prompt_tokens": 12, "completion_tokens": 6}})
        yield ProviderEvent("done", {})

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        reasoning_level: str | None = None,
    ) -> str:
        del profile, reasoning_level
        system = messages[0]["content"]
        if "跨会话保存" in system:
            return (
                '[{"kind":"preference","key":"回复风格","content":"喜欢简洁回答",'
                '"confidence":0.95}]'
            )
        return (
            "## 背景\n已压缩的历史。\n\n## 决定与约束\n保留用户偏好。\n\n## 未完成事项\n继续实现。"
        )

    async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        del profile
        return [[float(len(text)), 1.0] for text in texts]

    async def test_connection(self, profile: dict[str, Any]) -> dict[str, Any]:
        del profile
        return {"ok": True, "models": []}


@pytest.fixture
def core(tmp_path: Path) -> CoreServices:
    services = CoreServices.create(tmp_path / "data", InMemorySecretStore())
    fake = FakeProvider()
    services.provider = fake  # type: ignore[assignment]
    services.knowledge.provider = fake  # type: ignore[assignment]
    services.context.provider = fake  # type: ignore[assignment]
    services.memory.provider = fake  # type: ignore[assignment]
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
            "reasoning_levels": ["low", "high"],
            "is_default": True,
        },
        has_api_key=False,
    )


@pytest.fixture
def client(core: CoreServices, tmp_path: Path) -> TestClient:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = core
    return TestClient(create_app(runtime))
