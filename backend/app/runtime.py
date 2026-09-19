from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import BootstrapStore
from app.context import ContextManager
from app.database import Database
from app.errors import JarvisError, SetupRequiredError
from app.knowledge import KnowledgeService
from app.memory import MemoryService
from app.provider import OpenAICompatibleProvider
from app.secrets import KeyringSecretStore, SecretStore
from app.skills import SkillService
from app.store import Store


class RunBroadcast:
    """The live state of one run, shared by everything watching it.

    The producer never writes to an HTTP response; it publishes here, and each
    client reads. That is what lets a run outlive the connection that started
    it, so refreshing the page no longer aborts the answer.

    Text is kept as a cumulative snapshot rather than a queue of deltas. A
    subscriber that connects late, or misses a wake-up, still converges by
    comparing how much it has already sent - so only the newest string is kept
    and a long answer costs one buffer instead of thousands of chunks.
    """

    def __init__(self, assistant_message_id: str) -> None:
        self.assistant_message_id = assistant_message_id
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.content = ""
        self.reasoning = ""
        self.closed = False
        self.version = 0
        self._waiters: list[asyncio.Event] = []

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))
        self._touch()

    def show_text(self, *, content: str, reasoning: str) -> None:
        self.content = content
        self.reasoning = reasoning
        self._touch()

    def close(self) -> None:
        """Mark the run finished so subscribers stop after draining."""
        self.closed = True
        self._touch()

    def finish(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append((event, payload))
        self.close()

    async def wait_for_change(self, version: int) -> None:
        """Block until something is published after ``version``.

        Each waiter gets its own event. Sharing one would let a subscriber that
        is only now waking clear the flag before a peer has seen it.
        """
        if self.version != version:
            return
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        try:
            # Registering above is atomic with the version test - nothing awaits
            # in between - so a publish can no longer slip past unseen.
            if self.version != version:
                return
            await waiter.wait()
        finally:
            self._waiters.remove(waiter)

    def _touch(self) -> None:
        self.version += 1
        for waiter in self._waiters:
            waiter.set()


class RunRegistry:
    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._broadcasts: dict[str, RunBroadcast] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)

    def is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled

    def open(self, run_id: str, assistant_message_id: str) -> RunBroadcast:
        broadcast = RunBroadcast(assistant_message_id)
        self._broadcasts[run_id] = broadcast
        return broadcast

    def broadcast(self, run_id: str) -> RunBroadcast | None:
        return self._broadcasts.get(run_id)

    def track(self, run_id: str, task: asyncio.Task[None]) -> None:
        """Hold the producing task so it is not collected mid-run.

        asyncio keeps only a weak reference to a running task; dropping this
        would let a garbage collection silently stop the answer halfway.
        """
        self._tasks[run_id] = task

    def close(self, run_id: str) -> None:
        self._broadcasts.pop(run_id, None)
        self._tasks.pop(run_id, None)
        self._cancelled.discard(run_id)


@dataclass
class CoreServices:
    database: Database
    store: Store
    provider: OpenAICompatibleProvider
    knowledge: KnowledgeService
    context: ContextManager
    memory: MemoryService
    skills: SkillService
    run_registry: RunRegistry

    @classmethod
    def create(cls, data_directory: Path, secrets: SecretStore | None = None) -> CoreServices:
        database = Database(data_directory)
        database.initialize()
        store = Store(database)
        store.interrupt_orphaned_runs()
        provider = OpenAICompatibleProvider(secrets or KeyringSecretStore())
        knowledge = KnowledgeService(store, provider)
        skills = SkillService(store, data_directory)
        context = ContextManager(store, provider, knowledge, skills)
        memory = MemoryService(store)
        return cls(
            database=database,
            store=store,
            provider=provider,
            knowledge=knowledge,
            context=context,
            memory=memory,
            skills=skills,
            run_registry=RunRegistry(),
        )


class Runtime:
    def __init__(self, bootstrap: BootstrapStore | None = None) -> None:
        self.bootstrap = bootstrap or BootstrapStore.create_default()
        self._services: CoreServices | None = None
        self._startup_error: str | None = None
        existing = self.bootstrap.get_data_directory()
        if existing:
            try:
                self._services = CoreServices.create(existing)
            except (JarvisError, OSError, sqlite3.Error) as error:
                # Running with no services is the honest outcome: the app cannot
                # read or write anything, and carrying on regardless would show an
                # empty conversation list as though that were the truth. The
                # reason is kept so `/api/health` can name the broken directory
                # instead of presenting itself as a first run.
                self._startup_error = str(error)

    @property
    def configured(self) -> bool:
        return self._services is not None

    @property
    def startup_error(self) -> str | None:
        """Why the chosen directory could not be opened, when it could not."""
        return self._startup_error if self._services is None else None

    @property
    def data_directory(self) -> Path | None:
        """Where everything lives, or None before one has been chosen.

        Read off the live services rather than kept as a second copy, so there is
        no way for the path the app reports to drift from the path it is using.
        When those services could not be built at all there is no such path to
        report, and the bootstrap file is then the only record of what was asked
        for - which is what lets the settings screen show which directory is the
        broken one instead of an empty box.
        """
        if self._services is None:
            return self.bootstrap.get_data_directory()
        return self._services.database.data_directory

    def setup(self, data_directory: str) -> CoreServices:
        selected = self.bootstrap.select_data_directory(data_directory)
        self._services = CoreServices.create(selected)
        self._startup_error = None
        return self._services

    def services(self) -> CoreServices:
        if self._services is None:
            raise SetupRequiredError("请先在首次设置中选择 Jarvis 数据目录。")
        return self._services
