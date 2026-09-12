from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.errors import ProviderError, ValidationError
from app.runtime import CoreServices
from app.tokens import estimate_tokens
from app.utils import json_dump


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json_dump(payload)}\n\n"


class RunService:
    def __init__(self, services: CoreServices) -> None:
        self.services = services

    def start(
        self,
        conversation_id: str,
        *,
        content: str,
        model_profile_id: str | None,
        reasoning_level: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        store = self.services.store
        conversation = store.get_conversation(conversation_id)
        profile_id = model_profile_id or conversation["model_profile_id"]
        profile = (
            store.get_model_profile(profile_id) if profile_id else store.get_default_model_profile()
        )
        user = store.append_message(conversation_id, "user", content)
        assistant = store.append_message(conversation_id, "assistant", "", parent_id=user["id"])
        run = store.create_run(conversation_id, user["id"], assistant["id"], profile["id"])
        if conversation["title"] == "新对话":
            store.update_conversation(
                conversation_id,
                {"title": self._derive_title(content)},
            )
        return run, profile, reasoning_level or ""

    def start_regenerate(
        self,
        message_id: str,
        *,
        model_profile_id: str | None,
        reasoning_level: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Re-run an existing assistant reply from its originating user message."""
        store = self.services.store
        assistant = store.get_message(message_id)
        if assistant["role"] != "assistant":
            raise ValidationError("只能重新生成助手的回复。")
        messages = store.list_messages(assistant["conversation_id"])
        if not messages or messages[-1]["id"] != assistant["id"]:
            raise ValidationError("只能重新生成最新一条回复，否则会丢失它之后的对话。")
        if not assistant["parent_id"]:
            raise ValidationError("这条回复没有对应的提问，无法重新生成。")
        user_message = store.get_message(assistant["parent_id"])
        conversation = store.get_conversation(assistant["conversation_id"])
        profile_id = model_profile_id or conversation["model_profile_id"]
        profile = (
            store.get_model_profile(profile_id) if profile_id else store.get_default_model_profile()
        )
        # Clear the previous answer before building context so the model is not
        # asked to continue from the reply being replaced.
        store.update_message(assistant["id"], "", {})
        run = store.create_run(
            conversation["id"], user_message["id"], assistant["id"], profile["id"]
        )
        return run, profile, reasoning_level or ""

    async def stream(
        self,
        run_id: str,
        *,
        profile: dict[str, Any],
        reasoning_level: str | None,
    ) -> AsyncIterator[str]:
        store = self.services.store
        run = store.get_run(run_id)
        conversation = store.get_conversation(run["conversation_id"])
        user_message = store.get_message(run["user_message_id"])
        sequence = 0

        def audit(stage: str, state: str, payload: dict[str, Any]) -> str:
            nonlocal sequence
            sequence += 1
            event = store.create_run_event(run_id, sequence, stage, state, payload)
            return encode_sse("audit", event)

        yield encode_sse(
            "run.started",
            {
                "run_id": run_id,
                "assistant_message_id": run["assistant_message_id"],
                "model_profile_id": profile["id"],
            },
        )
        try:
            if self.services.context.is_compact_request(user_message["content"]):
                yield audit("context_compaction", "running", {"trigger": "user_request"})
                artifact = await self.services.context.compact(
                    conversation["id"],
                    profile,
                    force=True,
                )
                response = (
                    "已完成上下文压缩。" if artifact else "当前对话还没有足够的历史内容可压缩。"
                )
                metadata = {
                    "run_id": run_id,
                    "context_artifact_id": artifact["id"] if artifact else None,
                }
                store.update_message(run["assistant_message_id"], response, metadata)
                store.update_run(
                    run_id,
                    status="completed",
                    input_token_estimate=0,
                    output_token_estimate=estimate_tokens(response),
                    completed=True,
                )
                yield audit(
                    "context_compaction",
                    "completed",
                    {"artifact_id": artifact["id"] if artifact else None},
                )
                yield encode_sse(
                    "message.completed",
                    {
                        "message_id": run["assistant_message_id"],
                        "content": response,
                        "metadata": metadata,
                    },
                )
                return

            yield audit("context_compaction", "running", {"trigger": "threshold_check"})
            artifact = await self.services.context.maybe_compact(conversation["id"], profile)
            yield audit(
                "context_compaction",
                "completed",
                {"artifact_id": artifact["id"] if artifact else None},
            )
            yield audit("context_retrieval", "running", {})
            bundle = await self.services.context.build(
                conversation["id"],
                profile,
                user_message["content"],
            )
            store.update_run(
                run_id,
                status="running",
                input_token_estimate=bundle.input_token_estimate,
            )
            yield audit(
                "context_retrieval",
                "completed",
                {
                    "input_token_estimate": bundle.input_token_estimate,
                    "remaining_token_estimate": bundle.remaining_token_estimate,
                    "citation_count": len(bundle.citations),
                },
            )
            yield encode_sse(
                "context.ready",
                {
                    "citations": bundle.citations,
                    "input_token_estimate": bundle.input_token_estimate,
                    "remaining_token_estimate": bundle.remaining_token_estimate,
                },
            )
            yield audit("model_stream", "running", {"model": profile["chat_model"]})
            response_parts: list[str] = []
            reasoning_parts: list[str] = []
            usage: dict[str, Any] | None = None
            async for event in self.services.provider.stream_chat(
                profile,
                bundle.messages,
                reasoning_level=reasoning_level,
            ):
                if self.services.run_registry.is_cancelled(run_id):
                    partial = "".join(response_parts)
                    metadata = {
                        "run_id": run_id,
                        "citations": bundle.citations,
                        "reasoning": "".join(reasoning_parts),
                        "cancelled": True,
                    }
                    store.update_message(run["assistant_message_id"], partial, metadata)
                    store.update_run(
                        run_id,
                        status="cancelled",
                        input_token_estimate=bundle.input_token_estimate,
                        output_token_estimate=estimate_tokens(partial),
                        completed=True,
                    )
                    yield audit("model_stream", "cancelled", {})
                    yield encode_sse(
                        "run.cancelled",
                        {
                            "run_id": run_id,
                            "message_id": run["assistant_message_id"],
                            "content": partial,
                        },
                    )
                    return
                if event.kind == "delta":
                    response_parts.append(event.payload["text"])
                    yield encode_sse(
                        "message.delta",
                        {"message_id": run["assistant_message_id"], "delta": event.payload["text"]},
                    )
                elif event.kind == "reasoning":
                    reasoning_parts.append(event.payload["text"])
                    yield encode_sse(
                        "reasoning.delta",
                        {"message_id": run["assistant_message_id"], "delta": event.payload["text"]},
                    )
                elif event.kind == "usage":
                    usage = event.payload["usage"]
            response = "".join(response_parts).strip()
            if not response:
                response = "模型未返回可显示的文本。"
            metadata = {
                "run_id": run_id,
                "citations": bundle.citations,
                "reasoning": "".join(reasoning_parts),
                "usage": usage,
            }
            store.update_message(run["assistant_message_id"], response, metadata)
            store.update_run(
                run_id,
                status="completed",
                input_token_estimate=bundle.input_token_estimate,
                output_token_estimate=estimate_tokens(response),
                completed=True,
            )
            yield audit("model_stream", "completed", {"usage": usage or {}})
            yield encode_sse(
                "message.completed",
                {
                    "message_id": run["assistant_message_id"],
                    "content": response,
                    "metadata": metadata,
                },
            )
            yield audit("memory_write", "running", {})
            try:
                memories = await self.services.memory.extract_and_store(
                    profile=profile,
                    user_content=user_message["content"],
                    user_message_id=user_message["id"],
                    project_id=conversation["project_id"],
                )
            except ProviderError as error:
                yield audit("memory_write", "skipped", {"reason": str(error)})
            else:
                yield audit(
                    "memory_write", "completed", {"count": len(memories), "items": memories}
                )
        except ProviderError as error:
            partial = store.get_message(run["assistant_message_id"])["content"]
            if not partial:
                partial = "模型服务暂时无法完成本次回复。"
            store.update_message(
                run["assistant_message_id"],
                partial,
                {"run_id": run_id, "error": str(error)},
            )
            store.update_run(
                run_id,
                status="failed",
                error_message=str(error),
                output_token_estimate=estimate_tokens(partial),
                completed=True,
            )
            yield audit("model_stream", "failed", {"error": str(error)})
            yield encode_sse("run.failed", {"run_id": run_id, "error": str(error)})
        finally:
            self.services.run_registry.clear(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        self.services.store.get_run(run_id)
        self.services.run_registry.cancel(run_id)
        return self.services.store.update_run(run_id, status="cancelling")

    @staticmethod
    def _derive_title(content: str) -> str:
        normalized = " ".join(content.split())
        return normalized[:30] + ("…" if len(normalized) > 30 else "")
