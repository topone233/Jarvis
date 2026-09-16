from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from typing import Any

from app.errors import ProviderError, ValidationError
from app.images import parse_images, write_images
from app.memory import split_memory_block
from app.runtime import CoreServices, RunBroadcast
from app.schemas import ThinkingLevel
from app.store import INTERRUPTED_ERROR
from app.tokens import estimate_tokens
from app.utils import json_dump, new_id

# How far a streaming run may fall behind the database. This window is what a
# crash or a power cut can cost, so it trades durability against writing on
# every token. Whichever limit is reached first triggers a write.
CHECKPOINT_SECONDS = 0.5
CHECKPOINT_CHARACTERS = 400

# How long a subscriber may be left with nothing to read. A run can be quiet
# for a long time - the model is thinking, a tool call is in flight - and a
# silent connection is indistinguishable from a dead one. A comment line every
# few seconds is what lets a client tell them apart, and what keeps something
# in between (a proxy with an idle timeout) from closing the socket.
HEARTBEAT_SECONDS = 10.0


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json_dump(payload)}\n\n"


@dataclass(frozen=True)
class RunChoice:
    """What the composer picked for this one run.

    Deliberately not stored anywhere: the choice rides on the request that
    carried it, so a reload comes back to what the profile itself says. Nothing
    downstream may read this as configuration - the profile is the configuration,
    and this is one caller's answer to "which model, and where the thinking dial
    is".

    `chat_model` is None when the request named none, which is every internal
    caller: they want the profile's own model.
    """

    chat_model: str | None = None
    thinking: ThinkingLevel = "off"


