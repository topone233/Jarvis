from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import sqlite_vec

from app.errors import ValidationError
from app.utils import json_load, segment_for_index, utc_now

SCHEMA_VERSION = 6

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
    -- NULL means "do not send max_tokens at all", which is not the same as
    -- sending a large number: an endpoint that has its own idea of the limit
    -- should be left to apply it.
    max_tokens INTEGER,
    compact_percent INTEGER NOT NULL DEFAULT 72,
    -- What this endpoint wants added to a request when the thinking switch is
    -- on, and when it is off. Two JSON objects rather than a list of levels,
    -- because "how do I ask this provider not to think" has no single answer:
    -- reasoning_effort, enable_thinking and thinking.type are all in use, and a
    -- provider that needs none of them wants an empty object.
    thinking_on_json TEXT NOT NULL DEFAULT '{}',
    thinking_off_json TEXT NOT NULL DEFAULT '{}',
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
    embedding_json TEXT,
    embedding_model TEXT,
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
    content TEXT NOT NULL,
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
    section_id TEXT,
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


def vec_table_name(dim: int) -> str:
    """The vec0 table holding chunk embeddings of one dimension.

    One table per dimension, because vec0 fixes the vector width at CREATE
    time and different embedding models have different widths.
    """
    return f"knowledge_chunks_vec_{int(dim)}"


