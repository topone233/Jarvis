from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from app import knowledge as knowledge_module
from app.knowledge import ImportItem
from app.runtime import CoreServices
from app.utils import segment_for_index


@pytest.mark.asyncio
async def test_import_and_hybrid_search(core: CoreServices, profile: dict[str, Any]) -> None:
    project = core.store.create_project("Jarvis", False)
    imported = await core.knowledge.import_items(
        [
            ImportItem(
                filename="architecture.md",
                content="Jarvis 使用 SQLite 保存本地知识，并支持上下文压缩。".encode(),
                relative_path="notes/architecture.md",
            )
        ],
        project_id=project["id"],
        profile=profile,
    )

    assert imported[-1]["status"] == "ready"
    document = imported[-1]["document"]
    assert document["chunk_count"] == 1

    results = await core.knowledge.search(
        "SQLite 上下文",
        project_id=project["id"],
        profile=profile,
    )

    assert results
    assert results[0]["document_id"] == document["id"]
    assert "SQLite" in results[0]["content"]


@pytest.mark.asyncio
async def test_chinese_query_matches_a_sub_phrase(core: CoreServices) -> None:
    """Keyword search must reach inside a CJK run, not only match whole runs."""
    project = core.store.create_project("中文检索", False)
    await core.knowledge.import_items(
        [
            ImportItem(
                filename="notes.md",
                content="Jarvis 使用 SQLite 保存本地知识，并支持上下文压缩。".encode(),
            )
        ],
        project_id=project["id"],
        profile=None,
    )

    for query in ("上下文", "压缩", "上下文压缩"):
        results = await core.knowledge.search(query, project_id=project["id"], profile=None)
        assert results, f"未命中：{query}"

    assert not await core.knowledge.search("记忆", project_id=project["id"], profile=None)


def test_segment_for_index_expands_cjk_runs_into_bigrams() -> None:
    assert segment_for_index("上下文") == "上下 下文"
    assert segment_for_index("记忆") == "记忆"
    assert segment_for_index("SQLite 上下文压缩") == "SQLite 上下 下文 文压 压缩"
    assert segment_for_index("plain english text") == "plain english text"


@pytest.mark.asyncio
async def test_import_never_writes_the_source_file(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="note.txt", content=b"original content")],
        project_id=None,
        profile=None,
    )
    document = imported[-1]["document"]

    assert document["stored_path"].startswith(str(core.database.objects_directory))
    assert core.database.objects_directory.joinpath(
        document["stored_path"].split("\\")[-1]
    ).exists()


@pytest.mark.asyncio
async def test_import_stores_content_and_section_shaped_chunks(core: CoreServices) -> None:
    content = "# 指南\n## 登录\n账号密码登录的用例。\n## 上传\n附件上传的用例。\n"
    imported = await core.knowledge.import_items(
        [ImportItem(filename="guide.md", content=content.encode())],
        project_id=None,
        profile=None,
    )
    document = imported[-1]["document"]

    stored = core.store.get_knowledge_document(document["id"])
    assert stored["content"] == content

    chunks = core.database.fetchall(
        "SELECT section_id, content FROM knowledge_chunks"
        " WHERE document_id = ? AND deleted_at IS NULL ORDER BY position",
        (document["id"],),
    )
    assert [row["section_id"] for row in chunks] == ["1.1", "1.2"]
    assert chunks[0]["content"].startswith("【1.1 登录】")
    assert "账号密码" in chunks[0]["content"]


@pytest.mark.asyncio
async def test_search_reports_the_section_of_a_hit(core: CoreServices) -> None:
    content = "# doc\n## 用户管理\n登录的测试用例。\n## 订单管理\n下单的测试用例。\n"
    imported = await core.knowledge.import_items(
        [ImportItem(filename="cases.md", content=content.encode())],
        project_id=None,
        profile=None,
    )
    document = imported[-1]["document"]

    results = await core.knowledge.search("登录 测试用例", project_id=None, profile=None)
    assert results
    assert results[0]["document_id"] == document["id"]
    assert results[0]["section_id"] == "1.1"
    assert "用户管理" in results[0]["section_title"]


