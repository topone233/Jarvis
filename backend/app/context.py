from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.errors import ProviderError
from app.images import image_data_url, message_images
from app.knowledge import KnowledgeService
from app.memory import MEMORY_TOOLS
from app.prompts import PROMPT_VERSION
from app.provider import OpenAICompatibleProvider
from app.sections import format_outline_compact, parse_sections
from app.settings import (
    COMPACT_PERCENT_DEFAULT,
    COMPACTION_PROMPT,
    MEMORY_PROMPT,
    SYSTEM_PROMPT,
    read_prompt,
)
from app.store import Store
from app.tokens import IMAGE_TOKEN_ESTIMATE, estimate_tokens
from app.utils import json_dump, json_load

#: A memory joins the injected list only when its similarity to the current
#: query clears the floor, and at most this many do. Tuned by hand and kept as
#: code on purpose: they shape one prompt section, not user-facing behavior.
MEMORY_RELEVANCE_FLOOR = 0.25
MEMORY_RELEVANCE_LIMIT = 6

#: How much of a retrieved chunk rides in the system instruction. Raised from
#: the old 1400 when chunks became section-shaped: a section header plus a
#: whole test case is the unit the model can actually answer from, and a
#: mid-case cut was one of the reasons list answers came back short.
KNOWLEDGE_EXCERPT_CHARS = 2_800


@dataclass(frozen=True)
class ContextBundle:
    messages: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    # The memories injected into the system instruction, kept for the audit
    # trail: the model can only speak about memories it was shown, so the
    # retrieval step reports this list and the screen can say what the memory
    # step had to work with.
    memories: list[dict[str, Any]]
    # How the injected list was chosen: "relevance" (ranked against the
    # query), "forget_bypass" (everything listed so the model can name what
    # to forget), or "fallback" (nothing to rank with - no embedding model,
    # a blank query, or a failed embed call - so everything deduped is shown).
    memory_mode: str
    input_token_estimate: int
    remaining_token_estimate: int
    # The memory tools registered on this turn's request, present only when
    # the memory directive says the model may manage memory at all. Cleared
    # directive - the settings choice - means no tools on the wire.
    tools: list[dict[str, Any]]