class Database:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory
        self.path = data_directory / "jarvis.sqlite3"
        self.objects_directory = data_directory / "objects"

    def initialize(self) -> None:
        if not self.data_directory.is_dir():
            raise ValidationError(
                f"数据目录不存在或不可用：{self.data_directory}。"
                "它可能被移动或删除了，也可能所在的磁盘没有挂上。"
            )
        # Inside a directory that has just been checked, and non-recursive on
        # purpose: this must never become a way to create the data directory.
        self.objects_directory.mkdir(exist_ok=True)
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
        if current < 5:
            self._backfill_vec_index(connection)
        # Gated on the column rather than on the recorded version, because the
        # two paths into this method disagree about what is already there: a
        # database from before this change has the table without the columns,
        # while one created just now got them from SCHEMA_SQL and reports
        # version 0, so a version test would ALTER a column into existence twice.
        self._add_column_if_missing(
            connection,
            "model_profiles",
            "max_tokens",
            "ALTER TABLE model_profiles ADD COLUMN max_tokens INTEGER",
        )
        self._add_column_if_missing(
            connection,
            "model_profiles",
            "compact_percent",
            "ALTER TABLE model_profiles ADD COLUMN compact_percent INTEGER NOT NULL DEFAULT 72",
        )
        self._add_column_if_missing(
            connection,
            "model_profiles",
            "thinking_on_json",
            "ALTER TABLE model_profiles ADD COLUMN thinking_on_json TEXT NOT NULL DEFAULT '{}'",
        )
        self._add_column_if_missing(
            connection,
            "model_profiles",
            "thinking_off_json",
            "ALTER TABLE model_profiles ADD COLUMN thinking_off_json TEXT NOT NULL DEFAULT '{}'",
        )
        # The levels list this replaces never held anything: no client ever set
        # it, so nothing is migrated out of it. Dropped rather than left behind,
        # because a column that still exists is a column someone will fill.
        self._drop_column_if_present(connection, "model_profiles", "reasoning_levels_json")
        # Memory vectors are a write-through cache for relevance ranking, not
        # data: they are computed lazily at retrieval time, so there is nothing
        # to backfill and no version-gated migration - only columns to add.
        self._add_column_if_missing(
            connection,
            "memories",
            "embedding_json",
            "ALTER TABLE memories ADD COLUMN embedding_json TEXT",
        )
        self._add_column_if_missing(
            connection,
            "memories",
            "embedding_model",
            "ALTER TABLE memories ADD COLUMN embedding_model TEXT",
        )
        # Documents from before the stored-content model have no canonical
        # text and no section ids; their chunks cannot address sections and
        # the tools cannot read them. Deleted outright - the extraction they
        # came from cannot be reproduced without the import - rather than
        # carried as a second, degraded code path everywhere after.
        self._add_column_if_missing(
            connection,
            "knowledge_documents",
            "content",
            "ALTER TABLE knowledge_documents ADD COLUMN content TEXT",
        )
        self._add_column_if_missing(
            connection,
            "knowledge_chunks",
            "section_id",
            "ALTER TABLE knowledge_chunks ADD COLUMN section_id TEXT",
        )
        self._delete_legacy_knowledge_documents(connection)

    def _delete_legacy_knowledge_documents(self, connection: sqlite3.Connection) -> None:
        """Remove every document that predates the stored-content model.

        Runs on every startup but is a no-op once the table is clean. The
        chunks, both indexes, any trash entry that would restore the
        document, and the original file in objects/ all go with it - a
        restore target that cannot be restored, or an orphaned blob on disk,
        would each be a quieter version of the same lie.
        """
        legacy = connection.execute(
            "SELECT id, stored_path FROM knowledge_documents WHERE content IS NULL"
        ).fetchall()
        if not legacy:
            return
        marks = ",".join("?" * len(legacy))
        document_ids = [row["id"] for row in legacy]
        chunk_ids = [
            row["id"]
            for row in connection.execute(
                f"SELECT id FROM knowledge_chunks WHERE document_id IN ({marks})",
                document_ids,
            )
        ]
        if chunk_ids:
            chunk_marks = ",".join("?" * len(chunk_ids))
            vec_tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name LIKE 'knowledge_chunks_vec_%'"
            ).fetchall()
            for table in vec_tables:
                connection.execute(
                    f"DELETE FROM {table['name']} WHERE chunk_id IN ({chunk_marks})",
                    chunk_ids,
                )
            connection.execute(
                f"DELETE FROM knowledge_chunks_fts WHERE document_id IN ({marks})",
                document_ids,
            )
            connection.execute(
                f"DELETE FROM knowledge_chunks WHERE document_id IN ({marks})",
                document_ids,
            )
        connection.execute(
            "DELETE FROM trash_items WHERE entity_type = 'knowledge_document'"
            f" AND entity_id IN ({marks})",
            document_ids,
        )
        connection.execute(f"DELETE FROM knowledge_documents WHERE id IN ({marks})", document_ids)
        objects = self.objects_directory
        for row in legacy:
            with contextlib.suppress(OSError):
                stored = Path(row["stored_path"])
                # The path is a join of this very directory at import time;
                # the containment check is what keeps a tampered row from
                # pointing the delete anywhere else.
                if stored.is_file() and objects in stored.parents:
                    stored.unlink()

    @staticmethod
    def _add_column_if_missing(
        connection: sqlite3.Connection, table: str, column: str, statement: str
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(statement)

    @staticmethod
    def _drop_column_if_present(connection: sqlite3.Connection, table: str, column: str) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column in columns:
            connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")

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

    def _backfill_vec_index(self, connection: sqlite3.Connection) -> None:
        """Rebuild the vec0 KNN tables from stored chunk embeddings.

        The vec tables are derived data, so a full rebuild is always safe and
        the migration is a rebuild rather than a one-time copy.
        """
        rows = connection.execute(
            "SELECT id, project_id, embedding_json, embedding_model FROM knowledge_chunks"
            " WHERE deleted_at IS NULL AND embedding_json IS NOT NULL"
        ).fetchall()
        by_dim: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            vector = json_load(row["embedding_json"], None)
            if isinstance(vector, list) and vector:
                by_dim.setdefault(len(vector), []).append(row)
        for dim, entries in by_dim.items():
            self.ensure_vec_table(connection, dim)
            for row in entries:
                vector = json_load(row["embedding_json"], None)
                connection.execute(
                    f"INSERT OR IGNORE INTO {vec_table_name(dim)}"
                    "(chunk_id, embedding, embedding_model, project_id) VALUES (?, ?, ?, ?)",
                    (
                        row["id"],
                        sqlite_vec.serialize_float32(vector),
                        row["embedding_model"],
                        row["project_id"] or "",
                    ),
                )

    def ensure_vec_table(self, connection: sqlite3.Connection, dim: int) -> None:
        """Create the per-dimension vec0 KNN table if it does not exist yet.

        Embeddings are compared with cosine distance, and the two metadata
        columns carry exactly the filters a search applies (the model that
        produced the vectors and the project scope, "" for global - the same
        convention knowledge_chunks_fts uses).
        """
        name = vec_table_name(dim)
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        if exists is None:
            connection.execute(
                f"CREATE VIRTUAL TABLE {name} USING vec0("
                "chunk_id TEXT PRIMARY KEY,"
                f"embedding FLOAT[{int(dim)}] distance_metric=cosine,"
                "embedding_model TEXT,"
                "project_id TEXT)"
            )

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        # The extension is loadable per connection and cheap to load; every
        # connection goes through here, so the vec tables work everywhere.
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
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