# --- Document fixtures -------------------------------------------------------
#
# Hand-built rather than checked-in binaries, so every test states the file
# shape it depends on and none of them age out of sync with a blob.


def _zip_of(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _docx(paragraphs: list[str], *, with_image: bool = False) -> bytes:
    """A minimal .docx: the parts mammoth needs and nothing else.

    With `with_image`, a paragraph also carries a drawing whose blip
    references rId5, so a test can prove the extracted text keeps only words.
    """
    drawing = (
        "<w:p><w:r><w:drawing>"
        '<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:blipFill><a:blip r:embed="rId5"/></pic:blipFill>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        if with_image
        else ""
    )
    body = drawing + "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return _zip_of(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType='
                '"application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="png" ContentType="image/png"/>'
                '<Override PartName="/word/document.xml" ContentType='
                '"application/vnd.openxmlformats-officedocument.wordprocessingml'
                '.document.main+xml"/>'
                "</Types>"
            ),
            "_rels/.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type='
                '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                'officeDocument" Target="word/document.xml"/></Relationships>'
            ),
            "word/_rels/document.xml.rels": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId5" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                'Target="media/image1.png"/></Relationships>'
            ),
            "word/media/image1.png": b"\x89PNG-fixture-bytes",
            "word/document.xml": (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
                ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<w:body>{body}</w:body></w:document>"
            ),
        }
    )


def _xlsx(sheets: dict[str, list[list[Any]]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    # A fresh workbook always has one sheet; the stubs hedge, so narrow it.
    first = workbook.active
    if first is not None:
        workbook.remove(first)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _pptx() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1))
    box.text_frame.text = "发布会时间表"
    second = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(1))
    second.text_frame.text = "Quarterly roadmap review"
    slide.notes_slide.notes_text_frame.text = "备注：先讲预算"
    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _pdf(pages: list[str], *, font: str = "Helvetica") -> bytes:
    """A minimal PDF with one text line per page, xref offsets computed for real."""
    objects: list[bytes | None] = []

    def add(body: bytes | None) -> int:
        objects.append(body)
        return len(objects)

    catalog_id = add(None)
    pages_id = add(None)
    if font == "Helvetica":
        font_object = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    else:
        # A non-embedded CID font: pdfminer reads it through its bundled
        # CMaps, so a Chinese fixture needs no font file at all.
        font_object = (
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H "
            b"/DescendantFonts [<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>] >>"
        )
    font_id = add(font_object)
    kids: list[int] = []
    for text in pages:
        if font == "Helvetica":
            encoded = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        else:
            # UniGB-UCS2-H takes UTF-16BE code units as hex strings.
            hex_text = text.encode("utf-16-be").hex().upper()
            encoded = f"BT /F1 12 Tf 72 720 Td <{hex_text}> Tj ET".encode()
        stream_id = add(
            b"<< /Length "
            + str(len(encoded)).encode()
            + b" >>\nstream\n"
            + encoded
            + b"\nendstream"
        )
        page = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {stream_id} 0 R >>"
        ).encode()
        kids.append(add(page))
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{kid} 0 R' for kid in kids)}] /Count {len(kids)} >>"
    ).encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{object_id} 0 obj\n".encode() + (body or b"<< >>") + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF"
    ).encode()
    return bytes(out)


# --- Office and PDF imports --------------------------------------------------


@pytest.mark.asyncio
async def test_docx_import_extracts_paragraphs(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="设计.docx", content=_docx(["Jarvis 的知识库设计", "Local first"]))],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"
    document = imported[-1]["document"]
    assert document["chunk_count"] == 1

    results = await core.knowledge.search("知识库", project_id=None, profile=None)
    assert results
    assert results[0]["document_id"] == document["id"]


