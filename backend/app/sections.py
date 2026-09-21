"""The section model of a knowledge document.

A document's stored content is Markdown-shaped text (extracted from PDF, docx
or imported directly). This module derives, from that text alone, the heading
tree the tools and the context builder navigate by: every heading becomes a
Section with a stable dotted id (`2.3` is the third heading under the second
top-level one). Ids are positional by construction, so documents whose own
numbering is missing, duplicated or inconsistent still address cleanly - and
the LLM passes ids back, never title text, which is what keeps same-named
headings from colliding.

Pure functions on strings; no I/O and no store access, so the parsers and
formatters test without fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: ATX headings only. Every extractor that feeds the knowledge base (mammoth,
#: pymupdf4llm, python-pptx, this module's own importers) emits `#`-style lines,
#: and setext underlines are far too collision-prone to treat as structure.
_HEADING_RE = re.compile(r"^(#{1,6})(?:\s+(.*))?$")

#: How many outline lines the compact formatter may spend on one document.
OUTLINE_MAX_LINES = 60


@dataclass(frozen=True)
class Section:
    id: str
    level: int
    title: str
    #: Offsets into the document content. `end` runs to the next heading at
    #: this level or above, so `[start:end]` is the section including its own
    #: heading line and everything nested beneath it.
    start: int
    end: int


def parse_sections(content: str, fallback_title: str = "全文") -> list[Section]:
    """Every heading as a Section, or one root section for a headingless text.

    Fenced code is transparent: a `#` line inside ``` or ~~~ is a comment, not
    structure, and the parser tracks the open fence marker so a ``` line cannot
    close a ~~~ fence. An unclosed fence runs to end of file - which is the
    reading a broken document deserves.

    Levels are normalized against the shallowest heading the document actually
    uses. A document that marks everything `##` gets top-level sections
    1, 2, 3 - not 0.1, 0.2 - and a document that jumps h2 → h4 nests normally.
    A heading shallower than that minimum is clamped to the top level rather
    than given a negative rank; the document is broken either way, and this
    keeps its ids stable and unique.
    """
    headings: list[tuple[int, int, str]] = []
    offset = 0
    fence: str | None = None
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
        else:
            match = _HEADING_RE.match(stripped)
            if match is not None:
                headings.append((offset, len(match.group(1)), (match.group(2) or "").strip()))
        offset += len(line)
    if not headings:
        return [Section(id="1", level=1, title=fallback_title, start=0, end=len(content))]
    minimum = min(level for _, level, _ in headings)
    sections: list[Section] = []
    # Tree numbering: a heading's id is its parent's path plus its ordinal
    # among the headings under that same parent. Plain per-level counters
    # would print a phantom `0` for a level that never occurred (an h4 under
    # an h2 would be `1.0.1`), and dropping the zeros can collide.
    stack: list[tuple[int, str]] = []
    siblings: dict[str, int] = {}
    for start, level, title in headings:
        rank = max(level - minimum, 0) + 1
        while stack and stack[-1][0] >= rank:
            stack.pop()
        parent_id = stack[-1][1] if stack else ""
        siblings[parent_id] = siblings.get(parent_id, 0) + 1
        section_id = f"{parent_id}.{siblings[parent_id]}" if parent_id else str(siblings[parent_id])
        stack.append((rank, section_id))
        sections.append(
            Section(id=section_id, level=rank, title=title, start=start, end=len(content))
        )
    # A heading closes the section above it when it is that section's level
    # or higher; the ends are fixed in one pass here instead of back-patched
    # while scanning.
    for index, section in enumerate(sections[:-1]):
        following = sections[index + 1]
        if following.level <= section.level:
            sections[index] = Section(
                section.id, section.level, section.title, section.start, following.start
            )
    return sections


def section_text(content: str, sections: list[Section], ids: list[str]) -> list[tuple[str, str]]:
    """The text of the requested sections, in the order they were asked for.

    A missing id is a KeyError on purpose: the tool layer turns it into a
    bash-style error listing what does exist, which is how the model learns
    the address space without a schema to consult.
    """
    by_id = {section.id: section for section in sections}
    return [
        (section_id, content[by_id[section_id].start : by_id[section_id].end]) for section_id in ids
    ]


def section_label(section: Section, by_id: dict[str, Section]) -> str:
    """`1.1 用户管理 · 登录` - the id, then the chain of titles above it.

    The chain stops below the top level: a document's root heading is usually
    its title, which the caller already shows next to the citation. Ancestor
    titles are what disambiguate same-named headings - the case ids exist
    for - without dragging the whole path in on every deep section.
    """
    chain = [section.title] if section.title else []
    cursor = section.id
    while "." in cursor:
        cursor = cursor.rsplit(".", 1)[0]
        ancestor = by_id.get(cursor)
        if ancestor is None or ancestor.level <= 1:
            break
        if ancestor.title:
            chain.append(ancestor.title)
    label = " · ".join(reversed(chain))
    return f"{section.id} {label}".strip()


def _outline_header(
    document_title: str, original_filename: str, content: str, total: int
) -> list[str]:
    lines = content.count("\n") + 1 if content else 0
    size_kb = max(len(content.encode("utf-8")) // 1024, 1)
    return [
        f"FILE: {original_filename or document_title}",
        f"SIZE: {size_kb} KB | LINES: {lines} | SECTIONS: {total}",
        "",
    ]


def _outline_lines(sections: list[Section], max_depth: int) -> list[str]:
    return [
        f"{'#' * section.level} {section.title or '(untitled)'}"
        for section in sections
        if section.level <= max_depth
    ]


def format_outline(
    document_title: str,
    original_filename: str,
    content: str,
    sections: list[Section],
) -> str:
    """The complete outline, one flush-left heading per line.

    Depth is carried by the numbering (`## 2. Order Management` under
    `# SQL Injection Test Cases`), not by indentation - indented `##` lines
    read as code blocks in Markdown renderers and buy nothing to a model
    that already sees the level.
    """
    header = _outline_header(document_title, original_filename, content, len(sections))
    max_depth = max(section.level for section in sections)
    return "\n".join(header + _outline_lines(sections, max_depth)).rstrip()


def format_outline_compact(
    document_title: str,
    original_filename: str,
    content: str,
    sections: list[Section],
) -> str:
    """The outline as it rides inside the system instruction, budgeted.

    Over budget means shallow, not truncated: the deepest level is dropped
    first (h6, then h5, ...) because a coarse but complete map tells the
    model "there is more here" - the very signal the injected context exists
    to send. SECTIONS still reports the full count, so a shrunken outline
    visibly covers less than the document holds. Only a document that will
    not fit even at depth 1 is hard-cut.
    """
    header = _outline_header(document_title, original_filename, content, len(sections))
    budget = OUTLINE_MAX_LINES - len(header)
    max_depth = max(section.level for section in sections)
    lines = _outline_lines(sections, max_depth)
    while len(lines) > budget and max_depth > 1:
        max_depth -= 1
        lines = _outline_lines(sections, max_depth)
    if len(lines) > budget:
        lines = lines[:budget]
    return "\n".join(header + lines).rstrip()
