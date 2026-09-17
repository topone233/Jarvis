from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.errors import ProviderError
from app.schemas import ThinkingLevel
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


def _accumulate_tool_call(tool_calls: dict[int, dict[str, Any]], call: Any) -> None:
    """Fold one streamed tool-call delta into the call it belongs to.

    The wire splits a call across chunks: an id and name may arrive once
    while the arguments trickle in as string pieces, keyed by index. Pieces
    of arguments concatenate; id and name overwrite.
    """
    if not isinstance(call, dict):
        return
    try:
        index = int(call.get("index", 0))
    except (TypeError, ValueError):
        return
    slot = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
    if call.get("id"):
        slot["id"] = str(call["id"])
    function = call.get("function")
    if not isinstance(function, dict):
        return
    if function.get("name"):
        slot["name"] = str(function["name"])
    if function.get("arguments"):
        slot["arguments"] += str(function["arguments"])


def _tool_calls_event(tool_calls: dict[int, dict[str, Any]]) -> ProviderEvent:
    calls = [tool_calls[index] for index in sorted(tool_calls)]
    return ProviderEvent("tool_calls", {"calls": calls})


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

    @staticmethod
    def _thinking_fields(profile: dict[str, Any], level: ThinkingLevel) -> dict[str, Any]:
        """What this profile adds to a request for one position of the dial.

        "off" is the profile's own business, and the only position that is: what
        to tell a provider that is not to think has no single spelling, so the
        "thinking off" fragment goes out as written. The three strengths are one
        name this server sets itself - `reasoning_effort` - which is why a
        strength sends that key and nothing else, not even the profile's own
        fragment. Internal callers (compaction, memory extraction) never ask for
        a strength and so always land on "off".

        Returned first in the payload so the fields this module sets itself win:
        a profile describes its provider's dialect, and is not allowed to decide
        which model answers, what it is asked, or whether the reply is streamed.
        """
        if level != "off":
            return {"reasoning_effort": level}
        raw = profile.get("thinking_off")
        return raw if isinstance(raw, dict) else {}

    async def list_models(self, profile: dict[str, Any]) -> list[Any]:
        """The endpoint's own list of model names, as it reports them."""
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
        try:
            return list(response.json().get("data", []))
        except (ValueError, AttributeError, TypeError) as error:
            raise ProviderError("模型服务返回了无法识别的模型列表。") from error

    async def test_connection(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "models": await self.list_models(profile)}

    async def complete_chat(
        self,
        profile: dict[str, Any],
        messages: list[dict[str, Any]],
        *,
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
    ) -> str:
        payload: dict[str, Any] = {
            **self._thinking_fields(profile, thinking),
            "model": chat_model or profile["chat_model"],
            "messages": messages,
            "stream": False,
        }
        # Absent unless the user set one: an endpoint that was never told has a
        # limit of its own, and it knows the model better than this code does.
        if profile.get("max_tokens"):
            payload["max_tokens"] = profile["max_tokens"]
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
        chat_model: str | None = None,
        thinking: ThinkingLevel = "off",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        payload: dict[str, Any] = {
            **self._thinking_fields(profile, thinking),
            "model": chat_model or profile["chat_model"],
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # Same as complete_chat: no limit unless one was configured.
        if profile.get("max_tokens"):
            payload["max_tokens"] = profile["max_tokens"]
        # Absent unless a caller registered tools. No tool_choice: the model
        # decides when a call is worth making, which is the whole contract -
        # these tools record things, they do not answer.
        if tools:
            payload["tools"] = tools
        tool_calls: dict[int, dict[str, Any]] = {}
        tool_calls_reported = False
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
                            if tool_calls and not tool_calls_reported:
                                yield _tool_calls_event(tool_calls)
                                tool_calls_reported = True
                            yield ProviderEvent("done", {})
                            break
                        try:
                            chunk = json.loads(value)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            for call in delta.get("tool_calls") or []:
                                _accumulate_tool_call(tool_calls, call)
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
                                if finish_reason == "tool_calls" and tool_calls:
                                    yield _tool_calls_event(tool_calls)
                                    tool_calls_reported = True
                                yield ProviderEvent("finish", {"reason": finish_reason})
                        if chunk.get("usage"):
                            yield ProviderEvent("usage", {"usage": chunk["usage"]})
                    # The [DONE] branch above reports on its way out; this one
                    # covers a stream that just ends - a dropped [DONE], an
                    # endpoint with no finish reason - so calls already
                    # assembled are not lost to a nonstandard close.
                    if tool_calls and not tool_calls_reported:
                        yield _tool_calls_event(tool_calls)
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
