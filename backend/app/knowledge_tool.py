"""The `knowledge` tool: the model's terminal into the knowledge base.

One tool, four subcommands - list, inspect, read, grep - answered here and
returned to the model as tool output. The surface is deliberately command-like
rather than a forest of JSON tools: the model is deeply trained on reading a
directory tree and reading a file, and a `read` that answers with plain text
meets that training instead of fighting it. It is *not* a shell - there are no
pipes, no globs, no escapes - and the descriptions say so, so the model does
not transfer shell semantics onto it.

Every failure answers as text, in the shape of a shell error, because the
error message is the model's only schema: `knowledge: no such document: x`
with candidates listed teaches the address space without a tutorial round.
"""

from __future__ import annotations

import shlex
from typing import Any

from app.knowledge import KnowledgeService
from app.sections import (
    format_outline,
    parse_sections,
    section_label,
    section_text,
)
from app.store import Store

#: A single `read` answer. A full document can far exceed this; the cap comes
#: with a note that says so, which is what routes the model to --section.
READ_MAX_CHARS = 20_000

#: How much of each grep hit is shown - enough to judge, not enough to make
#: reading the section pointless.
GREP_EXCERPT_CHARS = 240

USAGE = (
    "用法：list | inspect <doc> | read <doc> [--section 2.3]... "
    "[--sections 1.2,2.3] | grep <query> [doc]"
)

KNOWLEDGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "knowledge",
            "description": (
                "查询知识库的工具（不是 shell，没有管道和通配符）。"
                "list：列出知识库里的全部文档；inspect <doc>：看一个文档的完整目录；"
                "read <doc> [--section 2.3]：读全文或指定章节，章节 id 来自目录；"
                "grep <query> [doc]：按关键词检索知识片段（语义+关键词混合检索，不是正则）。"
                "文档参数可以传 id 或标题的一部分；回答清单类问题前，先用它补全知识资料之外的内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "一条命令，例如 read sql-injection --section 2.3。" + USAGE
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
]


