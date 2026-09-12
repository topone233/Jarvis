from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from app.errors import ProviderError, ValidationError
from app.runtime import CoreServices, RunBroadcast
from app.store import INTERRUPTED_ERROR
from app.tokens import estimate_tokens
from app.utils import json_dump

# How far a streaming run may fall behind the database. This window is what a
# crash or a power cut can cost, so it trades durability against writing on
# every token. Whichever limit is reached first triggers a write.
CHECKPOINT_SECONDS = 0.5
CHECKPOINT_CHARACTERS = 400


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

    def launch(
        self,
        run: dict[str, Any],
        *,
        profile: dict[str, Any],
        reasoning_level: str,
    ) -> None:
        """Begin producing a run in the background, owned by no connection.

        The work goes into its own task rather than into the HTTP response body,
        so a client that refreshes, navigates away, or never attaches at all
        cannot interrupt it. Whoever is watching reads the same broadcast.
        """
        registry = self.services.run_registry
        broadcast = registry.open(run["id"], run["assistant_message_id"])
        registry.track(
            run["id"],
            asyncio.create_task(
                self._produce(
                    run["id"],
                    broadcast=broadcast,
                    profile=profile,
                    reasoning_level=reasoning_level or None,
                )
            ),
        )

    async def _produce(
        self,
        run_id: str,
        *,
        broadcast: RunBroadcast,
        profile: dict[str, Any],
        reasoning_level: str | None,
    ) -> None:
        """Drive one run to completion, publishing as it goes."""
        store = self.services.store
        registry = self.services.run_registry
        sequence = 0

        def audit(stage: str, state: str, payload: dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            event = store.create_run_event(run_id, sequence, stage, state, payload)
            broadcast.emit("audit", event)

        try:
            run = store.get_run(run_id)
            conversation = store.get_conversation(run["conversation_id"])
            user_message = store.get_message(run["user_message_id"])
            assistant_id = run["assistant_message_id"]

            broadcast.emit(
                "run.started",
                {
                    "run_id": run_id,
                    "assistant_message_id": assistant_id,
                    "model_profile_id": profile["id"],
                },
            )

            if self.services.context.is_compact_request(user_message["content"]):
                audit("context_compaction", "running", {"trigger": "user_request"})
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
                store.update_message(assistant_id, response, metadata)
                store.update_run(
                    run_id,
                    status="completed",
                    input_token_estimate=0,
                    output_token_estimate=estimate_tokens(response),
                    completed=True,
                )
                audit(
                    "context_compaction",
                    "completed",
                    {"artifact_id": artifact["id"] if artifact else None},
                )
                broadcast.show_text(content=response, reasoning="")
                broadcast.finish(
                    "message.completed",
                    {"message_id": assistant_id, "content": response, "metadata": metadata},
                )
                return

            audit("context_compaction", "running", {"trigger": "threshold_check"})
            artifact = await self.services.context.maybe_compact(conversation["id"], profile)
            audit(
                "context_compaction",
                "completed",
                {"artifact_id": artifact["id"] if artifact else None},
            )
            audit("context_retrieval", "running", {})
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
            audit(
                "context_retrieval",
                "completed",
                {
                    "input_token_estimate": bundle.input_token_estimate,
                    "remaining_token_estimate": bundle.remaining_token_estimate,
                    "citation_count": len(bundle.citations),
                },
            )
            broadcast.emit(
                "context.ready",
                {
                    "citations": bundle.citations,
                    "input_token_estimate": bundle.input_token_estimate,
                    "remaining_token_estimate": bundle.remaining_token_estimate,
                },
            )
            audit("model_stream", "running", {"model": profile["chat_model"]})
            response_parts: list[str] = []
            reasoning_parts: list[str] = []
            usage: dict[str, Any] | None = None
            flushed = 0
            flushed_at = time.monotonic()
            async for event in self.services.provider.stream_chat(
                profile,
                bundle.messages,
                reasoning_level=reasoning_level,
            ):
                if registry.is_cancelled(run_id):
                    partial = "".join(response_parts)
                    metadata = {
                        "run_id": run_id,
                        "citations": bundle.citations,
                        "reasoning": "".join(reasoning_parts),
                        "cancelled": True,
                    }
                    store.update_message(assistant_id, partial, metadata)
                    store.update_run(
                        run_id,
                        status="cancelled",
                        input_token_estimate=bundle.input_token_estimate,
                        output_token_estimate=estimate_tokens(partial),
                        completed=True,
                    )
                    audit("model_stream", "cancelled", {})
                    broadcast.show_text(content=partial, reasoning="".join(reasoning_parts))
                    broadcast.finish(
                        "run.cancelled",
                        {"run_id": run_id, "message_id": assistant_id, "content": partial},
                    )
                    return
                if event.kind == "usage":
                    usage = event.payload["usage"]
                    continue
                if event.kind == "reasoning":
                    reasoning_parts.append(event.payload["text"])
                elif event.kind == "delta":
                    response_parts.append(event.payload["text"])
                else:
                    continue

                content = "".join(response_parts)
                reasoning = "".join(reasoning_parts)
                broadcast.show_text(content=content, reasoning=reasoning)
                now = time.monotonic()
                # Reasoning counts towards the window as well: a model can think for
                # a long time before its first visible word, and that thinking is
                # exactly what a reload would otherwise lose.
                if (
                    len(content) + len(reasoning) - flushed >= CHECKPOINT_CHARACTERS
                    or now - flushed_at >= CHECKPOINT_SECONDS
                ):
                    store.update_message(
                        assistant_id,
                        content,
                        {"run_id": run_id, "citations": bundle.citations, "reasoning": reasoning},
                    )
                    flushed = len(content) + len(reasoning)
                    flushed_at = now

            response = "".join(response_parts).strip()
            if not response:
                response = "模型未返回可显示的文本。"
            metadata = {
                "run_id": run_id,
                "citations": bundle.citations,
                "reasoning": "".join(reasoning_parts),
                "usage": usage,
            }
            store.update_message(assistant_id, response, metadata)
            store.update_run(
                run_id,
                status="completed",
                input_token_estimate=bundle.input_token_estimate,
                output_token_estimate=estimate_tokens(response),
                completed=True,
            )
            audit("model_stream", "completed", {"usage": usage or {}})
            broadcast.show_text(content=response, reasoning="".join(reasoning_parts))
            # Sent before the memory write so the answer appears at once; the
            # stream stays open until the write reports back.
            broadcast.emit(
                "message.completed",
                {"message_id": assistant_id, "content": response, "metadata": metadata},
            )
            audit("memory_write", "running", {})
            try:
                memories = await self.services.memory.extract_and_store(
                    profile=profile,
                    user_content=user_message["content"],
                    user_message_id=user_message["id"],
                    project_id=conversation["project_id"],
                )
            except ProviderError as error:
                audit("memory_write", "skipped", {"reason": str(error)})
            else:
                audit("memory_write", "completed", {"count": len(memories), "items": memories})
            broadcast.close()
        except ProviderError as error:
            partial = store.get_message(broadcast.assistant_message_id)["content"]
            if not partial:
                partial = "模型服务暂时无法完成本次回复。"
            store.update_message(
                broadcast.assistant_message_id,
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
            audit("model_stream", "failed", {"error": str(error)})
            broadcast.finish("run.failed", {"run_id": run_id, "error": str(error)})
        except Exception as error:
            # Anything unexpected still has to close the run: a subscriber left
            # waiting on an open broadcast would hang forever.
            with contextlib.suppress(Exception):
                store.update_run(run_id, status="failed", error_message=str(error), completed=True)
            audit("model_stream", "failed", {"error": str(error)})
            broadcast.finish("run.failed", {"run_id": run_id, "error": str(error)})
        finally:
            if not broadcast.closed:
                # Cancelled, or a handler above failed partway. Either way the
                # run has to end here: a subscriber left waiting on an open
                # broadcast would never be told the answer stopped coming.
                with contextlib.suppress(Exception):
                    store.update_run(
                        run_id,
                        status="interrupted",
                        error_message=INTERRUPTED_ERROR,
                        completed=True,
                    )
                broadcast.finish("run.failed", {"run_id": run_id, "error": INTERRUPTED_ERROR})
            registry.close(run_id)

    async def follow(self, run_id: str) -> AsyncGenerator[str, None]:
        """Stream one run to one client, live or long finished.

        Text arrives as cumulative snapshots, so this subscriber only ever needs
        to say how much it has already sent. A client that attaches halfway
        through therefore receives everything produced so far in its first
        flush, then continues token by token with no gap and no duplication.
        """
        run = self.services.store.get_run(run_id)
        broadcast = self.services.run_registry.broadcast(run_id)
        if broadcast is None:
            for chunk in self._replay(run):
                yield chunk
            return

        events_sent = 0
        content_sent = 0
        reasoning_sent = 0
        version = 0
        while True:
            while events_sent < len(broadcast.events):
                name, payload = broadcast.events[events_sent]
                events_sent += 1
                yield encode_sse(name, payload)
            if len(broadcast.content) > content_sent:
                yield encode_sse(
                    "message.delta",
                    {
                        "message_id": broadcast.assistant_message_id,
                        "delta": broadcast.content[content_sent:],
                    },
                )
                content_sent = len(broadcast.content)
            if len(broadcast.reasoning) > reasoning_sent:
                yield encode_sse(
                    "reasoning.delta",
                    {
                        "message_id": broadcast.assistant_message_id,
                        "delta": broadcast.reasoning[reasoning_sent:],
                    },
                )
                reasoning_sent = len(broadcast.reasoning)
            if broadcast.closed:
                return
            await broadcast.wait_for_change(version)
            version = broadcast.version

    def _replay(self, run: dict[str, Any]) -> Iterator[str]:
        """Report a run that is no longer being produced.

        Covers the client that reconnects in the moment a run ends, and the one
        that asks about a run from days ago. Both get the same terminal event
        they would have seen live, so the client needs no special case.
        """
        store = self.services.store
        message = store.get_message(run["assistant_message_id"])
        yield encode_sse(
            "run.started",
            {
                "run_id": run["id"],
                "assistant_message_id": run["assistant_message_id"],
                "model_profile_id": run["model_profile_id"],
            },
        )
        if run["status"] == "completed":
            yield encode_sse(
                "message.completed",
                {
                    "message_id": run["assistant_message_id"],
                    "content": message["content"],
                    "metadata": message["metadata"],
                },
            )
        elif run["status"] == "cancelled":
            yield encode_sse(
                "run.cancelled",
                {
                    "run_id": run["id"],
                    "message_id": run["assistant_message_id"],
                    "content": message["content"],
                },
            )
        else:
            yield encode_sse(
                "run.failed",
                {"run_id": run["id"], "error": run["error_message"] or INTERRUPTED_ERROR},
            )

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Stop a run, if there is still something to stop.

        A stop button races the last token: pressing it as the answer lands must
        not rewrite a finished run as cancelling, or the next startup would close
        it again as interrupted and a client would be told the answer failed when
        it had actually arrived.
        """
        run = self.services.store.get_run(run_id)
        if run["status"] not in {"running", "cancelling"}:
            return run
        self.services.run_registry.cancel(run_id)
        return self.services.store.update_run(run_id, status="cancelling")

    @staticmethod
    def _derive_title(content: str) -> str:
        normalized = " ".join(content.split())
        return normalized[:30] + ("…" if len(normalized) > 30 else "")