def _cosine(left: list[float], right: list[float]) -> float:
    """Plain cosine, guarding the shapes a half-finished cache can produce.

    A dimension mismatch (the embedding endpoint changed width under the same
    model name) or a zero vector is simply "no similarity", which ranks the
    memory out instead of failing the turn.
    """
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


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

    @staticmethod
    def is_forget_request(content: str) -> bool:
        """Whether this turn asks the model to make it forget something.

        A forget entry is copied from the 记忆 list the model was shown,
        so a turn that means to forget must be shown everything, dedup and
        ranking alike. Heuristic on purpose and one-sided: it only widens
        what is injected, never narrows it - a false positive fattens one
        prompt, a false negative would hide a memory the user asked to name.
        """
        normalized = content.lower()
        return "忘" in normalized or "forget" in normalized

    async def build(
        self,
        conversation_id: str,
        profile: dict[str, Any],
        user_query: str,
    ) -> ContextBundle:
        conversation = self.store.get_conversation(conversation_id)
        artifact = self.store.get_latest_context_artifact(conversation_id)
        candidates = self.store.list_memories(
            project_id=conversation["project_id"],
            include_global=True,
        )[:16]
        # An image-only message has no words to search with; an empty query is
        # not an answer to any question, so nothing is looked up at all.
        has_query = bool(user_query.strip())
        query_vector: list[float] | None = None
        if (
            profile.get("embedding_model")
            and has_query
            and candidates
            and not self.is_forget_request(user_query)
        ):
            try:
                query_vector = await self._refresh_memory_vectors(candidates, profile, user_query)
            except ProviderError:
                query_vector = None
        citations = (
            await self.knowledge.search(
                user_query,
                project_id=conversation["project_id"],
                profile=profile,
                query_vector=query_vector,
            )
            if has_query
            else []
        )
        # The number the system instruction shows is the number the screen
        # shows, so it is assigned once here and travels with the citation
        # into the message metadata.
        citations = [{**citation, "number": index + 1} for index, citation in enumerate(citations)]
        raw_messages = self._messages_after_artifact(conversation_id, artifact)
        input_budget = profile["context_window"] - profile["output_token_reserve"]
        # Pass one fits with every candidate listed. Its visible set only
        # grows from here - the final system drops memories, never adds them -
        # so a source it calls visible stays visible, while one it calls
        # hidden gets its memory kept even if the second fit would reveal the
        # source: keeping a duplicate beats dropping a memory's last carrier.
        directive = read_prompt(self.store, MEMORY_PROMPT)
        system = self._assemble_system_instruction(candidates, artifact, citations, directive)
        selected_messages, estimate, visible_ids = self._fit_messages(
            system, raw_messages, input_budget
        )
        memories, mode = self._select_memories(
            candidates, conversation_id, artifact, visible_ids, user_query, query_vector
        )
        if [memory["id"] for memory in memories] != [memory["id"] for memory in candidates]:
            system = self._assemble_system_instruction(memories, artifact, citations, directive)
            selected_messages, estimate, _ = self._fit_messages(system, raw_messages, input_budget)
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
            memory_mode=mode,
            input_token_estimate=estimate,
            remaining_token_estimate=remaining,
            tools=list(MEMORY_TOOLS) if directive else [],
        )

    def _select_memories(
        self,
        candidates: list[dict[str, Any]],
        conversation_id: str,
        artifact: dict[str, Any] | None,
        visible_ids: set[str],
        user_query: str,
        query_vector: list[float] | None,
    ) -> tuple[list[dict[str, Any]], str]:
        """What this turn's 记忆 section holds, and how it was chosen.

        Dedup first - it is pure logic, and a memory it drops never needs a
        vector. Ranking runs on what is left, against the query vector the
        build already paid for.
        """
        if self.is_forget_request(user_query):
            return candidates, "forget_bypass"
        deduped = self._drop_present_memories(candidates, conversation_id, artifact, visible_ids)
        if query_vector is None:
            return deduped, "fallback"
        return self._rank_memories(deduped, query_vector), "relevance"

    def _drop_present_memories(
        self,
        candidates: list[dict[str, Any]],
        conversation_id: str,
        artifact: dict[str, Any] | None,
        visible_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Skip a memory whose information is already in front of the model.

        A memory is the distilled form of one source message. While that
        message is still visible in this turn's window, or has been folded
        into the compaction artifact (a choice the user made: lossy summary
        or not, it counts as present), injecting the memory again is
        duplication. Once the source is gone from both - scrolled out of the
        budget, deleted, or living in another conversation - the memory is
        the fact's only carrier and comes back.
        """
        sources = self.store.get_messages_by_ids(
            memory["source_message_id"] for memory in candidates if memory["source_message_id"]
        )
        placement = {row["id"]: row for row in sources}
        kept: list[dict[str, Any]] = []
        for memory in candidates:
            source = placement.get(memory["source_message_id"])
            if source is None or source["conversation_id"] != conversation_id:
                kept.append(memory)
                continue
            if artifact is not None and source["ordinal"] <= artifact["end_ordinal"]:
                continue
            if source["id"] in visible_ids:
                continue
            kept.append(memory)
        return kept

    async def _refresh_memory_vectors(
        self, memories: list[dict[str, Any]], profile: dict[str, Any], query: str
    ) -> list[float]:
        """Embed the query and every stale memory in one call; cache the rest.

        A memory's vector is cached until its content changes or the profile
        names another embedding model, so the missing ones are paid for once
        and a steady-state turn embeds only the query. Candidates are
        refreshed before dedup drops any of them on purpose: a memory
        suppressed today holds its vector ready for the turn its source
        scrolls out of the window.
        """
        model = profile["embedding_model"]
        stale = [
            memory
            for memory in memories
            if memory.get("embedding_model") != model or memory.get("embedding_json") is None
        ]
        texts = [query, *(memory["content"] for memory in stale)]
        vectors = await self.provider.embed(profile, texts)
        if len(vectors) != len(texts):
            raise ProviderError("嵌入服务返回的向量数量不匹配。")
        for memory, vector in zip(stale, vectors[1:], strict=True):
            self.store.update_memory_embedding(memory["id"], vector, model)
            # The ranking reads the records in hand, not the database again:
            # the write-back has to land in both or the fresh vectors are
            # invisible to the very call that produced them.
            memory["embedding_json"] = json_dump(vector)
            memory["embedding_model"] = model
        return vectors[0]

    @staticmethod
    def _rank_memories(
        memories: list[dict[str, Any]], query_vector: list[float]
    ) -> list[dict[str, Any]]:
        """The memories that clear the relevance floor, best first, capped.

        Ties keep the kind-and-recency order the candidates arrived in, so
        the ranking only ever promotes, never shuffles equal things.
        """
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for index, memory in enumerate(memories):
            vector = json_load(memory.get("embedding_json"), None)
            score = _cosine(query_vector, vector) if vector else 0.0
            if score >= MEMORY_RELEVANCE_FLOOR:
                scored.append((-score, index, memory))
        scored.sort(key=lambda item: (item[0], item[1]))
        return [memory for _, _, memory in scored[:MEMORY_RELEVANCE_LIMIT]]

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
            sections.append(f"## 上一次的压缩摘要\n{previous['content']}")
        source = "\n".join(
            f"[{message['role']} #{message['ordinal']}]\n{message['content']}"
            for message in candidates
        )
        sections.append(f"## 待压缩的对话记录\n{source}")
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
        directive: str,
    ) -> str:
        sections = [read_prompt(self.store, SYSTEM_PROMPT)]
        if memories:
            facts = "\n".join(
                f"- [{memory['kind']}] {memory['memory_key']}：{memory['content']}"
                for memory in memories
            )
            sections.append(f"## 记忆\n{facts}")
        if artifact:
            sections.append(f"## 历史摘要\n{artifact['content']}")
        if citations:
            sections.append(f"## 知识资料\n{self._format_knowledge_section(citations)}")
        # Memory management rides on the main call: the directive tells the
        # model when to use the memory tools, and it can name memories only
        # because the 记忆 section above listed them. Cleared like any other
        # prompt - a cleared memory prompt is a choice too.
        if directive:
            sections.append(directive)
        # Blank sections are dropped rather than joined, so a cleared prompt
        # leaves the memories or the history opening the message instead of a
        # run of empty lines ahead of them.
        return "\n\n".join(section for section in sections if section)

    def _format_knowledge_section(self, citations: list[dict[str, Any]]) -> str:
        """The 知识资料 body: numbered excerpts, then each hit document's map.

        The outline after the excerpts is the "you may be seeing only part of
        it" signal - a document whose sections outnumber the excerpts shown
        tells the model there is more to fetch before it answers a list-type
        question. Outlines are the compact form (depth-shrunk, not cut) so a
        wide document cannot eat the budget the excerpts paid for.

        Documents are read here rather than riding the citations: the same
        list is stored as message metadata for the screen, and a full
        document has no business being copied into every message that cited
        it.
        """
        blocks = [
            "[{number}] 《{title}》 · {section_title}\n{content}".format(
                number=citation["number"],
                title=citation["title"],
                section_title=citation.get("section_title") or "全文",
                content=citation["content"][:KNOWLEDGE_EXCERPT_CHARS],
            )
            for citation in citations
        ]
        outlines: list[str] = []
        seen_documents: set[str] = set()
        for citation in citations:
            document_id = citation["document_id"]
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            document = self.store.get_knowledge_document(document_id)
            sections = parse_sections(document["content"], document["title"])
            lead = (
                "文档《{}》共 {} 节，其余章节可用 knowledge 工具 read 命令按 --section 获取，目录："
            ).format(citation["title"], len(sections))
            outlines.append(
                lead
                + "\n"
                + format_outline_compact(
                    document["title"],
                    document["original_filename"],
                    document["content"],
                    sections,
                )
            )
        parts = ["检索命中的知识片段，编号即回答中的引用编号：", *blocks]
        if outlines:
            parts.extend(outlines)
        return "\n\n".join(parts)

    def _cost(self, message: dict[str, Any]) -> int:
        """What a stored message counts against the budget: its text and, at
        the shared constant, every image it carries."""
        return estimate_tokens(message["content"]) + len(message_images(message)) * (
            IMAGE_TOKEN_ESTIMATE
        )

    def _fit_messages(
        self, system: str, messages: list[dict[str, Any]], input_budget: int
    ) -> tuple[list[dict[str, Any]], int, set[str]]:
        """The newest messages that fit, as provider-shaped payloads, with the
        cost that was measured - build()'s estimate and the fitting have to be
        the same number, so the fitting is where it comes from - plus the ids
        of the messages that made the cut, which the memory dedup reads as
        "visible in this turn's window"."""
        selected: list[dict[str, Any]] = []
        visible_ids: set[str] = set()
        used = estimate_tokens(system)
        for message in reversed(messages):
            cost = self._cost(message)
            if selected and used + cost > input_budget:
                break
            selected.append(
                self._provider_message(message["role"], message["content"], message_images(message))
            )
            visible_ids.add(message["id"])
            used += cost
        return list(reversed(selected)), used, visible_ids

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
