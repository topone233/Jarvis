from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from app.utils import segment_for_index, utc_now

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    protocol TEXT NOT NULL,
    chat_model TEXT NOT NULL,
    embedding_model TEXT,
    context_window INTEGER NOT NULL,
    output_token_reserve INTEGER NOT NULL,
    reasoning_levels_json TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    has_api_key INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    model_profile_id TEXT REFERENCES model_profiles(id),
    is_pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    parent_id TEXT REFERENCES messages(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_ordinal
    ON messages(conversation_id, ordinal);

CREATE TABLE IF NOT EXISTS assistant_runs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    user_message_id TEXT NOT NULL REFERENCES messages(id),
    assistant_message_id TEXT NOT NULL REFERENCES messages(id),
    model_profile_id TEXT NOT NULL REFERENCES model_profiles(id),
    status TEXT NOT NULL,
    error_message TEXT,
    input_token_estimate INTEGER NOT NULL DEFAULT 0,
    output_token_estimate INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES assistant_runs(id),
    sequence INTEGER NOT NULL,
    stage TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events(run_id, sequence);

CREATE TABLE IF NOT EXISTS context_artifacts (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    start_ordinal INTEGER NOT NULL,
    end_ordinal INTEGER NOT NULL,
    content TEXT NOT NULL,
    decision_anchors_json TEXT NOT NULL,
    source_message_ids_json TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_context_artifacts_conversation_range
    ON context_artifacts(conversation_id, end_ordinal);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id),
    kind TEXT NOT NULL,
    memory_key TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    confidence REAL NOT NULL,
    confirmation_count INTEGER NOT NULL DEFAULT 1,
    source_message_id TEXT REFERENCES messages(id),
    source_excerpt TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    superseded_by TEXT REFERENCES memories(id),
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_scope_status ON memories(scope, project_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_normalized_key ON memories(normalized_key);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    relative_path TEXT,
    mime_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    status TEXT NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_project_status
    ON knowledge_documents(project_id, status);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(id),
    project_id TEXT REFERENCES projects(id),
    position INTEGER NOT NULL,
    content TEXT NOT NULL,
    token_estimate INTEGER NOT NULL,
    embedding_json TEXT,
    embedding_model TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document_position
    ON knowledge_chunks(document_id, position);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    document_id UNINDEXED,
    project_id UNINDEXED,
    content
);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES messages(id),
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS trash_items (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    restored_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_trash_items_entity ON trash_items(entity_type, entity_id);
"""


class Database:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory
        self.path = data_directory / "jarvis.sqlite3"
        self.objects_directory = data_directory / "objects"

    def initialize(self) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.objects_directory.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            # journal_mode is persisted in the database file, so it only needs to
            # be set once instead of on every connection.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.commit()
        with self.transaction() as connection:
            connection.executescript(SCHEMA_SQL)
            self._apply_migrations(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        current = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
        if current < 2:
            self._rebuild_search_index(connection)

    @staticmethod
    def _rebuild_search_index(connection: sqlite3.Connection) -> None:
        """Re-derive the FTS index, which must mirror segment_for_index()."""
        rows = connection.execute(
            "SELECT id, document_id, project_id, content FROM knowledge_chunks"
            " WHERE deleted_at IS NULL"
        ).fetchall()
        connection.execute("DELETE FROM knowledge_chunks_fts")
        connection.executemany(
            """
            INSERT INTO knowledge_chunks_fts(chunk_id, document_id, project_id, content)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    row["id"],
                    row["document_id"],
                    row["project_id"] or "",
                    segment_for_index(row["content"]),
                )
                for row in rows
            ],
        )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetchone(self, query: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            return connection.execute(query, parameters).fetchone()

    def fetchall(self, query: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            return list(connection.execute(query, parameters).fetchall())
