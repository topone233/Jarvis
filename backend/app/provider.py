from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.errors import ProviderError
from app.secrets import SecretStore


@dataclass(frozen=True)
class ProviderEvent:
    kind: str
    payload: dict[str, Any]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return ""


class OpenAICompatibleProvider:
    """Minimal OpenAI Chat Completions compatible transport."""

    def __init__(
        self,
        secrets: SecretStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.secrets = secrets
        self.transport = transport

    def _headers(self, profile: dict[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        secret = self.secrets.get(profile["id"])
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        return headers

    @staticmethod
    def _endpoint(profile: dict[str, Any], suffix: str) -> str:
        return f"{profile['base_url'].rstrip('/')}/{suffix.lstrip('/')}"

    @staticmethod
    def _provider_error(response: httpx.Response) -> ProviderError:
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message") or payload.get("message")
        except (ValueError, AttributeError):
            message = response.text
        message = message or f"HTTP {response.status_code}"
        return ProviderError(f"模型服务请求失败：{message}")

    async def test_connection(self, profile: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                response = await client.get(
                    self._endpoint(profile, "models"),
                    headers=self._headers(profile),
                )
        except httpx.HTTPError as error:
            raise ProviderError("无法连接模型服务。") from error
        if response.is_error:
            raise self._provider_error(response)
        payload = response.json()
        return {"ok": True, "models": payload.get("data", [])}

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        reasoning_level: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": profile["chat_model"],
            "messages": messages,
            "stream": False,
        }
        if reasoning_level and reasoning_level in profile.get("reasoning_levels", []):
            payload["reasoning_effort"] = reasoning_level
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=120) as client:
                response = await client.post(
                    self._endpoint(profile, "chat/completions"),
                    headers=self._headers(profile),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise ProviderError("模型服务请求失败，未能完成生成。") from error
        if response.is_error:
            raise self._provider_error(response)
        try:
            choice = response.json()["choices"][0]
            return _content_to_text(choice["message"].get("content"))
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ProviderError("模型服务返回了无法识别的聊天响应。") from error

    async def stream_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        reasoning_level: str | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        payload: dict[str, Any] = {
            "model": profile["chat_model"],
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if reasoning_level and reasoning_level in profile.get("reasoning_levels", []):
            payload["reasoning_effort"] = reasoning_level
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=120) as client:
                async with client.stream(
                    "POST",
                    self._endpoint(profile, "chat/completions"),
                    headers=self._headers(profile),
                    json=payload,
                ) as response:
                    if response.is_error:
                        body = await response.aread()
                        response._content = body
                        raise self._provider_error(response)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        value = line.removeprefix("data:").strip()
                        if value == "[DONE]":
                            yield ProviderEvent("done", {})
                            break
                        try:
                            chunk = json.loads(value)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            text = _content_to_text(delta.get("content"))
                            if text:
                                yield ProviderEvent("delta", {"text": text})
                            reasoning = _content_to_text(
                                delta.get("reasoning_content") or delta.get("reasoning")
                            )
                            if reasoning:
                                yield ProviderEvent("reasoning", {"text": reasoning})
                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason:
                                yield ProviderEvent("finish", {"reason": finish_reason})
                        if chunk.get("usage"):
                            yield ProviderEvent("usage", {"usage": chunk["usage"]})
        except httpx.HTTPError as error:
            raise ProviderError("模型流式连接中断。") from error

    async def embed(self, profile: dict[str, Any], texts: list[str]) -> list[list[float]]:
        model = profile.get("embedding_model")
        if not model:
            raise ProviderError("当前模型配置未设置嵌入模型。")
        payload = {"model": model, "input": texts}
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=120) as client:
                response = await client.post(
                    self._endpoint(profile, "embeddings"),
                    headers=self._headers(profile),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise ProviderError("嵌入服务请求失败。") from error
        if response.is_error:
            raise self._provider_error(response)
        try:
            rows = sorted(response.json()["data"], key=lambda item: item["index"])
            return [row["embedding"] for row in rows]
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderError("嵌入服务返回了无法识别的响应。") from error
