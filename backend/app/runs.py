from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import traceback
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ask_user_tool import ASK_USER_TOOLS, validate_ask_user
from app.bash_tool import BASH_STOPPED_TEXT, BASH_TOOLS
from app.errors import ProviderError, ValidationError
from app.images import parse_images, write_images
from app.knowledge_tool import KNOWLEDGE_TOOLS, USAGE, KnowledgeToolService
from app.runtime import CoreServices, PendingUserInput, RunBroadcast, RunRegistry
from app.schemas import ThinkingLevel
from app.settings import read_bash_settings, read_tool_limits
from app.skills import SKILL_TOOLS, SkillToolService
from app.skills import USAGE as SKILL_USAGE
from app.store import INTERRUPTED_ERROR
from app.tokens import estimate_tokens
from app.utils import json_dump, new_id

logger = logging.getLogger(__name__)

# How far a streaming run may fall behind the database. This window is what a
# crash or a power cut can cost, so it trades durability against writing on
# every token. Whichever limit is reached first triggers a write.
CHECKPOINT_SECONDS = 0.5
CHECKPOINT_CHARACTERS = 400

# Tool rounds one run may spend before it is finished whether it likes it or
# not, and the gate on repeating one identical call inside a window, are both
# user settings - see `settings.ToolLimits`.

# How long a subscriber may be left with nothing to read. A run can be quiet
# for a long time - the model is thinking, a tool call is in flight - and a
# silent connection is indistinguishable from a dead one. A comment line every
# few seconds is what lets a client tell them apart, and what keeps something
# in between (a proxy with an idle timeout) from closing the socket.
HEARTBEAT_SECONDS = 10.0


# A bash command waits this poll interval at a time inside its grace window,
# checking the stop button between waits - the window itself is the
# `bash_grace_seconds` setting.
BASH_GRACE_POLL_SECONDS = 0.2


async def grace_window(registry: RunRegistry, run_id: str, seconds: float) -> bool:
    """Hold a bash call before it executes; True when a stop arrived in time.

    Every bash command gets the same window, whatever it says: reading the
    text for danger is a filter that is wrong exactly once and then useless,
    while a window that is always there is a promise that holds. The raw call
    is already on the audit trail and the screen when this starts, so the
    window is the user's time to read the command and reach the stop button -
    and a stop here means the command never ran at all.
    """
    deadline = time.monotonic() + seconds
    while True:
        if registry.is_cancelled(run_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(BASH_GRACE_POLL_SECONDS, remaining))


async def wait_user_input(
    registry: RunRegistry, run_id: str, pending: PendingUserInput
) -> str | None:
    """Hold the run for the user's answer; None when the run was stopped.

    The answer rides a future resolved by the API, so there is nothing to
    poll for but the stop button, and the poll is only there because the
    future waking the producer directly would race a cancel that arrives in
    the same moment. A stop wins over a late answer: the run is ending, and
    the pop-up the user never pressed is about to disappear with it.
    """
    while True:
        if registry.is_cancelled(run_id):
            return None
        if pending.future.done():
            return pending.future.result()
        await asyncio.sleep(BASH_GRACE_POLL_SECONDS)


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json_dump(payload)}\n\n"


