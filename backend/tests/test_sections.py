from __future__ import annotations

import pytest

from app.sections import (
    Section,
    format_outline,
    format_outline_compact,
    parse_sections,
    section_label,
    section_text,
)


def ids_of(content: str) -> list[str]:
    return [section.id for section in parse_sections(content)]


def test_nested_headings_get_positional_ids() -> None:
    content = "\n".join(
        [
            "# SQL Injection",
            "## 1. User Management",
            "### 1.1 Login",
            "### 1.2 Search",
            "## 2. Order Management",
            "### 2.1 Search",
        ]
    )
    assert ids_of(content) == ["1", "1.1", "1.1.1", "1.1.2", "1.2", "1.2.1"]


def test_level_skips_reset_the_deeper_counters() -> None:
    assert ids_of("## a\n#### b\n#### c\n## d") == ["1", "1.1", "1.2", "2"]


def test_fenced_code_does_not_produce_sections() -> None:
    content = "# real\n```python\n# not a heading\nx = 1\n```\n~~~\n# also not\n~~~\n# after"
    assert ids_of(content) == ["1", "2"]
    # A fence with a different marker must not be closed by the other kind.
    mixed = "# a\n~~~\n```\n# b\n~~~\n# c"
    assert ids_of(mixed) == ["1", "2"]


def test_unclosed_fence_consumes_the_rest() -> None:
    assert ids_of("# a\n```python\n# b") == ["1"]


def test_offsets_roundtrip_through_section_text() -> None:
    content = "# chapter\n## one\nbody one\n## two\nbody two\n# chapter 2\nmore"
    sections = parse_sections(content)
    text = dict(section_text(content, sections, ["1.1", "1.2", "2"]))
    assert text["1.1"] == "## one\nbody one\n"
    assert text["1.2"] == "## two\nbody two\n"
    assert text["2"] == "# chapter 2\nmore"


def test_headingless_document_is_one_root_section() -> None:
    content = "plain notes\nno structure at all"
    sections = parse_sections(content, "笔记")
    assert sections == [Section(id="1", level=1, title="笔记", start=0, end=len(content))]
    assert section_text(content, sections, ["1"])[0][1] == content


def test_duplicate_titles_do_not_collide() -> None:
    content = "# doc\n## search\nalpha\n## other\n## search\nbeta"
    sections = parse_sections(content)
    text = dict(section_text(content, sections, ["1.1", "1.3"]))
    assert "alpha" in text["1.1"]
    assert "beta" in text["1.3"]


def test_unknown_id_is_a_key_error() -> None:
    sections = parse_sections("# a")
    with pytest.raises(KeyError):
        section_text("x", sections, ["9.9"])


def test_section_label_prefers_the_parent_when_titles_repeat() -> None:
    sections = parse_sections("# doc\n## user\n### search\n## order\n### search")
    by_id = {section.id: section for section in sections}
    assert section_label(by_id["1.1.1"], by_id) == "1.1.1 user · search"
    assert section_label(by_id["1.2.1"], by_id) == "1.2.1 order · search"


def test_outline_is_flush_left() -> None:
    content = "# title\n## one\n### deep"
    sections = parse_sections(content)
    outline = format_outline("t", "t.md", content, sections)
    for line in outline.splitlines():
        assert not line.startswith(" "), line
    assert "### deep" in outline
    assert "SECTIONS: 3" in outline
    assert "FILE: t.md" in outline


def test_compact_outline_shrinks_by_depth_instead_of_cutting() -> None:
    content = "\n".join(
        ["# top"]
        + [f"## chapter {i}" for i in range(1, 30)]
        + [f"### section {i}" for i in range(1, 100)]
    )
    sections = parse_sections(content)
    outline = format_outline_compact("t", "t.md", content, sections)
    lines = outline.splitlines()
    # Depth-shrunk until it fits, and the total section count still says how
    # much the document holds.
    assert len(lines) <= 60
    assert "### section" not in outline
    assert "SECTIONS: 129" in outline
    assert lines[-1].startswith("## ")


def test_compact_outline_hard_cuts_only_when_depth_one_still_overflows() -> None:
    content = "\n".join(f"# heading {i}" for i in range(1, 100))
    sections = parse_sections(content)
    outline = format_outline_compact("t", "t.md", content, sections)
    assert len(outline.splitlines()) <= 60
