from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import settings
from app.errors import NotFoundError, ProviderError, SetupRequiredError, ValidationError
from app.images import image_path, media_type_for, message_images
from app.knowledge import ImportItem
from app.runs import RunChoice, RunService
from app.runtime import CoreServices, Runtime
from app.schemas import (
    CompactRequest,
    ConversationCreate,
    ConversationUpdate,
    FeedbackCreate,
    MemoryUpdate,
    ModelProfileCreate,
    ModelProfileUpdate,
    ProjectCreate,
    ProjectUpdate,
    RegenerateRequest,
    RetrievalSettingsUpdate,
    RetrievalTestRequest,
    RunRequest,
    SettingsUpdate,
    SetupRequest,
    SkillEnabledUpdate,
    SkillImportRequest,
)
from app.sections import parse_sections
from app.store import RETRIEVAL_KEY_IDS


def _start_run(
    run_service: RunService,
    run: dict[str, Any],
    profile: dict[str, Any],
    choice: RunChoice,
) -> StreamingResponse:
    """Kick the run off detached, then attach this client to it.

    The run is not owned by this response, so closing the connection - a reload,
    a navigation, a crash of the browser - leaves the answer generating.
    """
    run_service.launch(run, profile=profile, choice=choice)
    return _stream_run(run_service, run["id"])