class RepeatGuard:
    """The gate that stops one identical call being asked for over and over.

    A call is identified by its tool name plus its arguments - whitespace and
    key order aside, so `{"a": 1}` and `{ "a" : 1 }` are the same call. Each
    call that gets through is stamped; within the window at most `limit` of
    the same identity may execute, and the next one is turned away with words
    that redirect the model rather than an error it would retry around.

    The window slides: a call older than it no longer counts, so a legitimate
    polling call spread wider than the window is never blocked. A call refused
    here is not stamped - it never executed, so it spends nothing.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._stamps: dict[tuple[str, str], list[float]] = {}

    def gate(self, name: str, arguments: str, *, now: float) -> str | None:
        """None to let the call through (and stamp it); otherwise the refusal."""
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            # Arguments that do not parse have no meaning to normalize away;
            # the raw text is the identity, and two different garbles are two
            # different calls.
            identity = arguments
        else:
            identity = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = (name, identity)
        stamps = [stamp for stamp in self._stamps.get(key, []) if now - stamp < self.window_seconds]
        if len(stamps) >= self.limit:
            return (
                f"{name}: 同一调用在 {self.window_seconds} 秒内已执行 {self.limit} 次，"
                "已阻止执行。请基于已获得的信息作答，或改变调用方式。"
            )
        stamps.append(now)
        self._stamps[key] = stamps
        return None


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
        # The stages whose most recent audit row is still open. A run that
        # ends by any path must not leave one open: the screen times a
        # running row against `now`, so a row without a closing record would
        # count seconds forever - live and, replayed from the store, after
        # every reload too.
        open_stages: list[str] = []

        def audit(stage: str, state: str, payload: dict[str, Any]) -> None:
            nonlocal sequence
            sequence += 1
            if state == "running":
                if stage not in open_stages:
                    open_stages.append(stage)
            elif stage in open_stages:
                open_stages.remove(stage)
            event = store.create_run_event(run_id, sequence, stage, state, payload)
            broadcast.emit("audit", event)

        def close_open_stages(reason: str) -> None:
            """Shut every stage still open, as its own failed record.

            Called only from the endings a run did not plan (a tool or the
            provider raising past every handler); the planned endings close
            their own rows on the way out, and an empty list costs nothing.
            """
            for stage in list(open_stages):
                audit(stage, "failed", {"reason": reason})

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
            if bundle.forced_skill is not None:
                # The user named the skill, so the load needed no round and no
                # tool call - but it is still an action worth a step on the
                # trail, or the answer would cite a skill nobody can see it used.
                audit(
                    "skill_tool",
                    "completed",
                    {"command": f"/{bundle.forced_skill}", "trigger": "user_request"},
                )
            # The tool rounds. A round that ends in tool calls is not the
            # answer - its text was a stop on the way ("我来查一下"), shown
            # live and replaced by the next round's. The loop owns the wire's
            # protocol details: an assistant message carrying its tool calls,
            # one tool message per call, memory answered in place.
            limits = read_tool_limits(store)
            guard = RepeatGuard(limits.repeat_limit, limits.repeat_window_seconds)
            knowledge_tool = KnowledgeToolService(self.services.store, self.services.knowledge)
            has_documents = bool(
                self.services.store.list_knowledge_documents(conversation["project_id"])
            )
            skill_tool = SkillToolService(self.services.skills)
            # Bash settings read once per run, like the round budget: a
            # mid-run change waits for the next answer. The stored working
            # directory is the run's cwd; nothing stored means the data
            # directory.
            bash_settings = read_bash_settings(store)
            bash_tool = self.services.bash_tool
            bash_cwd = (
                Path(bash_settings.working_dir)
                if bash_settings.working_dir
                else self.services.database.data_directory
            )
            tools = [
                *bundle.tools,
                *(KNOWLEDGE_TOOLS if has_documents else []),
                *(SKILL_TOOLS if self.services.skills.has_enabled() else []),
                *(BASH_TOOLS if bash_settings.enabled else []),
                *ASK_USER_TOOLS,
            ]
            messages = list(bundle.messages)
            # The empty assistant row this run writes into rode along in the
            # single-round request and cost nothing there. In a tool loop it
            # would sit between turns on the wire - an assistant turn with no
            # part in the conversation - so it does not ride anymore.
            while messages and messages[-1]["role"] == "assistant" and not messages[-1]["content"]:
                messages.pop()
            response_parts: list[str] = []
            reasoning_parts: list[str] = []
            # One round's own thinking and usage, reset at each boundary - the
            # round's audit row records them, which is what puts the thinking
            # on the trail between the tool rows it sits between.
            round_reasoning: list[str] = []
            round_usage: dict[str, Any] | None = None
            # Whether a model_stream row is currently open: the failure paths
            # may only emit their failed record over a row that is actually
            # open, or an ending record with no open row opens a stray one.
            round_row_open = False
            usage: dict[str, Any] | None = None
            flushed = 0
            flushed_at = time.monotonic()

            async def answer_tool_call(call: dict[str, Any], round_text: str) -> str:
                """One tool call's output, and the audit rows describing it.

                Every call is auditable, whatever it was: the raw tool-call
                object the model produced (id, name, arguments - verbatim)
                and the result text that went back to it both land in the
                trail, so the page shows what the AI asked for and what it
                got, not a summary of it. A call turned away before it ran
                is recorded too, under the generic `tool_call` stage.
                """
                name = str(call.get("name", ""))
                raw_call = {
                    "id": str(call.get("id", "")),
                    "name": name,
                    "arguments": call.get("arguments", ""),
                }
                call_payload: dict[str, Any] = {"call": raw_call}
                if round_text:
                    # The round's accompanying text is otherwise lost - the
                    # next round replaces it on screen - so it rides here.
                    call_payload["round_text"] = round_text

                async def run_tool(
                    stage: str, execute: Callable[..., Awaitable[str]], *args: Any, **kwargs: Any
                ) -> tuple[str, bool]:
                    """Run one tool; (output, False), or (text, True) on a crash.

                    A tool raising must not fail the whole run: the failure
                    closes the stage's open row (the screen times a running
                    row against `now`, so an unclosed one counts seconds
                    forever) and goes back to the model as the tool result,
                    like every other tool failure is. The bool is what keeps
                    the caller from celebrating a crash with a completed
                    record after the failed one.
                    """
                    try:
                        return await execute(*args, **kwargs), False
                    except Exception as error:
                        # The type name rides in the text because some
                        # exceptions - a bare NotImplementedError is the one
                        # that burned us - stringify to nothing, and a bare
                        # colon tells nobody anything. The traceback goes to
                        # the log and onto the audit row, so the crash is
                        # diagnosable from the console, from the live page,
                        # and from a reload of the same page.
                        detail = f"{type(error).__name__}"
                        if str(error):
                            detail = f"{detail}: {error}"
                        failure = f"{name}: 执行异常：{detail}"
                        logger.exception("工具 %s 执行异常", name)
                        audit(
                            stage,
                            "failed",
                            {
                                **call_payload,
                                "output": failure,
                                "reason": "执行异常",
                                "traceback": traceback.format_exc(),
                            },
                        )
                        return failure, True

                # The repeat gate sees every tool the same way: same name and
                # same arguments, once too often inside the window, is a loop
                # whatever the tool was. The refusal is the call's tool result,
                # and the gate records nothing for a call it turned away.
                refusal = guard.gate(name, str(call.get("arguments") or ""), now=time.monotonic())
                if refusal is not None:
                    audit(
                        "tool_call",
                        "failed",
                        {**call_payload, "output": refusal, "reason": "重复命令已拦截"},
                    )
                    return refusal
                try:
                    parsed = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError:
                    parsed = None
                if not isinstance(parsed, dict):
                    parsed = {}
                if name == "knowledge":
                    command = parsed.get("command")
                    if not isinstance(command, str) or not command.strip():
                        message = "knowledge: 空命令。" + USAGE
                        audit(
                            "knowledge_tool",
                            "failed",
                            {**call_payload, "output": message, "reason": "空命令"},
                        )
                        return message
                    audit("knowledge_tool", "running", {**call_payload, "command": command})
                    output, failed = await run_tool(
                        "knowledge_tool",
                        knowledge_tool.execute,
                        command,
                        project_id=conversation["project_id"],
                    )
                    if failed:
                        return output
                    audit(
                        "knowledge_tool",
                        "completed",
                        {
                            "command": command,
                            "output": output,
                            "output_chars": len(output),
                        },
                    )
                    return output
                if name == "skill":
                    command = parsed.get("command")
                    if not isinstance(command, str) or not command.strip():
                        message = "skill: 空命令。" + SKILL_USAGE
                        audit(
                            "skill_tool",
                            "failed",
                            {**call_payload, "output": message, "reason": "空命令"},
                        )
                        return message
                    audit("skill_tool", "running", {**call_payload, "command": command})
                    output, failed = await run_tool("skill_tool", skill_tool.execute, command)
                    if failed:
                        return output
                    audit(
                        "skill_tool",
                        "completed",
                        {
                            "command": command,
                            "output": output,
                            "output_chars": len(output),
                        },
                    )
                    return output
                if name == "bash":
                    command = parsed.get("command")
                    if not isinstance(command, str) or not command.strip():
                        message = "bash: 空命令。"
                        audit(
                            "bash_tool",
                            "failed",
                            {**call_payload, "output": message, "reason": "空命令"},
                        )
                        return message
                    # The raw call is on the trail and the screen before
                    # anything runs. What stands between the model and the
                    # command is the user's: the ask mode holds here until
                    # they approve or refuse it, the grace mode gives them a
                    # fixed window to reach the stop button. A stop - or a
                    # refusal - means the command never ran at all.
                    if bash_settings.approval_mode == "ask":
                        audit(
                            "bash_tool",
                            "running",
                            {
                                **call_payload,
                                "command": command,
                                "cwd": str(bash_cwd),
                                "approval": "ask",
                            },
                        )
                        # The slot exists before the announcement, so a client
                        # that answers impossibly fast still finds a future
                        # to resolve instead of a refusal.
                        pending = registry.request_user_input(
                            run_id, "bash", {"command": command, "cwd": str(bash_cwd)}
                        )
                        broadcast.emit(
                            "user_input.requested",
                            {"kind": "bash", "command": command, "cwd": str(bash_cwd)},
                        )
                        decision = await wait_user_input(registry, run_id, pending)
                        registry.pop_user_input(run_id)
                        if decision is None:
                            audit(
                                "bash_tool",
                                "cancelled",
                                {**call_payload, "command": command, "reason": "执行前被用户停止"},
                            )
                            return "bash: 用户在执行前停止了这条命令。"
                        if decision == "deny":
                            audit(
                                "bash_tool",
                                "cancelled",
                                {
                                    **call_payload,
                                    "command": command,
                                    "output": "bash: 用户拒绝了这条命令。",
                                    "reason": "用户拒绝执行",
                                },
                            )
                            return "bash: 用户拒绝了这条命令。"
                    else:
                        audit(
                            "bash_tool",
                            "running",
                            {
                                **call_payload,
                                "command": command,
                                "cwd": str(bash_cwd),
                                "grace_seconds": bash_settings.grace_seconds,
                            },
                        )
                        if await grace_window(registry, run_id, bash_settings.grace_seconds):
                            audit(
                                "bash_tool",
                                "cancelled",
                                {**call_payload, "command": command, "reason": "执行前被用户停止"},
                            )
                            return "bash: 用户在执行前停止了这条命令。"
                    output, failed = await run_tool(
                        "bash_tool",
                        bash_tool.execute,
                        command,
                        cwd=bash_cwd,
                        should_cancel=lambda: registry.is_cancelled(run_id),
                    )
                    if failed:
                        return output
                    if output == BASH_STOPPED_TEXT:
                        audit(
                            "bash_tool",
                            "cancelled",
                            {
                                **call_payload,
                                "command": command,
                                "output": output,
                                "reason": "执行中被用户停止",
                            },
                        )
                        return output
                    audit(
                        "bash_tool",
                        "completed",
                        {"command": command, "output": output, "output_chars": len(output)},
                    )
                    return output
                if name == "ask_user":
                    question, options, invalid = validate_ask_user(parsed)
                    if invalid is not None:
                        audit(
                            "ask_user",
                            "failed",
                            {**call_payload, "output": invalid, "reason": "参数无效"},
                        )
                        return invalid
                    # The run stops here until the user answers the popup -
                    # which may be never, and that is the point: the model
                    # asked for a decision only they can make. A stop ends
                    # the whole run, so a None here only needs the closing
                    # audit row; the outer loop's own cancel check finishes
                    # the run right after.
                    audit(
                        "ask_user",
                        "running",
                        {**call_payload, "question": question, "options": options},
                    )
                    # Slot before announcement, for the same reason as bash.
                    pending = registry.request_user_input(
                        run_id, "question", {"question": question, "options": options}
                    )
                    broadcast.emit(
                        "user_input.requested",
                        {"kind": "question", "question": question, "options": options},
                    )
                    answer = await wait_user_input(registry, run_id, pending)
                    registry.pop_user_input(run_id)
                    if answer is None:
                        audit(
                            "ask_user",
                            "cancelled",
                            {**call_payload, "question": question, "reason": "运行被用户停止"},
                        )
                        return "ask_user: 用户停止了运行，没有回答。"
                    audit(
                        "ask_user",
                        "completed",
                        {
                            **call_payload,
                            "question": question,
                            "options": options,
                            "answer": answer,
                        },
                    )
                    return f"用户的回答：{answer}"
                if name in ("save_memory", "forget_memory"):
                    # Multi-round protocol: an assistant message's tool calls
                    # must be answered, so memory writes happen right here
                    # instead of after the stream, and the result text tells
                    # the model what landed.
                    actions = self.services.memory.apply_tool_calls(
                        [call],
                        user_content=user_message["content"],
                        user_message_id=user_message["id"],
                        project_id=conversation["project_id"],
                    )
                    if not actions:
                        message = "没有执行任何记忆操作：参数无效或无法定位目标记忆。"
                        audit(
                            "tool_call",
                            "failed",
                            {**call_payload, "output": message, "reason": "没有执行任何记忆操作"},
                        )
                        return message
                    audit("memory_write", "running", call_payload)
                    result = _memory_result_text(actions)
                    audit(
                        "memory_write",
                        "completed",
                        {"count": len(actions), "items": actions, "output": result},
                    )
                    return result
                message = f"knowledge: unknown tool: {name}。"
                audit(
                    "tool_call",
                    "failed",
                    {**call_payload, "output": message, "reason": "未知工具"},
                )
                return message

            def _memory_result_text(actions: list[dict[str, Any]]) -> str:
                action = actions[0]
                memory = action.get("memory")
                key = memory.get("memory_key", "") if isinstance(memory, dict) else ""
                verbs = {
                    "created": f"已保存记忆：{key}",
                    "superseded": f"已更新记忆：{key}",
                    "confirmed": f"记忆未变化，已确认：{key}",
                    "forgotten": f"已忘记 {action.get('count', 1)} 条记忆：{key}",
                }
                return verbs.get(str(action.get("action")), "已处理。")

            for _ in range(limits.max_rounds):
                tool_calls: list[dict[str, Any]] = []
                # One row per round, opened here and closed by the round's own
                # completed record: the row's span is that round's work, and it
                # sits exactly between the tool rows around it.
                audit(
                    "model_stream",
                    "running",
                    {"model": choice.chat_model or profile["chat_model"]},
                )
                round_row_open = True
                async for event in self.services.provider.stream_chat(
                    profile,
                    messages,
                    chat_model=choice.chat_model,
                    thinking=choice.thinking,
                    tools=tools,
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
                        audit(
                            "model_stream",
                            "cancelled",
                            {"reasoning": "".join(round_reasoning)},
                        )
                        round_row_open = False
                        broadcast.show_text(content=partial, reasoning="".join(round_reasoning))
                        broadcast.finish(
                            "run.cancelled",
                            {"run_id": run_id, "message_id": assistant_id, "content": partial},
                        )
                        return
                    if event.kind == "usage":
                        usage = round_usage = event.payload["usage"]
                        continue
                    if event.kind == "reasoning":
                        reasoning_parts.append(event.payload["text"])
                        round_reasoning.append(event.payload["text"])
                    elif event.kind == "delta":
                        response_parts.append(event.payload["text"])
                    elif event.kind == "tool_calls":
                        tool_calls.extend(event.payload["calls"])
                    else:
                        continue

                    content = "".join(response_parts)
                    # The wire carries this round's thinking; the whole run's
                    # accumulation lives on in the metadata for a reload.
                    reasoning = "".join(round_reasoning)
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
                            {
                                "run_id": run_id,
                                "citations": bundle.citations,
                                "reasoning": reasoning,
                            },
                        )
                        flushed = len(content) + len(reasoning)
                        flushed_at = now

                if not tool_calls:
                    # A round with nothing to answer is the answer.
                    break
                # The round ended in tool calls: close its row now, with its
                # own thinking attached, so the reasoning is recorded between
                # the tool rows rather than in one pile after all of them.
                audit(
                    "model_stream",
                    "completed",
                    {"usage": round_usage or {}, "reasoning": "".join(round_reasoning)},
                )
                round_row_open = False
                messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(response_parts),
                        "tool_calls": [
                            {
                                "id": str(call.get("id", "")),
                                "type": "function",
                                "function": {
                                    "name": str(call.get("name", "")),
                                    "arguments": call.get("arguments", ""),
                                },
                            }
                            for call in tool_calls
                        ],
                    }
                )
                for call in tool_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(call.get("id", "")),
                            "content": await answer_tool_call(
                                call, round_text="".join(response_parts)
                            ),
                        }
                    )
                # The next round's text and thinking both replace this round's
                # on the wire: each round's reasoning is recorded on its own
                # audit row (the completed record above), so the live stream
                # only ever needs to carry the round in flight. The whole
                # run's thinking still accumulates in the metadata, which is
                # what a reload seeds from.
                response_parts = []
                round_reasoning = []
                round_usage = None
                if reasoning_parts:
                    reasoning_parts.append("\n\n")
                flushed = 0
                flushed_at = time.monotonic()
                broadcast.show_text(content="", reasoning="")
                # The shrinking content is a boundary a subscriber cannot
                # infer from snapshots alone: the reset tells it to replace
                # rather than append, and follow() re-homes its own cursor.
                broadcast.emit("round.reset", {"message_id": assistant_id})
                # Between rounds the stream is not running, so its own cancel
                # check cannot fire; the stop button has to work here too.
                if registry.is_cancelled(run_id):
                    partial = "".join(response_parts)
                    store.update_message(
                        assistant_id,
                        partial,
                        {
                            "run_id": run_id,
                            "citations": bundle.citations,
                            "reasoning": "".join(reasoning_parts),
                            "cancelled": True,
                        },
                    )
                    store.update_run(
                        run_id,
                        status="cancelled",
                        input_token_estimate=bundle.input_token_estimate,
                        output_token_estimate=estimate_tokens(partial),
                        completed=True,
                    )
                    # The round's own row already closed with its thinking
                    # attached, so no model_stream record here: a cancelled
                    # record with no open row would open a stray one, and the
                    # stop itself is carried by the run.cancelled event.
                    broadcast.finish(
                        "run.cancelled",
                        {"run_id": run_id, "message_id": assistant_id, "content": partial},
                    )
                    return
            else:
                # The round budget ran out while the model was still asking
                # for tools. Finish with whatever the last round wrote rather
                # than dropping the run, and say why in the audit.
                audit("tool_rounds_exhausted", "completed", {"rounds": limits.max_rounds})

            raw = "".join(response_parts).strip()
            response = raw if raw else "模型未返回可显示的文本。"
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
            audit(
                "model_stream",
                "completed",
                {"usage": usage or {}, "reasoning": "".join(round_reasoning)},
            )
            round_row_open = False
            broadcast.show_text(content=response, reasoning="".join(round_reasoning))
            broadcast.emit(
                "message.completed",
                {"message_id": assistant_id, "content": response, "metadata": metadata},
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
                if round_row_open:
                    audit(
                        "model_stream",
                        "failed",
                        {"error": str(error), "reasoning": "".join(round_reasoning)},
                    )
                    round_row_open = False
                # Before the terminal event, not after: a subscriber that has
                # seen `closed` stops reading, so closers landing later would
                # reach only the next reload.
                close_open_stages("运行提前结束")
            broadcast.finish("run.failed", {"run_id": run_id, "error": str(error)})
        except Exception as error:
            # Anything unexpected still has to close the run: a subscriber left
            # waiting on an open broadcast would hang forever. The audit is
            # best-effort for the same reason as above.
            with contextlib.suppress(Exception):
                store.update_run(run_id, status="failed", error_message=str(error), completed=True)
            with contextlib.suppress(Exception):
                if round_row_open:
                    audit(
                        "model_stream",
                        "failed",
                        {"error": str(error), "reasoning": "".join(round_reasoning)},
                    )
                close_open_stages("运行提前结束")
            broadcast.finish("run.failed", {"run_id": run_id, "error": str(error)})
        finally:
            if not broadcast.closed:
                # Cancelled, or a handler above failed partway. Either way the
                # run has to end here: a subscriber left waiting on an open
                # broadcast would never be told the answer stopped coming.
                with contextlib.suppress(Exception):
                    close_open_stages("运行提前结束")
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
                # A round boundary empties the published content and reasoning;
                # the cursors go back with them, so the next round's text and
                # thinking are sent whole and the client - told by the same
                # event - replaces rather than appends.
                if name == "round.reset":
                    content_sent = 0
                    reasoning_sent = 0
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

    def submit_user_input(self, run_id: str, value: str) -> dict[str, Any]:
        """Deliver the user's answer to a run paused waiting for one.

        A run that is not waiting - never asked, already answered, already
        ended - is refused rather than silently dropped: the user pressed a
        button and deserves to know nothing heard it. The value is checked
        against what is actually being waited for, so a bash approval cannot
        carry free text and a question cannot be answered with "approve".
        """
        pending = self.services.run_registry.pending_user_input(run_id)
        if pending is None:
            raise ValidationError("该运行没有在等待输入。")
        if pending.future.done():
            raise ValidationError("这个请求已经被处理过了。")
        if pending.kind == "bash" and value not in ("approve", "deny"):
            raise ValidationError("bash 审批只接受批准或拒绝。")
        if not self.services.run_registry.resolve_user_input(run_id, value):
            raise ValidationError("该运行没有在等待输入。")
        return self.services.store.get_run(run_id)

    @staticmethod
    def _derive_title(content: str) -> str:
        normalized = " ".join(content.split())
        return normalized[:30] + ("…" if len(normalized) > 30 else "")
