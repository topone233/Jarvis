from __future__ import annotations

import contextlib
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.errors import NotFoundError, ProviderError, SetupRequiredError, ValidationError
from app.knowledge import ImportItem
from app.runs import RunService
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
    RunRequest,
    SetupRequest,
)


def _stream_run(
    run_service: RunService,
    run: dict[str, Any],
    profile: dict[str, Any],
    reasoning_level: str,
) -> StreamingResponse:
    return StreamingResponse(
        run_service.stream(run["id"], profile=profile, reasoning_level=reasoning_level or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        return JSONResponse(
            status_code=422,
            content={"detail": error.errors(), "code": "request_validation"},
        )

    def services() -> CoreServices:
        return app.state.runtime.services()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        runtime_instance: Runtime = app.state.runtime
        return {
            "status": "ok",
            "configured": runtime_instance.configured,
            "service": "jarvis-core",
        }

    @app.post("/api/setup")
    async def setup(payload: SetupRequest) -> dict[str, Any]:
        configured = app.state.runtime.setup(payload.data_directory)
        return {
            "configured": True,
            "data_directory": str(configured.database.data_directory),
        }

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
        core: CoreServices = Depends(services),
    ) -> list[dict[str, Any]]:
        return core.store.list_conversations(project_id)

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
        core.store.delete_conversation(conversation_id)

    @app.get("/api/conversations/{conversation_id}/messages")
    async def list_messages(
        conversation_id: str, core: CoreServices = Depends(services)
    ) -> list[dict[str, Any]]:
        return core.store.list_messages(conversation_id)

    @app.delete("/api/messages/{message_id}", status_code=204, response_model=None)
    async def delete_message(message_id: str, core: CoreServices = Depends(services)) -> None:
        core.store.delete_message(message_id)

    @app.post("/api/messages/{message_id}/regenerate")
    async def regenerate_message(
        message_id: str,
        payload: RegenerateRequest | None = None,
        core: CoreServices = Depends(services),
    ) -> StreamingResponse:
        run_service = RunService(core)
        run, profile, reasoning_level = run_service.start_regenerate(
            message_id,
            model_profile_id=payload.model_profile_id if payload else None,
            reasoning_level=payload.reasoning_level if payload else None,
        )
        return _stream_run(run_service, run, profile, reasoning_level)

    @app.post("/api/conversations/{conversation_id}/runs")
    async def create_run(
        conversation_id: str,
        payload: RunRequest,
        core: CoreServices = Depends(services),
    ) -> StreamingResponse:
        run_service = RunService(core)
        run, profile, reasoning_level = run_service.start(
            conversation_id,
            content=payload.content,
            model_profile_id=payload.model_profile_id,
            reasoning_level=payload.reasoning_level,
        )
        return _stream_run(run_service, run, profile, reasoning_level)

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
        return core.store.list_memories(project_id=project_id, include_global=True)

    @app.patch("/api/memories/{memory_id}")
    async def update_memory(
        memory_id: str,
        payload: MemoryUpdate,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        return core.store.update_memory(memory_id, payload.model_dump(exclude_unset=True))

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
        model_profile_id: str | None = Form(default=None),
        relative_paths: list[str] | None = Form(default=None),
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        if project_id:
            core.store.get_project(project_id)
        profile: dict[str, Any] | None = None
        if model_profile_id:
            profile = core.store.get_model_profile(model_profile_id)
        elif core.store.list_model_profiles():
            profile = core.store.get_default_model_profile()
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
                profile=profile,
            )
        }

    @app.get("/api/knowledge/search")
    async def search_knowledge(
        query: str,
        project_id: str | None = None,
        model_profile_id: str | None = None,
        core: CoreServices = Depends(services),
    ) -> dict[str, Any]:
        profile: dict[str, Any] | None = None
        if model_profile_id:
            profile = core.store.get_model_profile(model_profile_id)
        elif core.store.list_model_profiles():
            profile = core.store.get_default_model_profile()
        return {
            "items": await core.knowledge.search(
                query,
                project_id=project_id,
                profile=profile,
            )
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

    return app


app = create_app()