def _stream_run(run_service: RunService, run_id: str) -> StreamingResponse:
    return StreamingResponse(
        run_service.follow(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _public_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """The API shape of a memory: the retrieval cache stays behind.

    The cached vector is plumbing for relevance ranking - hundreds of floats
    the screen never shows - and embedding_model only names the model that
    produced it.
    """
    return {
        key: value
        for key, value in memory.items()
        if key not in ("embedding_json", "embedding_model")
    }


def create_app(runtime: Runtime | None = None) -> FastAPI:
    app = FastAPI(title="Jarvis Core API", version="0.1.0")
    app.state.runtime = runtime or Runtime()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(SetupRequiredError)
    async def setup_required_handler(_: Request, error: SetupRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": str(error), "code": "setup_required"}
        )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error), "code": "not_found"})

    @app.exception_handler(ValidationError)
    async def validation_handler(_: Request, error: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": str(error), "code": "domain_validation"},
        )

    @app.exception_handler(ProviderError)
    async def provider_handler(_: Request, error: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=502, content={"detail": str(error), "code": "provider_error"}
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, error: RequestValidationError) -> JSONResponse:
        # `errors()` echoes the offending input, and when a body was sent without
        # `Content-Type: application/json` FastAPI never parses it - so the input
        # is the raw `bytes` and `json.dumps` raises, turning a 422 that would
        # have explained the problem into a 500 that explains nothing.
        parsed = jsonable_encoder(error.errors())
        return JSONResponse(
            status_code=422,
            content={"detail": parsed, "code": "request_validation"},
        )

    def services() -> CoreServices:
        return app.state.runtime.services()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        runtime_instance: Runtime = app.state.runtime
        directory = runtime_instance.data_directory
        return {
            "status": "ok",
            "configured": runtime_instance.configured,
            # The setup screen shows this so it is clear that changing it swaps
            # the whole data set, not just a label.
            "data_directory": str(directory) if directory is not None else None,
            # Set when a directory was chosen but could not be opened. Without it
            # a missing directory and a first run look identical on screen, and
            # the screen would ask for a directory that was already given.
            "data_directory_error": runtime_instance.startup_error,
            "service": "jarvis-core",
        }

    @app.post("/api/setup")
    async def setup(payload: SetupRequest) -> dict[str, Any]:
        configured = app.state.runtime.setup(payload.data_directory)
        return {
            "configured": True,
            "data_directory": str(configured.database.data_directory),
        }

    @app.get("/api/settings")
    async def read_settings(core: CoreServices = Depends(services)) -> dict[str, Any]:
        return settings.overview(core.store)

    @app.put("/api/settings")
    async def update_settings(
        payload: SettingsUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        # The field names are the settings keys, so one loop covers the three
        # prompts and the quick-prompt list alike. A key left out of the
        # request is untouched; a key sent as null goes back to the default.
        directory = payload.bash_working_dir
        if directory is not None and directory.strip():
            # Checked here rather than in the schema because it needs the
            # filesystem: a typo'd path is exactly what a save should catch,
            # while a directory deleted later surfaces as the command's own
            # failure text when it next runs.
            path = Path(directory.strip())
            if not path.is_absolute() or not path.is_dir():
                raise ValidationError(
                    f"bash 工作目录必须是一个已存在的绝对路径：{directory.strip()}"
                )
        for key, value in payload.model_dump(exclude_unset=True).items():
            # An empty working directory names nothing - it clears the row,
            # which is what typing over the box and saving means.
            if value is None or (key == settings.BASH_WORKING_DIR and not str(value).strip()):
                core.store.delete_setting(key)
            else:
                core.store.set_setting(key, value)
        return settings.overview(core.store)

    @app.get("/api/model-profiles")
    async def list_model_profiles(core: CoreServices = Depends(services)) -> list[dict[str, Any]]:
        return core.store.list_model_profiles()

    @app.post("/api/model-profiles", status_code=201)
    async def create_model_profile(
        payload: ModelProfileCreate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        values = payload.model_dump()
        api_key = values.pop("api_key")
        profile = core.store.create_model_profile(values, bool(api_key))
        if api_key:
            try:
                core.provider.secrets.set(profile["id"], api_key)
            except ProviderError:
                core.store.delete_model_profile(profile["id"])
                raise
        return profile

    @app.patch("/api/model-profiles/{profile_id}")
    async def update_model_profile(
        profile_id: str,
        payload: ModelProfileUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True)
        api_key = changes.pop("api_key", None)
        has_api_key: bool | None = None
        if api_key is not None:
            if api_key:
                core.provider.secrets.set(profile_id, api_key)
                has_api_key = True
            else:
                core.provider.secrets.delete(profile_id)
                has_api_key = False
        return core.store.update_model_profile(profile_id, changes, has_api_key)

    @app.delete("/api/model-profiles/{profile_id}", status_code=204, response_model=None)
    async def delete_model_profile(profile_id: str, core: CoreServices = Depends(services)) -> None:
        core.store.delete_model_profile(profile_id)
        with contextlib.suppress(ProviderError):
            core.provider.secrets.delete(profile_id)

    @app.post("/api/model-profiles/{profile_id}/test")
    async def test_model_profile(
        profile_id: str,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return await core.provider.test_connection(core.store.get_model_profile(profile_id))

    @app.get("/api/model-profiles/{profile_id}/models")
    async def list_profile_models(
        profile_id: str,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        """The model names this endpoint offers, for the composer's dropdown.

        Read live from the provider rather than stored: the list belongs to the
        service, and a copy of it here would go stale without anyone noticing.
        """
        profile = core.store.get_model_profile(profile_id)
        return {"models": await core.provider.list_models(profile)}

    def _public_retrieval_settings(core: CoreServices) -> dict[str, Any]:
        """The GET shape of both retrieval configs. has_api_key comes from the
        stored flag, not a keyring probe - a read of the settings screen must
        not be the thing that fails when the credential store is broken."""
        result: dict[str, Any] = {}
        for kind in ("embedding", "rerank"):
            spec = core.store.get_retrieval_spec(kind)
            result[kind] = (
                None
                if spec is None
                else {
                    "base_url": spec["base_url"],
                    "model": spec["model"],
                    "has_api_key": spec["has_api_key"],
                }
            )
        return result

    def _retrieval_test_spec(
        core: CoreServices, kind: str, payload: RetrievalTestRequest | None
    ) -> dict[str, Any]:
        """The spec a test call runs with: the form's fields over the stored
        config. A test may name an endpoint that is not saved yet - that is
        the point of testing before saving - and with no form key the stored
        one fills in, so a saved config can be re-tested bare."""
        stored = core.store.get_retrieval_spec(kind)
        base_url = (payload.base_url if payload else None) or (stored or {}).get("base_url")
        model = (payload.model if payload else None) or (stored or {}).get("model")
        if not base_url or not model:
            raise ValidationError("请先填写接口地址和模型名。")
        return {
            "base_url": base_url,
            "model": model,
            "key_id": RETRIEVAL_KEY_IDS[kind],
            "api_key": payload.api_key if payload else None,
        }

    @app.get("/api/retrieval-settings")
    async def get_retrieval_settings(
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return _public_retrieval_settings(core)

    @app.put("/api/retrieval-settings")
    async def update_retrieval_settings(
        payload: RetrievalSettingsUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        """Save both cards from one button; per kind, absent is untouched and
        null is cleared (config and keyring entry both).

        The key follows the model-profile form's deal: a value writes it, an
        empty string deletes it, absent or null leaves it alone. The keyring
        write happens before the spec is stored, so a failing credential
        store leaves the old config fully in force.
        """
        updates = payload.model_dump(exclude_unset=True)
        for kind in ("embedding", "rerank"):
            if kind not in updates:
                continue
            values = updates[kind]
            if values is None:
                core.store.set_retrieval_spec(kind, None)
                core.provider.secrets.delete(RETRIEVAL_KEY_IDS[kind])
                continue
            api_key = values.pop("api_key", None)
            has_api_key: bool | None = None
            if api_key:
                core.provider.secrets.set(RETRIEVAL_KEY_IDS[kind], api_key)
                has_api_key = True
            elif api_key == "":
                core.provider.secrets.delete(RETRIEVAL_KEY_IDS[kind])
                has_api_key = False
            core.store.set_retrieval_spec(
                kind,
                {"base_url": values["base_url"], "model": values["model"]},
                has_api_key=has_api_key,
            )
        return _public_retrieval_settings(core)

    @app.post("/api/retrieval-settings/embedding/test")
    async def test_retrieval_embedding(
        payload: RetrievalTestRequest | None = None,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        spec = _retrieval_test_spec(core, "embedding", payload)
        vectors = await core.provider.embed(spec, ["连通性测试"])
        return {"ok": True, "dimensions": len(vectors[0]) if vectors else 0}

    @app.post("/api/retrieval-settings/rerank/test")
    async def test_retrieval_rerank(
        payload: RetrievalTestRequest | None = None,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        spec = _retrieval_test_spec(core, "rerank", payload)
        await core.provider.rerank(spec, "连通性测试", ["测试文档一", "测试文档二"], 2)
        return {"ok": True}

    @app.get("/api/skills")
    async def list_skills(core: CoreServices = Depends(services)) -> list[dict[str, Any]]:
        return core.skills.list_skills()

    @app.post("/api/skills/import", status_code=201)
    async def import_skill(
        payload: SkillImportRequest, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        return core.skills.import_from_path(payload.path)

    @app.put("/api/skills/{name}/enabled")
    async def set_skill_enabled(
        name: str, payload: SkillEnabledUpdate, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        return core.skills.set_enabled(name, payload.enabled)

    @app.delete("/api/skills/{name}", status_code=204, response_model=None)
    async def delete_skill(name: str, core: CoreServices = Depends(services)) -> None:
        core.skills.delete_skill(name)

    @app.get("/api/projects")
    async def list_projects(core: CoreServices = Depends(services)) -> list[dict[str, Any]]:
        return core.store.list_projects()

    @app.post("/api/projects", status_code=201)
    async def create_project(
        payload: ProjectCreate, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        return core.store.create_project(payload.name, payload.is_pinned)

    @app.patch("/api/projects/{project_id}")
    async def update_project(
        project_id: str,
        payload: ProjectUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return core.store.update_project(project_id, payload.model_dump(exclude_unset=True))

    @app.delete("/api/projects/{project_id}", status_code=204, response_model=None)
    async def delete_project(project_id: str, core: CoreServices = Depends(services)) -> None:
        core.store.delete_project(project_id)

    @app.get("/api/conversations")
    async def list_conversations(
        project_id: str | None = None,
        q: str | None = None,
        core: CoreServices = Depends(services),
    ) -> list[dict[str, Any]]:
        # An empty q means "no filter" - the sidebar sends it while the search
        # box is being cleared.
        return core.store.list_conversations(project_id, query=q or None)

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(
        payload: ConversationCreate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return core.store.create_conversation(
            payload.title,
            payload.project_id,
            payload.model_profile_id,
            payload.is_pinned,
        )

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        return core.store.get_conversation(conversation_id)

    @app.patch("/api/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return core.store.update_conversation(
            conversation_id, payload.model_dump(exclude_unset=True)
        )

    @app.delete("/api/conversations/{conversation_id}", status_code=204, response_model=None)
    async def delete_conversation(
        conversation_id: str, core: CoreServices = Depends(services)
    ) -> None:
        # Permanent, by the user's decision: no recycle bin page exists to send
        # it to, and keeping soft-deleted rows behind an unreachable API was
        # the garbage the user asked not to leave behind. A run still writing
        # into this conversation is flagged cancelled first so its producer
        # stops instead of failing against rows that no longer exist.
        for run_id in core.store.list_active_run_ids(conversation_id):
            core.run_registry.cancel(run_id)
        core.store.delete_conversation_permanently(conversation_id)

    @app.get("/api/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: str, core: CoreServices = Depends(services)
    ) -> list[dict[str, Any]]:
        return core.store.list_messages(conversation_id)

    @app.get("/api/conversations/{conversation_id}/messages/{message_id}/images/{index}")
    async def read_message_image(
        conversation_id: str,
        message_id: str,
        index: int,
        core: CoreServices = Depends(services),
    ) -> FileResponse:
        """One pasted image, by its position in the message.

        The index, not a filename, is what the client names: the path is
        resolved from the message's own metadata, so nothing the request says
        can reach outside the objects directory.
        """
        message = core.store.get_message(message_id)
        if message["conversation_id"] != conversation_id:
            raise NotFoundError("未找到这张图片。")
        filenames = message_images(message)
        if not 0 <= index < len(filenames):
            raise NotFoundError("未找到这张图片。")
        path = image_path(core.store.database, filenames[index])
        if not path.is_file():
            raise NotFoundError("未找到这张图片。")
        return FileResponse(path, media_type=media_type_for(filenames[index]))

    @app.get("/api/conversations/{conversation_id}/run-events")
    async def list_conversation_run_events(
        conversation_id: str, core: CoreServices = Depends(services)
    ) -> dict[str, list[dict[str, Any]]]:
        """The audit trail of every run in a conversation, keyed by run id.

        What each stage of an answer actually took is in the timestamps of these
        records, so a reloaded page can redraw the execution steps it missed -
        including the ones belonging to answers older than the newest.
        """
        return core.store.list_conversation_run_events(conversation_id)

    @app.post("/api/messages/{message_id}/regenerate")
    async def regenerate_message(
        message_id: str,
        payload: RegenerateRequest | None = None,
        core: CoreServices = Depends(services),
    ) -> StreamingResponse:
        run_service = RunService(core)
        run, profile, choice = run_service.start_regenerate(
            message_id,
            model_profile_id=payload.model_profile_id if payload else None,
            choice=RunChoice(
                chat_model=payload.chat_model if payload else None,
                thinking=payload.thinking if payload else "off",
            ),
        )
        return _start_run(run_service, run, profile, choice)

    @app.post("/api/conversations/{conversation_id}/runs")
    async def create_run(
        conversation_id: str,
        payload: RunRequest,
        core: CoreServices = Depends(services),
    ) -> StreamingResponse:
        run_service = RunService(core)
        run, profile, choice = run_service.start(
            conversation_id,
            content=payload.content,
            images=payload.images,
            model_profile_id=payload.model_profile_id,
            choice=RunChoice(chat_model=payload.chat_model, thinking=payload.thinking),
        )
        return _start_run(run_service, run, profile, choice)

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, core: CoreServices = Depends(services)) -> dict[str, Any]:
        return core.store.get_run(run_id)

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str, core: CoreServices = Depends(services)) -> StreamingResponse:
        """Reattach to a run, whether it is still going or long finished."""
        return _stream_run(RunService(core), run_id)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, core: CoreServices = Depends(services)) -> dict[str, Any]:
        return RunService(core).cancel(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def list_run_events(
        run_id: str, core: CoreServices = Depends(services)
    ) -> list[dict[str, Any]]:
        return core.store.list_run_events(run_id)

    @app.post("/api/conversations/{conversation_id}/compact")
    async def compact_conversation(
        conversation_id: str,
        payload: CompactRequest,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        conversation = core.store.get_conversation(conversation_id)
        profile_id = payload.model_profile_id or conversation["model_profile_id"]
        profile = (
            core.store.get_model_profile(profile_id)
            if profile_id
            else core.store.get_default_model_profile()
        )
        artifact = await core.context.compact(conversation_id, profile, force=True)
        return {"artifact": artifact}

    @app.get("/api/memories")
    async def list_memories(
        project_id: str | None = None,
        core: CoreServices = Depends(services),
    ) -> list[dict[str, Any]]:
        return [
            _public_memory(memory)
            for memory in core.store.list_memories(project_id=project_id, include_global=True)
        ]

    @app.patch("/api/memories/{memory_id}")
    async def update_memory(
        memory_id: str,
        payload: MemoryUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        changes = core.store.update_memory(memory_id, payload.model_dump(exclude_unset=True))
        return _public_memory(changes)

    @app.delete("/api/memories/{memory_id}", status_code=204, response_model=None)
    async def delete_memory(memory_id: str, core: CoreServices = Depends(services)) -> None:
        core.store.delete_memory(memory_id)

    @app.post("/api/messages/{message_id}/feedback", status_code=201)
    async def create_feedback(
        message_id: str,
        payload: FeedbackCreate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return core.store.add_feedback(message_id, payload.kind)

    @app.get("/api/knowledge/documents")
    async def list_knowledge_documents(
        project_id: str | None = None,
        core: CoreServices = Depends(services),
    ) -> list[dict[str, Any]]:
        return core.store.list_knowledge_documents(project_id)

    @app.post("/api/knowledge/import", status_code=201)
    async def import_knowledge(
        files: list[UploadFile] = File(...),
        project_id: str | None = Form(default=None),
        relative_paths: list[str] | None = Form(default=None),
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        if project_id:
            core.store.get_project(project_id)
        paths = relative_paths or []
        items = [
            ImportItem(
                filename=upload.filename or "untitled.txt",
                content=await upload.read(),
                relative_path=paths[index] if index < len(paths) else None,
            )
            for index, upload in enumerate(files)
        ]
        return {
            "items": await core.knowledge.import_items(
                items,
                project_id=project_id,
            )
        }

    @app.get("/api/knowledge/search")
    async def search_knowledge(
        query: str,
        project_id: str | None = None,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return {
            "items": await core.knowledge.search(
                query,
                project_id=project_id,
            )
        }

    @app.get("/api/knowledge/documents/{document_id}/content")
    async def get_knowledge_content(
        document_id: str, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        """The canonical text a citation's 查看原文 panel renders, plus its
        section ids so the panel can offer the same addressing the model has."""
        document = core.store.get_knowledge_document(document_id)
        sections = parse_sections(document["content"], document["title"])
        return {
            "id": document["id"],
            "title": document["title"],
            "original_filename": document["original_filename"],
            "content": document["content"],
            "sections": [
                # start/end ride along so the panel can slice the document and
                # scroll straight to the cited section.
                {
                    "id": section.id,
                    "level": section.level,
                    "title": section.title,
                    "start": section.start,
                    "end": section.end,
                }
                for section in sections
            ],
        }

    @app.delete("/api/knowledge/documents/{document_id}", status_code=204, response_model=None)
    async def delete_knowledge_document(
        document_id: str, core: CoreServices = Depends(services)
    ) -> None:
        core.store.delete_knowledge_document(document_id)

    @app.get("/api/trash")
    async def list_trash(core: CoreServices = Depends(services)) -> list[dict[str, Any]]:
        return core.store.list_trash()

    @app.post("/api/trash/{trash_id}/restore")
    async def restore_trash(
        trash_id: str, core: CoreServices = Depends(services)
    ) -> dict[str, Any]:
        return core.store.restore_trash_item(trash_id)

    @app.delete("/api/trash/{trash_id}", status_code=204, response_model=None)
    async def discard_trash_item(trash_id: str, core: CoreServices = Depends(services)) -> None:
        core.store.discard_trash_item(trash_id)

    _mount_frontend(app)
    return app


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built frontend from this process, when one has been built.

    Nothing here runs in development (Vite serves the app and proxies `/api`
    back) or in tests, because `frontend/dist` only exists after `npm run build`.
    That is precisely the case where one process should be able to serve the
    whole application, which is how it ships.
    """
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        return

    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def serve_frontend(path: str) -> Any:
        # A mistyped API path has to come back as JSON. Falling through to
        # index.html would answer a JSON client with a page of HTML and turn a
        # plain 404 into a parse error somewhere far away.
        if path == "api" or path.startswith("api/"):
            return JSONResponse(
                status_code=404, content={"detail": "没有这个接口。", "code": "not_found"}
            )
        candidate = (FRONTEND_DIST / path).resolve()
        if path != "" and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST):
            return FileResponse(candidate)
        # Everything else is a client-side route, so the app gets to answer it.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
