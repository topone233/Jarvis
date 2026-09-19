from __future__ import annotations

from pathlib import Path

from app.database import Database
from app.store import Store


def test_migration_deletes_documents_that_predate_stored_content(tmp_path: Path) -> None:
    """A pre-section-model database loses its knowledge documents on upgrade.

    The legacy state is manufactured by dropping the content column off a
    fresh database and re-adding it nullable - the exact shape an upgraded
    database has - then nulling one document's content. Re-initializing runs
    the migration, and the document must go entirely: row, chunks, FTS row,
    and the original file in objects/.
    """
    database = Database(tmp_path)
    database.initialize()
    store = Store(database)
    stored_file = tmp_path / "objects" / "legacy.md"
    stored_file.parent.mkdir(exist_ok=True)
    stored_file.write_bytes(b"original bytes")
    document = store.create_knowledge_document(
        project_id=None,
        title="legacy",
        original_filename="legacy.md",
        relative_path=None,
        mime_type="text/markdown",
        content_hash="hash",
        stored_path=str(stored_file),
        content="正文",
    )
    store.create_knowledge_chunks(
        document["id"],
        None,
        [{"position": 0, "content": "正文", "section_id": "1", "token_estimate": 2}],
        None,
    )
    stored_file = Path(store.get_knowledge_document(document["id"])["stored_path"])
    assert stored_file.exists()

    with database.transaction() as connection:
        connection.execute("ALTER TABLE knowledge_documents DROP COLUMN content")
        connection.execute("ALTER TABLE knowledge_documents ADD COLUMN content TEXT")
        connection.execute(
            "UPDATE knowledge_documents SET content = NULL WHERE id = ?", (document["id"],)
        )

    database.initialize()

    assert (
        database.fetchone(
            "SELECT COUNT(*) AS n FROM knowledge_documents WHERE id = ?", (document["id"],)
        )["n"]
        == 0
    )
    assert (
        database.fetchone(
            "SELECT COUNT(*) AS n FROM knowledge_chunks WHERE document_id = ?", (document["id"],)
        )["n"]
        == 0
    )
    assert (
        database.fetchone(
            "SELECT COUNT(*) AS n FROM knowledge_chunks_fts WHERE document_id = ?",
            (document["id"],),
        )["n"]
        == 0
    )
    assert not stored_file.exists()


def test_migration_keeps_documents_that_have_content(tmp_path: Path) -> None:
    database = Database(tmp_path)
    database.initialize()
    store = Store(database)
    document = store.create_knowledge_document(
        project_id=None,
        title="current",
        original_filename="current.md",
        relative_path=None,
        mime_type="text/markdown",
        content_hash="hash",
        stored_path="",
        content="正文",
    )
    store.create_knowledge_chunks(
        document["id"],
        None,
        [{"position": 0, "content": "正文", "section_id": "1", "token_estimate": 2}],
        None,
    )

    database.initialize()

    assert store.get_knowledge_document(document["id"])["content"] == "正文"
