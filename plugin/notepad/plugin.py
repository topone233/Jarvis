"""便签（notepad）：Jarvis 的第一个插件，也是插件契约的验证用例。

随手记、记时间、AI 打标签、全文检索；正文是 markdown，存成
``<数据目录>/notes/`` 下的纯文本文件，任何编辑器（包括 Typora）都能直接
打开和修改，应用靠索引的 mtime 巡检把外部改动捡回来。

本模块只做两件事：声明契约（MANIFEST / SETTINGS_SCHEMA），把
notes_store 的存储和 tagging 的后台任务接成一个 APIRouter。接口在
``/api/plugins/notepad`` 前缀下。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.errors import NotFoundError, ValidationError
from app.plugin_host import PluginContext

from . import tagging
from .notes_store import NoteIndex

MANIFEST = {
    "id": "notepad",
    "name": "便签",
    "description": "随手记 markdown 便签：快捷键呼出、AI 自动打标签、全文检索；文件是纯 markdown，Typora 可直接打开。",
    "version": "0.1.0",
}

SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {"key": "capture_hotkey", "label": "呼出便签弹窗", "type": "hotkey", "default": "Alt+N"},
    {"key": "auto_tag", "label": "AI 自动打标签", "type": "bool", "default": True},
]


def _index(context: PluginContext) -> NoteIndex:
    return context.state["index"]


def ensure_services(context: PluginContext) -> None:
    """One index per data directory, held in the host's scratch state.

    A data-directory switch rebuilds the services - and this hook with it -
    so the index is always pointing at the directory the app is using.
    """
    context.state.setdefault("index", NoteIndex(context.data_directory / "notes"))


def _http_error(error: Exception) -> HTTPException:
    """A domain error becomes the response it deserves: a missing note is a
    404, a refused overwrite (the conflict wording is the client's
    instruction to reload) a 409, any other domain rule a 422."""
    message = str(error)
    if isinstance(error, NotFoundError):
        return HTTPException(status_code=404, detail=message)
    if isinstance(error, ValidationError):
        status = 409 if "外部修改" in message else 422
        return HTTPException(status_code=status, detail=message)
    return HTTPException(status_code=422, detail=message)


def create_router(context: PluginContext) -> APIRouter:
    router = APIRouter()

    @router.get("/notes")
    async def list_notes(
        q: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit = min(max(limit, 1), 200)
        items = _index(context).list_notes(query=q, tag=tag, limit=limit, offset=offset)
        return {"items": items}

    @router.post("/notes", status_code=201)
    async def create_note(payload: dict[str, Any]) -> dict[str, Any]:
        index = _index(context)
        title = str(payload.get("title") or "").strip()
        content = str(payload.get("content") or "")
        source = payload.get("source") if payload.get("source") in ("popup", "page") else "page"
        if not content.strip() and not title.strip():
            raise HTTPException(status_code=422, detail="便签不能是空的。")
        note = index.create(title=title, content=content, source=source)
        tagging.schedule_autotag(context, note.id)
        # Re-read rather than trust the pre-scheduling snapshot: the tagging
        # decision may already have moved the note to pending or failed.
        return index.get(note.id).full()

    @router.get("/notes/deleted")
    async def list_deleted_notes() -> dict[str, Any]:
        return {"items": _index(context).list_deleted()}

    @router.get("/notes/{note_id}")
    async def get_note(note_id: str) -> dict[str, Any]:
        try:
            return _index(context).get(note_id).full()
        except NotFoundError as error:
            raise _http_error(error)

    @router.patch("/notes/{note_id}")
    async def update_note(note_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index = _index(context)
        try:
            note = index.update(
                note_id,
                title=payload.get("title") if "title" in payload else None,
                content=payload.get("content") if "content" in payload else None,
                tags=payload.get("tags") if "tags" in payload else None,
                base_updated_at=payload.get("base_updated_at"),
            )
        except (NotFoundError, ValidationError) as error:
            raise _http_error(error)
        return note.full()

    @router.delete("/notes/{note_id}", status_code=204, response_model=None)
    async def delete_note(note_id: str) -> None:
        try:
            _index(context).move_to_deleted(note_id)
        except NotFoundError as error:
            raise _http_error(error)

    @router.post("/notes/{note_id}/restore")
    async def restore_note(note_id: str) -> dict[str, Any]:
        try:
            return {"note": _index(context).restore(note_id)}
        except NotFoundError as error:
            raise _http_error(error)

    @router.delete("/notes/{note_id}/purge", status_code=204, response_model=None)
    async def purge_note(note_id: str) -> None:
        try:
            _index(context).purge(note_id)
        except NotFoundError as error:
            raise _http_error(error)

    @router.post("/notes/{note_id}/retag")
    async def retag_note(note_id: str) -> dict[str, Any]:
        try:
            await tagging.retag(context, note_id)
        except RuntimeError as error:
            raise HTTPException(status_code=422, detail=str(error))
        return _index(context).get(note_id).full()

    @router.get("/tags")
    async def list_tags() -> dict[str, Any]:
        return {"items": _index(context).tags()}

    return router
