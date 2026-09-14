from __future__ import annotations

import json
import re
from typing import Any

from app.store import Store
from app.utils import normalize_key

ALLOWED_KINDS = {"profile", "preference", "fact", "decision"}

# The tail block the main model may append to a reply: a fenced ```memory
# block running to the end of the text. It is the whole memory mechanism -
# there is no separate extraction call - so the anchor has to be a line of
# its own, and only a block that reaches the very end counts. A ```memory
# fence in the middle of an answer is the answer's own business.
_MEMORY_BLOCK_RE = re.compile(r"(?ms)^[ \t]*```memory[ \t]*\r?\n(?P<body>.*)\Z")


def split_memory_block(text: str) -> tuple[str, str | None]:
    """Split a reply into what the user sees and its trailing memory block.

    A reply that never opened the block comes back whole. One that opened it
    but is still streaming comes back with the tail held back - the fence does
    not need its closing mark to be recognized, which is what keeps a
    half-written JSON blob from ever flashing on screen. The caller decides at
    completion whether the held-back text is a memory block or just an answer
    that happened to end with such a fence, and shows all of it then.
    """
    match = _MEMORY_BLOCK_RE.search(text)
    if match is None:
        return text, None
    return text[: match.start()], match.group("body")


def parse_memory_block(body: str) -> dict[str, Any]:
    """The JSON object inside the block, or an empty one when it is not JSON.

    Lenient on purpose about the wrapper - a stray closing fence, prose around
    the object - and strict about the result: anything that is not an object
    means "no actions", which is the same outcome as a model that said nothing.
    """
    content = body.strip()
    fence = chr(96) * 3
    if content.endswith(fence):
        content = content[: -len(fence)].rstrip()
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class MemoryService:
    """Applies the memory actions a reply carried in its trailing block.

    Deciding whether anything is worth remembering is the main model's job now,
    done in the turn it was already answering; this service only validates and
    stores what it asked for. A reply without a block is the common case and
    costs nothing - which is why a plain greeting no longer pays for an
    extraction call that returns an empty list.
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def apply_reply(
        self,
        *,
        reply: str,
        user_content: str,
        user_message_id: str,
        project_id: str | None,
    ) -> list[dict[str, Any]]:
        """Carry out the write and forget actions in a finished reply."""
        _, body = split_memory_block(reply)
        if body is None:
            return []
        actions = parse_memory_block(body)
        results: list[dict[str, Any]] = []

        writes = actions.get("write")
        if isinstance(writes, list):
            for candidate in writes:
                if not isinstance(candidate, dict):
                    continue
                result = self._store_candidate(candidate, user_content, user_message_id, project_id)
                if result:
                    results.append(result)

        forgets = actions.get("forget")
        if isinstance(forgets, list):
            for entry in forgets:
                result = self._forget(entry, project_id)
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
        """Soft-delete the memory a forget entry names, and say what went.

        The model copies both fields from the <memory> list it was shown, so
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