@pytest.mark.asyncio
async def test_docx_import_keeps_words_but_not_picture_bytes(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="带图.docx", content=_docx(["知识库正文"], with_image=True))],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    results = await core.knowledge.search("知识库正文", project_id=None, profile=None)
    assert results
    assert "data:image" not in results[0]["content"]


@pytest.mark.asyncio
async def test_xlsx_import_extracts_sheet_rows(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [
            ImportItem(
                filename="预算.xlsx",
                content=_xlsx({"二月": [["项目", "金额"], ["服务器", 3200], ["会议", 500]]}),
            )
        ],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    results = await core.knowledge.search("服务器", project_id=None, profile=None)
    assert results
    assert "3200" in results[0]["content"]


@pytest.mark.asyncio
async def test_xlsx_import_truncates_huge_sheets(core: CoreServices) -> None:
    rows = [["项目", "金额"]] + [[f"值{index}", index] for index in range(5_100)]
    imported = await core.knowledge.import_items(
        [ImportItem(filename="大表.xlsx", content=_xlsx({"流水": rows}))],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    hits = await core.knowledge.search("值4998", project_id=None, profile=None)
    assert hits
    assert not await core.knowledge.search("值4999", project_id=None, profile=None)


@pytest.mark.asyncio
async def test_pptx_import_extracts_slides_and_notes(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="评审.pptx", content=_pptx())],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    results = await core.knowledge.search("roadmap", project_id=None, profile=None)
    assert results
    assert "备注：先讲预算" in results[0]["content"]


@pytest.mark.asyncio
async def test_pdf_import_extracts_pages(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [
            ImportItem(
                filename="notes.pdf", content=_pdf(["Jarvis knowledge base", "retrieval page two"])
            )
        ],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    results = await core.knowledge.search("retrieval", project_id=None, profile=None)
    assert results
    assert "第 2 页" in results[0]["content"]


@pytest.mark.asyncio
async def test_pdf_import_reads_chinese_through_a_cid_font(core: CoreServices) -> None:
    """pdfminer's bundled CMaps must carry Chinese text without a font file."""
    imported = await core.knowledge.import_items(
        [ImportItem(filename="中文.pdf", content=_pdf(["知识库存放本地文档"], font="STSong"))],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "ready"

    results = await core.knowledge.search("知识库", project_id=None, profile=None)
    assert results


@pytest.mark.asyncio
async def test_textless_pdf_is_reported_as_scanned(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="扫描.pdf", content=_pdf([]))],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "skipped"
    assert "扫描件" in imported[-1]["reason"]


@pytest.mark.asyncio
async def test_legacy_office_formats_ask_for_a_resave(core: CoreServices) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="旧文档.doc", content=b"whatever")],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "skipped"
    assert "另存为" in imported[-1]["reason"]


@pytest.mark.asyncio
async def test_mislabeled_documents_are_skipped_not_fatal(core: CoreServices) -> None:
    results = await core.knowledge.import_items(
        [
            ImportItem(filename="假的.docx", content=b"this is not a zip file"),
            ImportItem(filename="截断.pdf", content=b"%PDF-1.4\n"),
        ],
        project_id=None,
        profile=None,
    )
    assert all(result["status"] == "skipped" for result in results)
    assert all(result["reason"] for result in results)


