from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.errors import ProviderError
from app.images import image_data_url, message_images
from app.knowledge import KnowledgeService
from app.prompts import PROMPT_VERSION
from app.provider import OpenAICompatibleProvider
from app.settings import (
    COMPACT_PERCENT_DEFAULT,
    COMPACTION_PROMPT,
    MEMORY_PROMPT,
    SYSTEM_PROMPT,
    read_prompt,
)
from app.store import Store
from app.tokens import IMAGE_TOKEN_ESTIMATE, estimate_tokens


@dataclass(frozen=True)
class ContextBundle:
    messages: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    # The memories injected into the system instruction, kept for the audit
    # trail: the model can only speak about memories it was shown, so the
    # retrieval step reports this list and the screen can say what the memory
    # step had to work with.
    memories: list[dict[str, Any]]
    input_token_estimate: int
    remaining_token_estimate: int


class ContextManager:
    def __init__(
        self,
        store: Store,
        provider: OpenAICompatibleProvider,
        knowledge: KnowledgeService,
    ) -> None:
        self.store = store
        self.provider = provider
        self.knowledge = knowledge

    @staticmethod
    def is_compact_request(content: str) -> bool:
        normalized = content.lower().strip()
        if normalized.startswith("/compact"):
            return True
        return "压缩" in normalized and any(
            word in normalized for word in ("上下文", "对话", "聊天记录")
        )

    async def maybe_compact(
        self, conversation_id: str, profile: dict[str, Any]
    ) -> dict[str, Any] | None:
        artifact = self.store.get_latest_context_artifact(conversation_id)
        raw_messages = self._messages_after_artifact(conversation_id, artifact)
        uncompressed_tokens = sum(self._cost(message) for message in raw_messages)
        artifact_tokens = artifact["token_estimate"] if artifact else 0
        input_budget = profile["context_window"] - profile["output_token_reserve"]
        compact_percent = profile.get("compact_percent", COMPACT_PERCENT_DEFAULT)
        compact_threshold = max(2_000, int(input_budget * compact_percent / 100))
        if uncompressed_tokens + artifact_tokens < compact_threshold:
            return None
        folded = await self.compact(conversation_id, profile, force=False)
        # `compact` hands back the previous artifact untouched when there was
        # nothing left to fold into it. That is "nothing happened" for this
        # caller: the audit step must not report a compaction that did not
        # occur, and the id is the only honest way to tell the two apart.
        if folded is None or (artifact is not None and folded["id"] == artifact["id"]):
            return None
        return folded

    async def compact(
        self,
        conversation_id: str,
        profile: dict[str, Any],
        *,
        force: bool,
    ) -> dict[str, Any] | None:
        previous = self.store.get_latest_context_artifact(conversation_id)
        pending = self._messages_after_artifact(conversation_id, previous)
        keep_recent = 4 if force else 6
        candidates = pending[:-keep_recent] if len(pending) > keep_recent else []
        if not candidates:
            return previous
        transcript = self._format_compaction_input(previous, candidates)
        try:
            summary = await self.provider.complete_chat(
                profile,
                [
                    {"role": "system", "content": read_prompt(self.store, COMPACTION_PROMPT)},
                    {"role": "user", "content": transcript},
                ],
            )
        except ProviderError:
            summary = self._fallback_summary(previous, candidates)
        source_ids = list(previous["source_message_ids"]) if previous else []
        source_ids.extend(message["id"] for message in candidates)
        anchors = self._decision_anchors(candidates)
        start_ordinal = previous["start_ordinal"] if previous else candidates[0]["ordinal"]
        return self.store.create_context_artifact(
            conversation_id=conversation_id,
            start_ordinal=start_ordinal,
            end_ordinal=candidates[-1]["ordinal"],
            content=summary,
            decision_anchors=anchors,
            source_message_ids=source_ids,
            prompt_version=PROMPT_VERSION,
            token_estimate=estimate_tokens(summary),
        )

    async def build(
        self,
        conversation_id: str,
        profile: dict[str, Any],
        user_query: str,
    ) -> ContextBundle:
        conversation = self.store.get_conversation(conversation_id)
        artifact = self.store.get_latest_context_artifact(conversation_id)
        memories = self.store.list_memories(
            project_id=conversation["project_id"],
            include_global=True,
        )[:16]
        # An image-only message has no words to search with; an empty query is
        # not an answer to any question, so nothing is looked up at all.
        citations = (
            await self.knowledge.search(
                user_query,
                project_id=conversation["project_id"],
                profile=profile,
            )
            if user_query.strip()
            else []
        )
        system = self._assemble_system_instruction(memories, artifact, citations)
        raw_messages = self._messages_after_artifact(conversation_id, artifact)
        input_budget = profile["context_window"] - profile["output_token_reserve"]
        selected_messages, estimate = self._fit_messages(system, raw_messages, input_budget)
        # A cleared prompt is a choice the settings screen allows, and an empty
        # system message is not how to carry it out: some endpoints reject one
        # outright. Nothing is sent instead, which is what "no system prompt"
        # means to the model anyway.
        opening = [{"role": "system", "content": system}] if system else []
        model_messages = [*opening, *selected_messages]
        remaining = max(input_budget - estimate, 0)
        return ContextBundle(
            messages=model_messages,
            citations=citations,
            memories=memories,
            input_token_estimate=estimate,
            remaining_token_estimate=remaining,
        )

    def _messages_after_artifact(
        self, conversation_id: str, artifact: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        messages = self.store.list_messages(conversation_id)
        if artifact is None:
            return messages
        return [message for message in messages if message["ordinal"] > artifact["end_ordinal"]]

    @staticmethod
    def _format_compaction_input(
        previous: dict[str, Any] | None, candidates: list[dict[str, Any]]
    ) -> str:
        sections: list[str] = []
        if previous:
            sections.append(f"<previous_compact>\n{previous['content']}\n</previous_compact>")
        source = "\n".join(
            f"[{message['role']} #{message['ordinal']}]\n{message['content']}"
            for message in candidates
        )
        sections.append(f"<history>\n{source}\n</history>")
        return "\n\n".join(sections)

    @staticmethod
    def _fallback_summary(previous: dict[str, Any] | None, candidates: list[dict[str, Any]]) -> str:
        excerpts = [
            f"- {message['role']}：{message['content'][:280].replace(chr(10), ' ')}"
            for message in candidates
        ]
        prior = previous["content"] if previous else "无"
        return "\n".join(
            [
                "## 背景",
                prior,
                "## 历史要点",
                *excerpts,
                "## 决定与约束",
                "请以原始消息和后续对话为准，以上为降级压缩记录。",
            ]
        )

    @staticmethod
    def _decision_anchors(messages: list[dict[str, Any]]) -> list[str]:
        markers = ("决定", "必须", "不要", "计划", "偏好", "需要", "约束")
        anchors: list[str] = []
        for message in messages:
            if message["role"] != "user":
                continue
            for line in re.split(r"[\n。！？]", message["content"]):
                line = line.strip()
                if line and any(marker in line for marker in markers):
                    anchors.append(line[:220])
        return anchors[:12]

    def _assemble_system_instruction(
        self,
        memories: list[dict[str, Any]],
        artifact: dict[str, Any] | None,
        citations: list[dict[str, Any]],
    ) -> str:
        sections = [read_prompt(self.store, SYSTEM_PROMPT)]
        if memories:
            facts = "\n".join(
                f"- [{memory['kind']}] {memory['memory_key']}：{memory['content']}"
                for memory in memories
            )
            sections.append(f"<memory>\n{facts}\n</memory>")
        if artifact:
            sections.append(f"<history>\n{artifact['content']}\n</history>")
        if citations:
            sources = "\n\n".join(
                f"【知识:{citation['title']}】\n{citation['content'][:1_400]}"
                for citation in citations
            )
            sections.append(f"<knowledge>\n{sources}\n</knowledge>")
        # Memory management rides on the main call: the directive tells the
        # model how to append its ```memory block, and it can name memories
        # only because the <memory> section above listed them. Cleared like
        # any other prompt - a cleared memory prompt is a choice too.
        directive = read_prompt(self.store, MEMORY_PROMPT)
        if directive:
            sections.append(directive)
        # Blank sections are dropped rather than joined, so a cleared prompt
        # leaves the memories or the history opening the message instead of a
        # run of empty lines ahead of them.
        return "\n\n".join(section for section in sections if section)

    def _cost(self, message: dict[str, Any]) -> int:
        """What a stored message counts against the budget: its text and, at
        the shared constant, every image it carries."""
        return estimate_tokens(message["content"]) + len(message_images(message)) * (
            IMAGE_TOKEN_ESTIMATE
        )

    def _fit_messages(
        self, system: str, messages: list[dict[str, Any]], input_budget: int
    ) -> tuple[list[dict[str, Any]], int]:
        """The newest messages that fit, as provider-shaped payloads, with the
        cost that was measured - build()'s estimate and the fitting have to be
        the same number, so the fitting is where it comes from."""
        selected: list[dict[str, Any]] = []
        used = estimate_tokens(system)
        for message in reversed(messages):
            cost = self._cost(message)
            if selected and used + cost > input_budget:
                break
            selected.append(
                self._provider_message(message["role"], message["content"], message_images(message))
            )
            used += cost
        return list(reversed(selected)), used

    def _provider_message(self, role: str, content: str, images: list[str]) -> dict[str, Any]:
        """One message as the wire wants it: a string, or the multimodal shape
        with a part per pasted image.

        The files are read and re-encoded here, at call time, so a conversation
        that has been open for days sends today's bytes. An image whose file has
        disappeared is simply left out - the model answers from what it gets,
        which is what it would do with a broken reference anyway.
        """
        if not images:
            return {"role": role, "content": content}
        parts: list[dict[str, Any]] = []
        if content.strip():
            parts.append({"type": "text", "text": content})
        for filename in images:
            url = image_data_url(self.store.database, filename)
            if url is not None:
                parts.append({"type": "image_url", "image_url": {"url": url}})
        if not parts:
            return {"role": role, "content": content}
        return {"role": role, "content": parts}
