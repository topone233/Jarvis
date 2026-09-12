from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any, overload

from app.database import Database
from app.errors import NotFoundError
from app.utils import json_dump, json_load, new_id, segment_for_index, utc_now


@overload
def _record(
    row: None,
    *,
    json_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
) -> None: ...


@overload
def _record(
    row: sqlite3.Row,
    *,
    json_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
) -> dict[str, Any]: ...


def _record(
    row: sqlite3.Row | None,
    *,
    json_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        result[field.removesuffix("_json")] = json_load(result.pop(field, None), [])
    for field in bool_fields:
        result[field] = bool(result[field])
    return result


def _records(
    rows: Iterable[sqlite3.Row],
    *,
    json_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    return [_record(row, json_fields=json_fields, bool_fields=bool_fields) for row in rows]


def _profile_column_value(key: str, value: Any) -> Any:
    if key == "reasoning_levels":
        return json_dump(value)
    if key == "is_default":
        return int(value)
    return value


class Store:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _require(row: sqlite3.Row | None, entity: str) -> sqlite3.Row:
        if row is None:
            raise NotFoundError(f"未找到{entity}或它已被删除。")
        return row

    @staticmethod
    def _snapshot(row: sqlite3.Row) -> str:
        return json_dump(dict(row))

    @staticmethod
    def _add_trash(
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        snapshot_json: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO trash_items(
                id, entity_type, entity_id, snapshot_json, deleted_at, restored_at
            )
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (new_id(), entity_type, entity_id, snapshot_json, utc_now()),
        )

    # Model profiles
    def list_model_profiles(self) -> list[dict[str, Any]]:
        rows = self.database.fetchall(
            """
            SELECT * FROM model_profiles
            WHERE deleted_at IS NULL
            ORDER BY is_default DESC, name COLLATE NOCASE
            """
        )
        return _records(
            rows, json_fields=("reasoning_levels_json",), bool_fields=("is_default", "has_api_key")
        )

    def get_model_profile(self, profile_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM model_profiles WHERE id = ? AND deleted_at IS NULL", (profile_id,)
        )
        return _record(
            self._require(row, "模型配置"),
            json_fields=("reasoning_levels_json",),
            bool_fields=("is_default", "has_api_key"),
        )

    def get_default_model_profile(self) -> dict[str, Any]:
        row = self.database.fetchone(
            """
            SELECT * FROM model_profiles
            WHERE deleted_at IS NULL
            ORDER BY is_default DESC, created_at ASC
            LIMIT 1
            """
        )
        return _record(
            self._require(row, "默认模型配置"),
            json_fields=("reasoning_levels_json",),
            bool_fields=("is_default", "has_api_key"),
        )

    def create_model_profile(self, data: dict[str, Any], has_api_key: bool) -> dict[str, Any]:
        profile_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            if data["is_default"]:
                connection.execute(
                    "UPDATE model_profiles SET is_default = 0 WHERE deleted_at IS NULL"
                )
            connection.execute(
                """
                INSERT INTO model_profiles(
                    id, name, base_url, protocol, chat_model, embedding_model, context_window,
                    output_token_reserve, reasoning_levels_json, is_default, has_api_key,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, 'chat_completions', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    profile_id,
                    data["name"],
                    data["base_url"],
                    data["chat_model"],
                    data.get("embedding_model"),
                    data["context_window"],
                    data["output_token_reserve"],
                    json_dump(data.get("reasoning_levels", [])),
                    int(data["is_default"]),
                    int(has_api_key),
                    now,
                    now,
                ),
            )
        return self.get_model_profile(profile_id)

    def update_model_profile(
        self, profile_id: str, changes: dict[str, Any], has_api_key: bool | None
    ) -> dict[str, Any]:
        current = self.get_model_profile(profile_id)
        allowed = {
            "name",
            "base_url",
            "chat_model",
            "embedding_model",
            "context_window",
            "output_token_reserve",
            "reasoning_levels",
            "is_default",
        }
        # Unset fields are already absent thanks to exclude_unset, so an explicit
        # null is a request to clear the field. Only embedding_model is nullable.
        values = {
            key: value
            for key, value in changes.items()
            if key in allowed and (value is not None or key == "embedding_model")
        }
        if not values and has_api_key is None:
            return current
        with self.database.transaction() as connection:
            if values.get("is_default"):
                connection.execute(
                    "UPDATE model_profiles SET is_default = 0 WHERE deleted_at IS NULL"
                )
            assignments: list[str] = []
            parameters: list[Any] = []
            for key, value in values.items():
                column = "reasoning_levels_json" if key == "reasoning_levels" else key
                assignments.append(f"{column} = ?")
                parameters.append(_profile_column_value(key, value))
            if has_api_key is not None:
                assignments.append("has_api_key = ?")
                parameters.append(int(has_api_key))
            assignments.append("updated_at = ?")
            parameters.append(utc_now())
            parameters.append(profile_id)
            connection.execute(
                f"UPDATE model_profiles SET {', '.join(assignments)}"
                " WHERE id = ? AND deleted_at IS NULL",
                tuple(parameters),
            )
        return self.get_model_profile(profile_id)

    def delete_model_profile(self, profile_id: str) -> None:
        with self.database.transaction() as connection:
            row = self._require(
                connection.execute(
                    "SELECT * FROM model_profiles WHERE id = ? AND deleted_at IS NULL",
                    (profile_id,),
                ).fetchone(),
                "模型配置",
            )
            self._add_trash(connection, "model_profile", profile_id, self._snapshot(row))
            connection.execute(
                "UPDATE model_profiles SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (utc_now(), utc_now(), profile_id),
            )

    # Projects
    def list_projects(self) -> list[dict[str, Any]]:
        rows = self.database.fetchall(
            """
            SELECT * FROM projects WHERE deleted_at IS NULL
            ORDER BY is_pinned DESC, updated_at DESC
            """
        )
        return _records(rows, bool_fields=("is_pinned",))

    def get_project(self, project_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL", (project_id,)
        )
        return _record(self._require(row, "项目"), bool_fields=("is_pinned",))

    def create_project(self, name: str, is_pinned: bool) -> dict[str, Any]:
        project_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, is_pinned, created_at, updated_at, deleted_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (project_id, name.strip(), int(is_pinned), now, now),
            )
        return self.get_project(project_id)

    def update_project(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.get_project(project_id)
        values = {key: value for key, value in changes.items() if value is not None}
        if not values:
            return self.get_project(project_id)
        assignments: list[str] = []
        parameters: list[Any] = []
        for key in ("name", "is_pinned"):
            if key in values:
                assignments.append(f"{key} = ?")
                parameters.append(int(values[key]) if key == "is_pinned" else values[key].strip())
        assignments.append("updated_at = ?")
        parameters.append(utc_now())
        parameters.append(project_id)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE projects SET {', '.join(assignments)} WHERE id = ? AND deleted_at IS NULL",
                tuple(parameters),
            )
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> None:
        self._soft_delete("projects", "project", project_id)

    # Conversations and messages
    def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id is None:
            query = """
                SELECT * FROM conversations
                WHERE project_id IS NULL AND deleted_at IS NULL
                ORDER BY is_pinned DESC, updated_at DESC
            """
            parameters: tuple[object, ...] = ()
        else:
            query = """
                SELECT * FROM conversations
                WHERE project_id = ? AND deleted_at IS NULL
                ORDER BY is_pinned DESC, updated_at DESC
            """
            parameters = (project_id,)
        rows = self.database.fetchall(query, parameters)
        return _records(rows, bool_fields=("is_pinned",))

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM conversations WHERE id = ? AND deleted_at IS NULL", (conversation_id,)
        )
        return _record(self._require(row, "对话"), bool_fields=("is_pinned",))

    def create_conversation(
        self,
        title: str,
        project_id: str | None,
        model_profile_id: str | None,
        is_pinned: bool,
    ) -> dict[str, Any]:
        if project_id is not None:
            self.get_project(project_id)
        if model_profile_id is not None:
            self.get_model_profile(model_profile_id)
        conversation_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO conversations(
                    id, project_id, title, model_profile_id, is_pinned, created_at,
                    updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    conversation_id,
                    project_id,
                    title.strip(),
                    model_profile_id,
                    int(is_pinned),
                    now,
                    now,
                ),
            )
        return self.get_conversation(conversation_id)

    def update_conversation(self, conversation_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        values = {key: value for key, value in changes.items() if value is not None}
        if not values:
            return self.get_conversation(conversation_id)
        if values.get("model_profile_id"):
            self.get_model_profile(values["model_profile_id"])
        assignments: list[str] = []
        parameters: list[Any] = []
        for key in ("title", "model_profile_id", "is_pinned"):
            if key in values:
                assignments.append(f"{key} = ?")
                raw_value = values[key]
                parameters.append(
                    int(raw_value)
                    if key == "is_pinned"
                    else raw_value.strip()
                    if key == "title"
                    else raw_value
                )
        assignments.append("updated_at = ?")
        parameters.append(utc_now())
        parameters.append(conversation_id)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE conversations SET {', '.join(assignments)}"
                " WHERE id = ? AND deleted_at IS NULL",
                tuple(parameters),
            )
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        self._soft_delete("conversations", "conversation", conversation_id)

    def list_messages(
        self, conversation_id: str, include_deleted: bool = False
    ) -> list[dict[str, Any]]:
        self.get_conversation(conversation_id)
        visibility = "" if include_deleted else "AND deleted_at IS NULL"
        rows = self.database.fetchall(
            f"""
            SELECT * FROM messages
            WHERE conversation_id = ? {visibility}
            ORDER BY ordinal ASC, created_at ASC
            """,
            (conversation_id,),
        )
        return _records(rows, json_fields=("metadata_json",))

    def get_message(self, message_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM messages WHERE id = ? AND deleted_at IS NULL", (message_id,)
        )
        return _record(self._require(row, "消息"), json_fields=("metadata_json",))

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_conversation(conversation_id)
        message_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO messages(
                    id, conversation_id, parent_id, role, content, ordinal, metadata_json,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    message_id,
                    conversation_id,
                    parent_id,
                    role,
                    content,
                    ordinal,
                    json_dump(metadata or {}),
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return self.get_message(message_id)

    def update_message(
        self, message_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        current = self.get_message(message_id)
        fields = ["content = ?", "updated_at = ?"]
        parameters: list[Any] = [content, utc_now()]
        if metadata is not None:
            fields.insert(1, "metadata_json = ?")
            parameters.insert(1, json_dump(metadata))
        parameters.append(message_id)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE messages SET {', '.join(fields)} WHERE id = ? AND deleted_at IS NULL",
                tuple(parameters),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (utc_now(), current["conversation_id"]),
            )
        return self.get_message(message_id)

    # Messages are never deleted on their own. Individual turns are not a unit
    # the user removes; a conversation leaves as a whole, from the conversation
    # list. Regenerate therefore never has to cope with a half-removed turn.

    # Runs and audit events
    def create_run(
        self,
        conversation_id: str,
        user_message_id: str,
        assistant_message_id: str,
        model_profile_id: str,
    ) -> dict[str, Any]:
        run_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO assistant_runs(
                    id, conversation_id, user_message_id, assistant_message_id, model_profile_id,
                    status, error_message, input_token_estimate, output_token_estimate, started_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, 'running', NULL, 0, 0, ?, NULL)
                """,
                (
                    run_id,
                    conversation_id,
                    user_message_id,
                    assistant_message_id,
                    model_profile_id,
                    utc_now(),
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.database.fetchone("SELECT * FROM assistant_runs WHERE id = ?", (run_id,))
        return _record(self._require(row, "运行记录"))

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        error_message: str | None = None,
        input_token_estimate: int | None = None,
        output_token_estimate: int | None = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        assignments = ["status = ?", "error_message = ?"]
        parameters: list[Any] = [status, error_message]
        if input_token_estimate is not None:
            assignments.append("input_token_estimate = ?")
            parameters.append(input_token_estimate)
        if output_token_estimate is not None:
            assignments.append("output_token_estimate = ?")
            parameters.append(output_token_estimate)
        if completed:
            assignments.append("completed_at = ?")
            parameters.append(utc_now())
        parameters.append(run_id)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE assistant_runs SET {', '.join(assignments)} WHERE id = ?",
                tuple(parameters),
            )
        return self.get_run(run_id)

    def create_run_event(
        self, run_id: str, sequence: int, stage: str, state: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        event_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO run_events(id, run_id, sequence, stage, state, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, run_id, sequence, stage, state, json_dump(payload), utc_now()),
            )
        row = self.database.fetchone("SELECT * FROM run_events WHERE id = ?", (event_id,))
        return _record(self._require(row, "审计事件"), json_fields=("payload_json",))

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetchall(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY sequence ASC", (run_id,)
        )
        return _records(rows, json_fields=("payload_json",))

    # Context artifacts
    def get_latest_context_artifact(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.database.fetchone(
            """
            SELECT * FROM context_artifacts
            WHERE conversation_id = ? AND deleted_at IS NULL
            ORDER BY end_ordinal DESC, created_at DESC
            LIMIT 1
            """,
            (conversation_id,),
        )
        return _record(
            row,
            json_fields=("decision_anchors_json", "source_message_ids_json"),
        )

    def create_context_artifact(
        self,
        conversation_id: str,
        start_ordinal: int,
        end_ordinal: int,
        content: str,
        decision_anchors: list[str],
        source_message_ids: list[str],
        prompt_version: str,
        token_estimate: int,
    ) -> dict[str, Any]:
        artifact_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO context_artifacts(
                    id, conversation_id, start_ordinal, end_ordinal, content,
                    decision_anchors_json, source_message_ids_json, prompt_version, token_estimate,
                    created_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    artifact_id,
                    conversation_id,
                    start_ordinal,
                    end_ordinal,
                    content,
                    json_dump(decision_anchors),
                    json_dump(source_message_ids),
                    prompt_version,
                    token_estimate,
                    utc_now(),
                ),
            )
        row = self.database.fetchone("SELECT * FROM context_artifacts WHERE id = ?", (artifact_id,))
        return _record(
            self._require(row, "压缩记录"),
            json_fields=("decision_anchors_json", "source_message_ids_json"),
        )

    # Memory
    def list_memories(
        self, *, project_id: str | None = None, include_global: bool = True
    ) -> list[dict[str, Any]]:
        clauses = ["status = 'active'", "deleted_at IS NULL"]
        parameters: list[Any] = []
        if project_id is None:
            clauses.append("project_id IS NULL")
        elif include_global:
            clauses.append("(project_id IS NULL OR project_id = ?)")
            parameters.append(project_id)
        else:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        rows = self.database.fetchall(
            f"""
            SELECT * FROM memories WHERE {" AND ".join(clauses)}
            ORDER BY kind ASC, updated_at DESC
            """,
            tuple(parameters),
        )
        return _records(rows)

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL", (memory_id,)
        )
        return _record(self._require(row, "记忆"))

    def find_active_memory(
        self, scope: str, project_id: str | None, normalized_key: str
    ) -> dict[str, Any] | None:
        if project_id is None:
            row = self.database.fetchone(
                """
                SELECT * FROM memories
                WHERE scope = ? AND project_id IS NULL AND normalized_key = ?
                    AND status = 'active' AND deleted_at IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (scope, normalized_key),
            )
        else:
            row = self.database.fetchone(
                """
                SELECT * FROM memories
                WHERE scope = ? AND project_id = ? AND normalized_key = ?
                    AND status = 'active' AND deleted_at IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (scope, project_id, normalized_key),
            )
        return _record(row)

    def create_memory(
        self,
        *,
        scope: str,
        project_id: str | None,
        kind: str,
        memory_key: str,
        content: str,
        normalized_key: str,
        confidence: float,
        source_message_id: str,
        source_excerpt: str,
    ) -> dict[str, Any]:
        memory_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memories(
                    id, scope, project_id, kind, memory_key, content, normalized_key,
                    confidence, confirmation_count, source_message_id, source_excerpt,
                    status, created_at, updated_at, superseded_by, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'active', ?, ?, NULL, NULL)
                """,
                (
                    memory_id,
                    scope,
                    project_id,
                    kind,
                    memory_key,
                    content,
                    normalized_key,
                    confidence,
                    source_message_id,
                    source_excerpt,
                    now,
                    now,
                ),
            )
        return self.get_memory(memory_id)

    def confirm_memory(self, memory_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE memories
                SET confirmation_count = confirmation_count + 1, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (utc_now(), memory_id),
            )
        return self.get_memory(memory_id)

    def supersede_memory(self, memory_id: str, successor_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE memories
                SET status = 'superseded', superseded_by = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (successor_id, utc_now(), memory_id),
            )

    def update_memory(self, memory_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.get_memory(memory_id)
        values = {key: value for key, value in changes.items() if value is not None}
        if not values:
            return self.get_memory(memory_id)
        assignments: list[str] = []
        parameters: list[Any] = []
        for key in ("content", "memory_key", "confidence"):
            if key in values:
                assignments.append(f"{key} = ?")
                parameters.append(values[key])
        assignments.append("updated_at = ?")
        parameters.append(utc_now())
        parameters.append(memory_id)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE memories SET {', '.join(assignments)} WHERE id = ? AND deleted_at IS NULL",
                tuple(parameters),
            )
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> None:
        self._soft_delete("memories", "memory", memory_id, set_status_deleted=True)

    # Knowledge
    def create_knowledge_document(
        self,
        *,
        project_id: str | None,
        title: str,
        original_filename: str,
        relative_path: str | None,
        mime_type: str,
        content_hash: str,
        stored_path: str,
    ) -> dict[str, Any]:
        document_id = new_id()
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_documents(
                    id, project_id, title, original_filename, relative_path, mime_type,
                    content_hash, stored_path, status, chunk_count, created_at,
                    updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing', 0, ?, ?, NULL)
                """,
                (
                    document_id,
                    project_id,
                    title,
                    original_filename,
                    relative_path,
                    mime_type,
                    content_hash,
                    stored_path,
                    now,
                    now,
                ),
            )
        return self.get_knowledge_document(document_id)

    def get_knowledge_document(self, document_id: str) -> dict[str, Any]:
        row = self.database.fetchone(
            "SELECT * FROM knowledge_documents WHERE id = ? AND deleted_at IS NULL", (document_id,)
        )
        return _record(self._require(row, "知识文档"))

    def list_knowledge_documents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id is None:
            query = """
                SELECT * FROM knowledge_documents
                WHERE project_id IS NULL AND deleted_at IS NULL
                ORDER BY updated_at DESC
            """
            parameters: tuple[object, ...] = ()
        else:
            query = """
                SELECT * FROM knowledge_documents
                WHERE project_id = ? AND deleted_at IS NULL
                ORDER BY updated_at DESC
            """
            parameters = (project_id,)
        return _records(self.database.fetchall(query, parameters))

    def finish_knowledge_document(
        self, document_id: str, status: str, chunk_count: int
    ) -> dict[str, Any]:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE knowledge_documents
                SET status = ?, chunk_count = ?, updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (status, chunk_count, utc_now(), document_id),
            )
        return self.get_knowledge_document(document_id)

    def create_knowledge_chunks(
        self,
        document_id: str,
        project_id: str | None,
        chunks: list[dict[str, Any]],
        embedding_model: str | None,
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        with self.database.transaction() as connection:
            for chunk in chunks:
                chunk_id = new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        id, document_id, project_id, position, content, token_estimate,
                        embedding_json, embedding_model, created_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        chunk_id,
                        document_id,
                        project_id,
                        chunk["position"],
                        chunk["content"],
                        chunk["token_estimate"],
                        json_dump(chunk["embedding"]) if chunk.get("embedding") else None,
                        embedding_model if chunk.get("embedding") else None,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks_fts(chunk_id, document_id, project_id, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chunk_id, document_id, project_id or "", segment_for_index(chunk["content"])),
                )
                created.append(
                    {
                        "id": chunk_id,
                        "document_id": document_id,
                        "project_id": project_id,
                        **chunk,
                    }
                )
        return created

    def get_knowledge_chunks_with_embeddings(
        self, project_id: str | None, embedding_model: str
    ) -> list[dict[str, Any]]:
        if project_id is None:
            query = """
                SELECT c.*, d.title AS document_title
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.project_id IS NULL AND c.deleted_at IS NULL AND d.deleted_at IS NULL
                    AND c.embedding_model = ? AND c.embedding_json IS NOT NULL
            """
            parameters: tuple[object, ...] = (embedding_model,)
        else:
            query = """
                SELECT c.*, d.title AS document_title
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.project_id = ? AND c.deleted_at IS NULL AND d.deleted_at IS NULL
                    AND c.embedding_model = ? AND c.embedding_json IS NOT NULL
            """
            parameters = (project_id, embedding_model)
        return _records(self.database.fetchall(query, parameters), json_fields=("embedding_json",))

    def search_knowledge_fts(
        self, query: str, project_id: str | None, limit: int = 12
    ) -> list[dict[str, Any]]:
        if project_id is None:
            project_clause = "c.project_id IS NULL"
            parameters: tuple[object, ...] = (query, limit)
        else:
            project_clause = "c.project_id = ?"
            parameters = (query, project_id, limit)
        rows = self.database.fetchall(
            f"""
            SELECT c.*, d.title AS document_title, d.original_filename,
                   bm25(knowledge_chunks_fts) AS fts_rank
            FROM knowledge_chunks_fts
            JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.chunk_id
            JOIN knowledge_documents d ON d.id = c.document_id
            WHERE knowledge_chunks_fts MATCH ?
                AND {project_clause}
                AND c.deleted_at IS NULL
                AND d.deleted_at IS NULL
            ORDER BY fts_rank ASC
            LIMIT ?
            """,
            parameters,
        )
        return _records(rows, json_fields=("embedding_json",))

    def delete_knowledge_document(self, document_id: str) -> None:
        with self.database.transaction() as connection:
            row = self._require(
                connection.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (document_id,),
                ).fetchone(),
                "知识文档",
            )
            self._add_trash(connection, "knowledge_document", document_id, self._snapshot(row))
            now = utc_now()
            connection.execute(
                "UPDATE knowledge_documents SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, document_id),
            )
            connection.execute(
                "UPDATE knowledge_chunks SET deleted_at = ? WHERE document_id = ?",
                (now, document_id),
            )
            connection.execute(
                "DELETE FROM knowledge_chunks_fts WHERE document_id = ?",
                (document_id,),
            )

    # Feedback and recycle bin
    def add_feedback(self, message_id: str, kind: str) -> dict[str, Any]:
        self.get_message(message_id)
        feedback_id = new_id()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO feedback(id, message_id, kind, created_at, deleted_at)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (feedback_id, message_id, kind, utc_now()),
            )
        row = self.database.fetchone("SELECT * FROM feedback WHERE id = ?", (feedback_id,))
        return _record(self._require(row, "反馈"))

    def list_trash(self) -> list[dict[str, Any]]:
        rows = self.database.fetchall(
            """
            SELECT * FROM trash_items
            WHERE restored_at IS NULL
            ORDER BY deleted_at DESC
            """
        )
        return _records(rows, json_fields=("snapshot_json",))

    def restore_trash_item(self, trash_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = self._require(
                connection.execute(
                    "SELECT * FROM trash_items WHERE id = ? AND restored_at IS NULL", (trash_id,)
                ).fetchone(),
                "回收站项目",
            )
            entity_type = row["entity_type"]
            entity_id = row["entity_id"]
            table_by_type = {
                "model_profile": "model_profiles",
                "project": "projects",
                "conversation": "conversations",
                "message": "messages",
                "memory": "memories",
                "knowledge_document": "knowledge_documents",
            }
            table = table_by_type.get(entity_type)
            if table is None:
                raise NotFoundError("此回收站项目暂不支持恢复。")
            if entity_type == "memory":
                connection.execute(
                    "UPDATE memories SET deleted_at = NULL, status = 'active', updated_at = ?"
                    " WHERE id = ?",
                    (utc_now(), entity_id),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET deleted_at = NULL WHERE id = ?", (entity_id,)
                )
            connection.execute(
                "UPDATE trash_items SET restored_at = ? WHERE id = ?",
                (utc_now(), trash_id),
            )
        return {
            "id": trash_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "restored": True,
        }

    def discard_trash_item(self, trash_id: str) -> None:
        """Remove a trash entry so the item can no longer be restored.

        This drops the recycle-bin record only. The soft-deleted row stays in
        its table, because assistant_runs references messages by foreign key and
        the run log is the audit trail - erasing rows here would break both. The
        content therefore remains in the database file and is not recoverable
        through the API.
        """
        with self.database.transaction() as connection:
            self._require(
                connection.execute(
                    "SELECT * FROM trash_items WHERE id = ? AND restored_at IS NULL", (trash_id,)
                ).fetchone(),
                "回收站项目",
            )
            connection.execute("DELETE FROM trash_items WHERE id = ?", (trash_id,))

    def _soft_delete(
        self,
        table: str,
        entity_type: str,
        entity_id: str,
        *,
        set_status_deleted: bool = False,
    ) -> None:
        with self.database.transaction() as connection:
            row = self._require(
                connection.execute(
                    f"SELECT * FROM {table} WHERE id = ? AND deleted_at IS NULL", (entity_id,)
                ).fetchone(),
                entity_type,
            )
            self._add_trash(connection, entity_type, entity_id, self._snapshot(row))
            now = utc_now()
            if set_status_deleted:
                connection.execute(
                    f"UPDATE {table} SET deleted_at = ?, status = 'deleted', updated_at = ?"
                    " WHERE id = ?",
                    (now, now, entity_id),
                )
            elif table in {"projects", "conversations", "messages", "model_profiles"}:
                connection.execute(
                    f"UPDATE {table} SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, entity_id),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET deleted_at = ? WHERE id = ?", (now, entity_id)
                )
