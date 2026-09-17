from __future__ import annotations

import json
from typing import Any

from app.store import Store
from app.utils import normalize_key

ALLOWED_KINDS = {"profile", "preference", "fact", "decision"}

# The memory tools ride the main call as native function calling: registered
# on the request, answered by an assistant message that carries content and
# tool_calls side by side. No result is ever sent back - a call is a
# instruction to record, not a question to answer - so the tool descriptions
# are where the "how" lives that a prompt protocol used to spell out.
MEMORY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "保存一条跨会话的记忆。只保存用户明确陈述的事实、稳定偏好或明确决定，"
                "不要根据语气、身份、兴趣等进行推测。与已有记忆键名相同且内容相同就不要调用，"
                "系统会自动确认；内容变了就保存同一个键的新内容，系统会自动替换旧条目。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["profile", "preference", "fact", "decision"],
                        "description": "记忆类别：画像、偏好、事实或决定",
                    },
                    "key": {
                        "type": "string",
                        "description": "简短的键名，同一主题跨会话复用同一个键",
                    },
                    "content": {
                        "type": "string",
                        "description": "要记住的事实内容",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0 到 1 之间的把握程度，可选",
                    },
                },
                "required": ["kind", "key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": (
                "忘记一条已有的记忆。key 和 content 都要从对话里看到的记忆列表原样复制，"
                "系统靠它们定位要删的是哪一条；列表里没有的记忆不能编造。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "要忘掉的记忆的键名"},
                    "content": {"type": "string", "description": "那条记忆的内容，原样复制"},
                },
                "required": ["key", "content"],
            },
        },
    },
]


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """The arguments object of one tool call, or an empty one when broken.

    The wire carries arguments as a JSON string. A model that garbled them
    yields no action at all, which is the same outcome as a model that said
    nothing - there is nothing to show the user for a call it never sees.
    """
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class MemoryService:
    """Applies the memory actions a reply's tool calls asked for.

    Deciding whether anything is worth remembering is the main model's job,
    done in the turn it was already answering; this service only validates
    and stores what it asked for. A reply without tool calls is the common
    case and costs nothing - no extra request, no audit step.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def apply_tool_calls(
        self,
        calls: list[dict[str, Any]],
        *,
        user_content: str,
        user_message_id: str,
        project_id: str | None,
    ) -> list[dict[str, Any]]:
        """Carry out the save and forget actions a finished reply's calls made."""
        results: list[dict[str, Any]] = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", "")).strip()
            arguments = _parse_arguments(call.get("arguments"))
            if not arguments:
                continue
            if name == "save_memory":
                result = self._store_candidate(arguments, user_content, user_message_id, project_id)
            elif name == "forget_memory":
                result = self._forget(arguments, project_id)
            else:
                result = None
            if result:
                results.append(result)
        return results

    def _store_candidate(
        self,
        candidate: dict[str, Any],
        user_content: str,
        source_message_id: str,
        project_id: str | None,
    ) -> dict[str, Any] | None:
        kind = str(candidate.get("kind", "")).strip().lower()
        key = str(candidate.get("key", "")).strip()
        content = str(candidate.get("content", "")).strip()
        if kind not in ALLOWED_KINDS or not key or not content:
            return None
        if len(key) > 200 or len(content) > 5_000:
            return None
        try:
            confidence = min(max(float(candidate.get("confidence", 0.7)), 0), 1)
        except (TypeError, ValueError):
            confidence = 0.7
        scope = "global" if kind in {"profile", "preference"} or project_id is None else "project"
        scoped_project_id = None if scope == "global" else project_id
        normalized = normalize_key(key)
        existing = self.store.find_active_memory(scope, scoped_project_id, normalized)
        if existing and normalize_key(existing["content"]) == normalize_key(content):
            return {"action": "confirmed", "memory": self.store.confirm_memory(existing["id"])}
        successor = self.store.create_memory(
            scope=scope,
            project_id=scoped_project_id,
            kind=kind,
            memory_key=key,
            content=content,
            normalized_key=normalized,
            confidence=confidence,
            source_message_id=source_message_id,
            source_excerpt=user_content[:600],
        )
        if existing:
            self.store.supersede_memory(existing["id"], successor["id"])
            return {
                "action": "superseded",
                "memory": successor,
                "previous_memory_id": existing["id"],
            }
        return {"action": "created", "memory": successor}

    def _forget(self, entry: Any, project_id: str | None) -> dict[str, Any] | None:
        """Soft-delete the memory a forget call names, and say what went.

        The model copies both fields from the memory list it was shown, so
        the key narrows the candidates and the content picks between them -
        the case that matters is a global and a project memory sharing a key.
        With no content match the key alone would be too broad, so nothing is
        deleted and the miss shows up as a missing action rather than as a
        wrong deletion.
        """
        if not isinstance(entry, dict):
            return None
        key = str(entry.get("key", "")).strip()
        if not key:
            return None
        candidates = self.store.find_active_memories_by_key(normalize_key(key), project_id)
        if not candidates:
            return None
        wanted = normalize_key(str(entry.get("content", "")).strip())
        if wanted:
            exact = [m for m in candidates if normalize_key(m["content"]) == wanted]
            if exact:
                candidates = exact
            else:
                partial = [m for m in candidates if wanted in normalize_key(m["content"])]
                if partial:
                    candidates = partial
                else:
                    return None
        for memory in candidates:
            self.store.delete_memory(memory["id"])
        return {"action": "forgotten", "memory": candidates[0], "count": len(candidates)}