@pytest.mark.asyncio
async def test_import_rejects_files_over_the_backstop(
    core: CoreServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(knowledge_module, "MAX_KNOWLEDGE_BYTES", 10)
    imported = await core.knowledge.import_items(
        [ImportItem(filename="太大.txt", content=b"x" * 11)],
        project_id=None,
        profile=None,
    )
    assert imported[-1]["status"] == "skipped"
    assert "50MB" in imported[-1]["reason"]


# --- sqlite-vec KNN index ----------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_search_hits_the_nearest_chunk(core: CoreServices, profile: dict) -> None:
    """A query whose vector exactly equals one chunk's must rank it first."""
    short = core.knowledge.import_items(
        [ImportItem(filename="短.txt", content=b" precisely seventeen ")],
        project_id=None,
        profile=profile,
    )
    long_import = core.knowledge.import_items(
        [
            ImportItem(
                filename="长.txt",
                content=b"a far longer passage with many more words in it",
            )
        ],
        project_id=None,
        profile=profile,
    )
    short_doc = (await short)[-1]["document"]
    long_doc = (await long_import)[-1]["document"]

    results = await core.knowledge.search(" precisely seventeen ", project_id=None, profile=profile)
    assert results
    assert results[0]["document_id"] == short_doc["id"]
    assert results[0]["score"] > 0.5

    others = await core.knowledge.search(
        "a far longer passage with many more words in it", project_id=None, profile=profile
    )
    assert others
    assert others[0]["document_id"] == long_doc["id"]


@pytest.mark.asyncio
async def test_delete_removes_the_vec_rows(core: CoreServices, profile: dict) -> None:
    imported = await core.knowledge.import_items(
        [ImportItem(filename="临时.txt", content="被删除后就不再被向量检索命中".encode())],
        project_id=None,
        profile=profile,
    )
    document = imported[-1]["document"]
    before = core.store.database.fetchall(
        "SELECT COUNT(*) AS n FROM knowledge_chunks_vec_2 WHERE chunk_id IN"
        " (SELECT id FROM knowledge_chunks WHERE document_id = ?)",
        (document["id"],),
    )
    assert before[0]["n"] > 0

    core.store.delete_knowledge_document(document["id"])
    after = core.store.database.fetchall(
        "SELECT COUNT(*) AS n FROM knowledge_chunks_vec_2 WHERE chunk_id IN"
        " (SELECT id FROM knowledge_chunks WHERE document_id = ?)",
        (document["id"],),
    )
    assert after[0]["n"] == 0


@pytest.mark.asyncio
async def test_restore_rebuilds_fts_and_vec_from_chunks(core: CoreServices, profile: dict) -> None:
    """Deleting drops the derived indexes; restoring must bring both back."""
    imported = await core.knowledge.import_items(
        [ImportItem(filename="复活.txt", content="恢复之后要能被重新检索到".encode())],
        project_id=None,
        profile=profile,
    )
    document = imported[-1]["document"]
    core.store.delete_knowledge_document(document["id"])
    assert not await core.knowledge.search("恢复", project_id=None, profile=profile)

    trash = core.store.list_trash()
    core.store.restore_trash_item(trash[0]["id"])

    results = await core.knowledge.search("恢复", project_id=None, profile=profile)
    assert results
    assert results[0]["document_id"] == document["id"]


@pytest.mark.asyncio
async def test_a_missing_vec_table_means_no_semantic_hits(
    core: CoreServices, profile: dict
) -> None:
    """A width nobody has embedded at yet is empty, not an error."""
    hits = core.store.search_knowledge_vec(None, "mock-embedding", [0.5] * 7, limit=5)
    assert hits == []


@pytest.mark.asyncio
async def test_migration_backfills_vec_rows_from_stored_embeddings(
    core: CoreServices, profile: dict
) -> None:
    """A database from schema 4 has embeddings but no vec rows; backfill them."""
    imported = await core.knowledge.import_items(
        [ImportItem(filename="旧库.txt", content="迁移之后向量行应该被补齐".encode())],
        project_id=None,
        profile=profile,
    )
    document = imported[-1]["document"]
    with core.database.transaction() as connection:
        connection.execute("DELETE FROM knowledge_chunks_vec_2")
        core.database._backfill_vec_index(connection)
    rows = core.store.database.fetchall(
        "SELECT COUNT(*) AS n FROM knowledge_chunks_vec_2 WHERE chunk_id IN"
        " (SELECT id FROM knowledge_chunks WHERE document_id = ?)",
        (document["id"],),
    )
    assert rows[0]["n"] > 0
