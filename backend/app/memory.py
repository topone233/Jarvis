from __future__ import annotations

import json
import re
from typing import Any

from app.prompts import MEMORY_EXTRACTION_INSTRUCTION
from app.provider import OpenAICompatibleProvider
from app.store import Store
from app.utils import normalize_key

ALLOWED_KINDS = {"profile", "preference", "fact", "decision"}


def _parse_candidates(value: str) -> list[dict[str, Any]]:
    content = value.strip()
    fence = chr(96) * 3
    if content.startswith(fence):
        content = re.sub(
            rf"^{re.escape(fence)}(?:json)?\s*|\s*{re.escape(fence)}$",
            "",
            content,
            flags=re.IGNORECASE,
        )
    start = content.find("[")
    end = content.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


class MemoryService:
    def __init__(self, store: Store, provider: OpenAICompatibleProvider) -> None:
        self.store = store
        self.provider = provider

    async def extract_and_store(
        self,
        *,
        profile: dict[str, Any],
        user_content: str,
        user_message_id: str,
        project_id: str | None,
    ) -> list[dict[str, Any]]:
        response = await self.provider.complete_chat(
            profile,
            [
                {"role": "system", "content": MEMORY_EXTRACTION_INSTRUCTION},
                {"role": "user", "content": user_content},
            ],
        )
        results: list[dict[str, Any]] = []
        for candidate in _parse_candidates(response):
            result = self._store_candidate(candidate, user_content, user_message_id, project_id)
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