class RunService:
    def __init__(self, services: CoreServices) -> None:
        self.services = services

    def start(
        self,
        conversation_id: str,
        *,
        content: str,
        images: list[str] | None = None,
        model_profile_id: str | None,
        choice: RunChoice,
    ) -> tuple[dict[str, Any], dict[str, Any], RunChoice]:
        store = self.services.store
        conversation = store.get_conversation(conversation_id)
        profile_id = model_profile_id or conversation["model_profile_id"]
        profile = (
            store.get_model_profile(profile_id) if profile_id else store.get_default_model_profile()
        )
        # Decoded and written before any message exists: a bad image fails the
        # request without leaving a row behind, and the files land under an id
        # the message is about to take. A write that fails after this point
        # strands files with no row, which is invisible; a message with no
        # images would be visible, so the invisible failure is the one to pick.
        payloads = parse_images(images or [])
        user_id = new_id()
        filenames = write_images(store.database, user_id, payloads)
        metadata = {"images": filenames} if filenames else None
        user = store.append_message(
            conversation_id, "user", content, message_id=user_id, metadata=metadata
        )
        assistant = store.append_message(conversation_id, "assistant", "", parent_id=user["id"])
        run = store.create_run(conversation_id, user["id"], assistant["id"], profile["id"])
        # Point the message at its run straight away. Leaving this to the first
        # checkpoint would mean a reload inside the first half second shows an
        # empty bubble for a run that is very much alive and unfindable.
        store.update_message(assistant["id"], "", {"run_id": run["id"]})
        if conversation["title"] == "新对话":
            # An image-only first message still deserves a name; the composer's
            # text may be empty, but something was sent.
            store.update_conversation(
                conversation_id,
                {"title": self._derive_title(content.strip() or "发了一张图片")},
            )
        return run, profile, choice

    def start_regenerate(
        self,
        message_id: str,
        *,
        model_profile_id: str | None,
        choice: RunChoice,
    ) -> tuple[dict[str, Any], dict[str, Any], RunChoice]:
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
        # The clear above wiped the previous run's id, so the new one goes back
        # on for the same reason `start` sets it: a reload has to be able to find
        # the run behind an answer that has not produced any text yet.
        store.update_message(assistant["id"], "", {"run_id": run["id"]})
        return run, profile, choice

    def launch(
        self,
        run: dict[str, Any],
        *,
        profile: dict[str, Any],
        choice: RunChoice,
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
                    choice=choice,
                )
            ),
        )

    async def _produce(
        self,
        run_id: str,
        *,
        broadcast: RunBroadcast,
        profile: dict[str, Any],
        choice: RunChoice,
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
            if artifact is None:
                audit("context_compaction", "completed", {"compacted": False})
            else:
                audit(
                    "context_compaction",
                    "completed",
                    {
                        "compacted": True,
                        "artifact_id": artifact["id"],
                        "range": [artifact["start_ordinal"], artifact["end_ordinal"]],
                    },
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
                    "memory_count": len(bundle.memories),
                    "memory_keys": [memory["memory_key"] for memory in bundle.memories],
                    "memory_mode": bundle.memory_mode,
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
            # The name that goes on the wire is the one worth auditing - the
            # composer may have named another model for this one run, and the
            # profile's own name would be wrong here.
            audit(
                "model_stream",
                "running",
                {"model": choice.chat_model or profile["chat_model"]},
            )
            # Accumulated raw, tail block included; everything shown or stored
            # below is the visible prefix of it. While the model is writing a
            # ```memory block the prefix simply stops growing, so no half-open
            # JSON ever reaches the screen or a checkpoint.
            response_parts: list[str] = []
            reasoning_parts: list[str] = []
            usage: dict[str, Any] | None = None
            flushed = 0
            flushed_at = time.monotonic()
            async for event in self.services.provider.stream_chat(
                profile,
                bundle.messages,
                chat_model=choice.chat_model,
                thinking=choice.thinking,
            ):
                if registry.is_cancelled(run_id):
                    partial, _ = split_memory_block("".join(response_parts))
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

                content, _ = split_memory_block("".join(response_parts))
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

            raw = "".join(response_parts).strip()
            # What looked like a memory tail while streaming gets its verdict
            # here. A block that produces real actions is stripped and carried
            # out; anything else - an answer that just happens to end in such
            # a fence, or JSON where nothing survived validation - is content
            # like any other and is shown in full.
            response, block = split_memory_block(raw)
            actions: list[dict[str, Any]] = []
            if block is not None:
                actions = self.services.memory.apply_reply(
                    reply=raw,
                    user_content=user_message["content"],
                    user_message_id=user_message["id"],
                    project_id=conversation["project_id"],
                )
                if not actions:
                    response = raw
                    block = None
            response = response.strip()
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
            # The answer is on its way before the memory step reports, so the
            # stream stays open only for the moment the actions take.
            broadcast.emit(
                "message.completed",
                {"message_id": assistant_id, "content": response, "metadata": metadata},
            )
            if actions:
                # The step exists only when it did something - a block that
                # parsed to no valid actions leaves no trace at all, which is
                # what keeps a plain answer free of a "写入记忆" row. The work
                # is local and takes milliseconds, so both records carry
                # effectively the same moment.
                audit("memory_write", "running", {})
                audit(
                    "memory_write",
                    "completed",
                    {"count": len(actions), "items": actions},
                )
            broadcast.close()
        except ProviderError as error:
            # Best-effort bookkeeping: the conversation may already have been
            # deleted - deleting one cancels its runs and erases their rows -
            # and a write failing here must not mask the terminal event, which
            # the finish below (and the finally after it) guarantees.
            with contextlib.suppress(Exception):
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
            # waiting on an open broadcast would hang forever. The audit is
            # best-effort for the same reason as above.
            with contextlib.suppress(Exception):
                store.update_run(run_id, status="failed", error_message=str(error), completed=True)
            with contextlib.suppress(Exception):
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
            try:
                await asyncio.wait_for(broadcast.wait_for_change(version), HEARTBEAT_SECONDS)
            except TimeoutError:
                # Nothing happened for a while. An SSE comment is ignored by
                # every parser, including this app's, so it costs the client
                # nothing to receive - it is there only to be evidence that the
                # connection is still alive. The version is left alone because
                # nothing was published.
                yield ": keep-alive\n\n"
                continue
            version = broadcast.version

    def _replay(self, run: dict[str, Any]) -> Iterator[str]:
        """Report a run that is no longer being produced.

        Covers the client that reconnects in the moment a run ends, and the one
        that asks about a run from days ago. Both get the same terminal event
        they would have seen live, so the client needs no special case.

        The audit trail is read back from the store rather than the broadcast,
        which the producer drops the moment it finishes. Without it the steps
        would appear or vanish depending on a race the user cannot see: attach
        while the answer is still coming and they are there, attach a moment
        later - which is what a reload is - and the answer arrives with no sign
        of what produced it. The records are the same ones the live path sent,
        so both paths draw the same screen.
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
        for event in store.list_run_events(run["id"]):
            yield encode_sse("audit", event)
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
