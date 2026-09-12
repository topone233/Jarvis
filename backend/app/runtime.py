from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import BootstrapStore
from app.context import ContextManager
from app.database import Database
from app.errors import SetupRequiredError
from app.knowledge import KnowledgeService
from app.memory import MemoryService
from app.provider import OpenAICompatibleProvider
from app.secrets import KeyringSecretStore, SecretStore
from app.store import Store


class RunRegistry:
    def __init__(self) -> None:
        self._cancelled: set[str] = set()

    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled

    def clear(self, run_id: str) -> None:
        self._cancelled.discard(run_id)


@dataclass
class CoreServices:
    database: Database
    store: Store
    provider: OpenAICompatibleProvider
    knowledge: KnowledgeService
    context: ContextManager
    memory: MemoryService
    run_registry: RunRegistry

    @classmethod
    def create(cls, data_directory: Path, secrets: SecretStore | None = None) -> CoreServices:
        database = Database(data_directory)
        database.initialize()
        store = Store(database)
        provider = OpenAICompatibleProvider(secrets or KeyringSecretStore())
        knowledge = KnowledgeService(store, provider)
        context = ContextManager(store, provider, knowledge)
        memory = MemoryService(store, provider)
        return cls(
            database=database,
            store=store,
            provider=provider,
            knowledge=knowledge,
            context=context,
            memory=memory,
            run_registry=RunRegistry(),
        )


class Runtime:
    def __init__(self, bootstrap: BootstrapStore | None = None) -> None:
        self.bootstrap = bootstrap or BootstrapStore.create_default()
        self._services: CoreServices | None = None
        existing = self.bootstrap.get_data_directory()
        if existing:
            self._services = CoreServices.create(existing)

    @property
    def configured(self) -> bool:
        return self._services is not None

    def setup(self, data_directory: str) -> CoreServices:
        selected = self.bootstrap.select_data_directory(data_directory)
        self._services = CoreServices.create(selected)
        return self._services

    def services(self) -> CoreServices:
        if self._services is None:
            raise SetupRequiredError("请先在首次设置中选择 Jarvis 数据目录。")
        return self._services