class KnowledgeToolService:
    """Parses and answers one `knowledge` command per call.

    Stateless on purpose: the multi-round loop in runs.py owns call history
    and repetition guards, so the service stays a pure command interpreter
    that is trivial to test.
    """

    def __init__(self, store: Store, knowledge: KnowledgeService) -> None:
        self.store = store
        self.knowledge = knowledge

    async def execute(
        self,
        command: str,
        *,
        project_id: str | None,
        profile: dict[str, Any] | None,
    ) -> str:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return "knowledge: 命令解析失败（引号不匹配）。" + USAGE
        if not tokens:
            return "knowledge: 空命令。" + USAGE
        name, *arguments = tokens
        if name == "list":
            return self._list(project_id)
        if name == "inspect":
            return self._inspect(arguments, project_id)
        if name == "read":
            return self._read(arguments, project_id)
        if name == "grep":
            return await self._grep(arguments, project_id, profile)
        return f"knowledge: 未知命令 {name}。" + USAGE

    # --- subcommands -----------------------------------------------------

    def _list(self, project_id: str | None) -> str:
        documents = self.store.list_knowledge_documents(project_id)
        if not documents:
            return "knowledge: 知识库为空，没有任何文档。"
        lines = [f"knowledge: 共 {len(documents)} 份文档："]
        for document in documents:
            full = self.store.get_knowledge_document(document["id"])
            sections = parse_sections(full["content"], full["title"])
            lines.append(
                (
                    "id={id} 《{title}》 FILE: {file} SIZE: {size} "
                    "LINES: {lines} SECTIONS: {sections}"
                ).format(
                    id=full["id"],
                    title=full["title"],
                    file=full["original_filename"],
                    size=max(len(full["content"].encode("utf-8")) // 1024, 1),
                    lines=full["content"].count("\n") + 1 if full["content"] else 0,
                    sections=len(sections),
                )
            )
        return "\n".join(lines)

    def _inspect(self, arguments: list[str], project_id: str | None) -> str:
        ok, selector = self._single_selector(arguments, "inspect")
        if not ok:
            return selector
        document = self._document(selector, project_id)
        if isinstance(document, str):
            return document
        sections = parse_sections(document["content"], document["title"])
        return format_outline(
            document["title"], document["original_filename"], document["content"], sections
        )

    def _read(self, arguments: list[str], project_id: str | None) -> str:
        selector: str | None = None
        wanted: list[str] = []
        index = 0
        while index < len(arguments):
            token = arguments[index]
            if token in ("--section", "--sections"):
                if index + 1 >= len(arguments):
                    return f"knowledge: {token} 缺少章节 id。" + USAGE
                wanted.extend(
                    part.strip() for part in arguments[index + 1].split(",") if part.strip()
                )
                index += 2
            elif token == "--full":
                index += 1
            elif token.startswith("--"):
                return f"knowledge: 未知选项 {token}。" + USAGE
            else:
                if selector is not None:
                    return "knowledge: read 只接受一个文档。" + USAGE
                selector = token
                index += 1
        if selector is None:
            return "knowledge: read 缺少文档。" + USAGE
        document = self._document(selector, project_id)
        if isinstance(document, str):
            return document
        content = document["content"]
        if wanted:
            sections = parse_sections(content, document["title"])
            try:
                parts = section_text(content, sections, wanted)
            except KeyError as error:
                return f"knowledge: no such section: {error.args[0]}。" + self._available_sections(
                    document
                )
            body = "\n\n".join(f"【{section_id}】\n{text}" for section_id, text in parts)
        else:
            body = content
        if len(body) > READ_MAX_CHARS:
            body = (
                body[:READ_MAX_CHARS] + "\n\n…（输出因过长被截断。请改用 --section 分章节读取，"
                "或用 inspect 查看目录后选择需要的章节。）"
            )
        return body if body.strip() else "knowledge: 该章节没有正文。"

    async def _grep(
        self,
        arguments: list[str],
        project_id: str | None,
        profile: dict[str, Any] | None,
    ) -> str:
        if not arguments:
            return "knowledge: grep 缺少检索词。" + USAGE
        query = arguments[0]
        document_id: str | None = None
        if len(arguments) > 1:
            document = self._document(arguments[1], project_id)
            if isinstance(document, str):
                return document
            document_id = document["id"]
        hits = await self.knowledge.search(query, project_id=project_id, profile=profile)
        if document_id is not None:
            hits = [hit for hit in hits if hit["document_id"] == document_id]
        if not hits:
            return f"knowledge: grep '{query}' 没有命中。"
        lines = [f"knowledge: grep '{query}' 命中 {len(hits)} 条（混合检索，非字面匹配）："]
        for position, hit in enumerate(hits, start=1):
            where = hit.get("section_title") or "全文"
            lines.append(f"[{position}] 《{hit['title']}》 {where} score {hit['score']}")
            excerpt = hit["content"][:GREP_EXCERPT_CHARS]
            if len(hit["content"]) > GREP_EXCERPT_CHARS:
                excerpt += "…"
            lines.append(f"  {excerpt}")
        lines.append("用 read <doc> --section <id> 可读取命中章节的完整内容。")
        return "\n".join(lines)

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _single_selector(arguments: list[str], command: str) -> tuple[bool, str]:
        """(False, error text) or (True, the selector) - a selector is itself
        an arbitrary string, so the two outcomes cannot share a type."""
        if len(arguments) != 1:
            return False, f"knowledge: {command} 需要恰好一个文档参数。" + USAGE
        return True, arguments[0]

    def _document(self, selector: str, project_id: str | None) -> dict[str, Any] | str:
        """The document a selector names, or the error text that says why not.

        Ids first (exact, then prefix - the model tends to shorten), then a
        unique case-insensitive hit on title or filename. Several candidates
        is not an error to swallow: they are listed, with ids, so the next
        call can be precise.
        """
        documents = self.store.list_knowledge_documents(project_id)
        exact = [d for d in documents if d["id"] == selector]
        if exact:
            return self.store.get_knowledge_document(exact[0]["id"])
        prefixed = [d for d in documents if d["id"].startswith(selector)]
        if len(prefixed) == 1:
            return self.store.get_knowledge_document(prefixed[0]["id"])
        needle = selector.casefold()
        by_title = [
            d
            for d in documents
            if needle in d["title"].casefold() or needle in d["original_filename"].casefold()
        ]
        candidates = prefixed if len(prefixed) > 1 else by_title
        if len(candidates) == 1:
            return self.store.get_knowledge_document(candidates[0]["id"])
        if not candidates:
            return f"knowledge: no such document: {selector}。"
        listing = "\n".join(f"  id={d['id']} 《{d['title']}》" for d in candidates[:10])
        return f"knowledge: 有 {len(candidates)} 份文档匹配 {selector}，请用完整 id：\n{listing}"

    def _available_sections(self, document: dict[str, Any]) -> str:
        sections = parse_sections(document["content"], document["title"])
        by_id = {section.id: section for section in sections}
        ids = ", ".join(section_label(section, by_id).split(" ", 1)[0] for section in sections)
        return f"可用章节：{ids}"
