from __future__ import annotations

import hashlib
import math
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from charset_normalizer import from_bytes

from app.errors import ProviderError, ValidationError
from app.provider import OpenAICompatibleProvider
from app.store import Store
from app.tokens import estimate_tokens
from app.utils import safe_filename, segment_for_index

ALLOWED_EXTENSIONS = {
    ".bat",
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".csv",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".markdown",
    ".php",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class ImportItem:
    filename: str
    content: bytes
    relative_path: str | None = None


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    detected = from_bytes(content).best()
    if detected is None:
        raise ValidationError("无法识别文件编码，请仅导入文本或源码文件。")
    return str(detected)


def _split_long_text(value: str, target_size: int, overlap_size: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + target_size)
        if end < len(value):
            split_at = value.rfind("\n", start + target_size // 2, end)
            if split_at > start:
                end = split_at
        part = value[start:end].strip()
        if part:
            chunks.append(part)
        if end == len(value):
            break
        start = max(end - overlap_size, start + 1)
    return chunks


def chunk_text(value: str, target_size: int = 1_800, overlap_size: int = 220) -> list[str]:
    sections = re.split(r"\n\s*\n", value)
    chunks: list[str] = []
    current = ""
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) > target_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_text(section, target_size, overlap_size))
            continue
        candidate = f"{current}\n\n{section}".strip() if current else section
        if len(candidate) <= target_size:
            current = candidate
        else:
            chunks.append(current)
            current = section
    if current:
        chunks.append(current)
    return chunks


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0


class KnowledgeService:
    def __init__(self, store: Store, provider: OpenAICompatibleProvider) -> None:
        self.store = store
        self.provider = provider

    async def import_items(
        self,
        items: list[ImportItem],
        *,
        project_id: str | None,
        profile: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not items:
            raise ValidationError("请至少选择一个文件。")
        results: list[dict[str, Any]] = []
        for item in items:
            suffix = Path(item.filename).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                results.append(
                    {
                        "filename": item.filename,
                        "status": "skipped",
                        "reason": "当前仅支持文本、Markdown 和常见源码文件。",
                    }
                )
                continue
            if not item.content:
                results.append(
                    {"filename": item.filename, "status": "skipped", "reason": "文件为空。"}
                )
                continue
            try:
                text = _decode_text(item.content)
            except ValidationError as error:
                results.append(
                    {"filename": item.filename, "status": "skipped", "reason": str(error)}
                )
                continue
            content_hash = hashlib.sha256(item.content).hexdigest()
            document_id_hint = hashlib.sha1(
                f"{item.filename}:{content_hash}".encode(), usedforsecurity=False
            ).hexdigest()[:12]
            stored_filename = f"{document_id_hint}_{safe_filename(item.filename)}"
            stored_path = self.store.database.objects_directory / stored_filename
            stored_path.write_bytes(item.content)
            document = self.store.create_knowledge_document(
                project_id=project_id,
                title=Path(item.filename).stem or item.filename,
                original_filename=item.filename,
                relative_path=item.relative_path,
                mime_type=mimetypes.guess_type(item.filename)[0] or "text/plain",
                content_hash=content_hash,
                stored_path=str(stored_path),
            )
            parts = chunk_text(text)
            payloads: list[dict[str, Any]] = [
                {"position": index, "content": part, "token_estimate": estimate_tokens(part)}
                for index, part in enumerate(parts)
            ]
            status = "ready"
            embedding_model: str | None = None
            if profile and profile.get("embedding_model"):
                try:
                    vectors = await self.provider.embed(
                        profile, [part["content"] for part in payloads]
                    )
                    if len(vectors) != len(payloads):
                        raise ProviderError("嵌入服务返回的向量数量不匹配。")
                    for payload, vector in zip(payloads, vectors, strict=True):
                        payload["embedding"] = vector
                    embedding_model = profile["embedding_model"]
                except ProviderError as error:
                    status = "ready_without_embeddings"
                    results.append(
                        {
                            "filename": item.filename,
                            "status": "warning",
                            "reason": f"已完成关键词索引，语义索引暂不可用：{error}",
                        }
                    )
            self.store.create_knowledge_chunks(
                document["id"], project_id, payloads, embedding_model
            )
            completed = self.store.finish_knowledge_document(document["id"], status, len(payloads))
            results.append({"filename": item.filename, "status": status, "document": completed})
        return results

    async def search(
        self,
        query: str,
        *,
        project_id: str | None,
        profile: dict[str, Any] | None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        fts_query = self._fts_query(query)
        fts_results = self.store.search_knowledge_fts(fts_query, project_id) if fts_query else []
        scored: dict[str, tuple[float, dict[str, Any]]] = {}
        fts_ids: set[str] = set()
        for index, item in enumerate(fts_results):
            fts_ids.add(item["id"])
            scored[item["id"]] = (0.45 / (index + 1), item)
        if profile and profile.get("embedding_model"):
            try:
                query_vector = (await self.provider.embed(profile, [query]))[0]
                vectors = self.store.get_knowledge_chunks_with_embeddings(
                    project_id, profile["embedding_model"]
                )
                for item in vectors:
                    score = max(_cosine(query_vector, item["embedding"]), 0) * 0.75
                    existing = scored.get(item["id"])
                    scored[item["id"]] = (score + (existing[0] if existing else 0), item)
            except (ProviderError, IndexError):
                pass
        ordered = sorted(scored.values(), key=lambda item: item[0], reverse=True)[:limit]
        return [
            {
                "chunk_id": item["id"],
                "document_id": item["document_id"],
                "title": item.get("document_title", "未命名文档"),
                "content": item["content"],
                "score": round(score, 4),
                "source": "hybrid" if item["id"] in fts_ids else "semantic",
            }
            for score, item in ordered
        ]

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = re.findall(r"[\w\u4e00-\u9fff]+", segment_for_index(query))
        return " OR ".join(f'"{term}"' for term in terms[:12])
