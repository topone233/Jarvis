"""The plugin host: what turns a folder in the project's ``plugin/`` directory
into a live part of Jarvis.

A plugin is a directory holding a ``plugin.py``, whose module-level constants
say what it is and what a user can configure for it:

- ``MANIFEST`` - ``{id, name, description, version}``. The id must match the
  directory name and the skill-name charset; it is the mount point
  (``/api/plugins/<id>``), the settings key, and the hook the frontend
  registers by.
- ``SETTINGS_SCHEMA`` - one entry per configurable field: ``{key, label,
  type, default}`` with type one of ``hotkey|bool|int|text``. The settings
  screen renders the form from this schema; no plugin ships UI for its own
  configuration.
- ``ensure_services(ctx)`` - optional, run once per data directory. Anything
  the plugin keeps for the lifetime of a data directory (an index, a handle)
  goes into ``ctx.state``.
- ``create_router(ctx)`` - the ``APIRouter`` mounted at the plugin's prefix.
  Every handler reaches the live services through ``ctx``, so nothing is
  captured stale: a data-directory switch is picked up by the very next
  request.

Two axes of state live apart, like skills: the filesystem is the source of
truth for what exists, and the settings KV only holds the disabled list and
per-plugin config. Disabling does not unmount the router - a gate dependency
answers 409 instead - so the switch takes effect without a restart, and a
newly dropped folder is one ``POST /api/plugins/reload`` away. Python cannot
re-import a module that has already been imported, so code changes to a
loaded plugin still take a backend restart; that limit is stated here rather
than pretended away.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import Depends, Request

from app.errors import NotFoundError, PluginDisabledError, ValidationError

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from app.runtime import CoreServices

PLUGIN_FILE = "plugin.py"

#: The settings row holding the disabled ids. No row while everything is on -
#: the same "no row is the code default" model every setting follows.
PLUGINS_DISABLED = "plugins_disabled"

#: Per-plugin config rows: one JSON object under ``plugin_config_<id>``.
CONFIG_PREFIX = "plugin_config_"

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Where plugins live: the project-root ``plugin/`` directory, next to
#: ``backend/`` and ``frontend/`` - this file is ``backend/app/plugin_host.py``.
PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugin"

SETTING_TYPES = ("hotkey", "bool", "int", "text")

#: The default type each schema entry must pair with its declared type, so a
#: plugin cannot ship a schema the settings form would render into a crash.
_DEFAULT_TYPES: dict[str, tuple[type, ...]] = {
    "hotkey": (str,),
    "text": (str,),
    "bool": (bool,),
    "int": (int,),
}


class PluginContext:
    """What plugin code is handed, and the only sanctioned way in.

    Every property resolves on access through the callable it was built with -
    the live services once mounted, one fixed instance during load - so a
    handler never holds a store or a provider that a data-directory switch
    has left behind. ``state`` is the plugin's own scratch space, owned by the
    host and thrown away with the services instance it belongs to.
    """

    def __init__(self, services: Callable[[], CoreServices], plugin_id: str) -> None:
        self._services = services
        self.id = plugin_id

    @property
    def services(self) -> CoreServices:
        return self._services()

    @property
    def store(self):
        return self.services.store

    @property
    def database(self):
        return self.services.database

    @property
    def provider(self):
        return self.services.provider

    @property
    def data_directory(self) -> Path:
        return self.services.database.data_directory

    @property
    def config(self) -> dict[str, Any]:
        return self.services.plugins.config_for(self.id)

    @property
    def state(self) -> dict[str, Any]:
        return self.services.plugins.state_for(self.id)


@dataclass
class LoadedPlugin:
    """One imported plugin: its module, the manifest copy the API serves, and
    the scratch state ``ensure_services`` was invited to fill."""

    module: Any
    manifest: dict[str, Any]
    settings_schema: list[dict[str, Any]]
    state: dict[str, Any] = field(default_factory=dict)


def _description(
    plugin_id: str,
    *,
    manifest: dict[str, Any] | None,
    schema: list[dict[str, Any]] | None,
    config: dict[str, Any],
    enabled: bool,
    broken: bool = False,
    error: str | None = None,
    loaded: bool = True,
) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "name": manifest["name"] if manifest else plugin_id,
        "description": manifest["description"] if manifest else None,
        "version": manifest["version"] if manifest else None,
        "settings_schema": schema or [],
        "config": config,
        "enabled": enabled,
        "broken": broken,
        "error": error,
        "loaded": loaded,
    }


class PluginService:
    """The plugins in the project's ``plugin/`` directory, and which are on.

    Loaded plugins live in memory keyed by id; a directory that failed to
    load is remembered in ``_broken`` with the reason, so the settings screen
    can show the error instead of the folder silently missing. Directories
    that appeared after the last load show as ``loaded: False`` until a
    reload brings them in.
    """

    def __init__(self, store: Any, root: Path) -> None:
        self.store = store
        self.plugins_dir = root
        self._loaded: dict[str, LoadedPlugin] = {}
        self._broken: dict[str, str] = {}
        self._current_services: Any = None

    # --- loading ---------------------------------------------------------

    def load_all(self) -> None:
        """Import every plugin folder once, then run each ``ensure_services``.

        Called when a data directory is opened: the scan itself needs no
        services, but the hook does, and a directory switch must rebuild the
        per-directory state with it.
        """
        for directory in self._directories():
            self._load(directory)
        for plugin_id in list(self._loaded):
            self._ensure(plugin_id)

    def reload(self) -> dict[str, Any]:
        """Pick up folders that appeared since the last load.

        Already-loaded plugins keep their imported module - re-importing live
        code is how you get two versions of a class in one process - so this
        is additive by design, and the response says so.
        """
        before = set(self._loaded)
        for directory in self._directories():
            if directory.name in self._loaded or directory.name in self._broken:
                continue
            self._load(directory)
        added = [plugin_id for plugin_id in self._loaded if plugin_id not in before]
        for plugin_id in added:
            self._ensure(plugin_id)
        return {"added": added}

    def _directories(self) -> list[Path]:
        if not self.plugins_dir.is_dir():
            return []
        return [
            entry
            for entry in sorted(self.plugins_dir.iterdir())
            if entry.is_dir() and not entry.name.startswith(".")
        ]

    def _load(self, directory: Path) -> None:
        """Import one folder's plugin.py, or remember why that did not work.

        The module gets a parent package carved out for it (``jarvis_plugin_<id>``
        whose ``__path__`` is the plugin folder) rather than joining ``app.*``:
        a plugin is a guest, its imports should never be mistaken for the
        host's, and its own modules - ``from . import helpers`` - must resolve
        inside its folder and nowhere else, which the carved package both
        enables and confines.
        """
        plugin_file = directory / PLUGIN_FILE
        if not plugin_file.is_file():
            self._broken[directory.name] = f"目录里没有 {PLUGIN_FILE}。"
            return
        package_name = f"jarvis_plugin_{directory.name.replace('-', '_')}"
        package = sys.modules.get(package_name)
        if package is None:
            package = importlib.util.module_from_spec(
                importlib.machinery.ModuleSpec(package_name, None, is_package=True)
            )
            package.__path__ = [str(directory)]  # type: ignore[attr-defined]
            sys.modules[package_name] = package
        module_name = f"{package_name}.plugin"
        spec = importlib.util.spec_from_file_location(module_name, plugin_file)
        if spec is None or spec.loader is None:
            self._broken[directory.name] = "plugin.py 无法加载（不是常规的 Python 源文件）。"
            return
        module: Any = importlib.util.module_from_spec(spec)
        module.__package__ = package_name
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as error:  # noqa: BLE001 - a plugin crash must not take the host down
            sys.modules.pop(module_name, None)
            self._broken[directory.name] = f"plugin.py 无法加载：{error}"
            return
        try:
            manifest, schema = self._validate(module, directory.name)
        except ValidationError as error:
            sys.modules.pop(module_name, None)
            self._broken[directory.name] = str(error)
            return
        self._loaded[manifest["id"]] = LoadedPlugin(
            module=module, manifest=manifest, settings_schema=schema
        )
        self._broken.pop(directory.name, None)

    def _validate(self, module: Any, directory_name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        manifest = getattr(module, "MANIFEST", None)
        if not isinstance(manifest, dict):
            raise ValidationError("plugin.py 缺少 MANIFEST（应是一个字典）。")
        for key in ("id", "name", "description", "version"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                raise ValidationError(f"MANIFEST 缺少有效的 {key} 字段。")
        plugin_id = manifest["id"]
        if not NAME_PATTERN.fullmatch(plugin_id):
            raise ValidationError(
                f"插件 id「{plugin_id}」不合法：只能用小写字母、数字和中划线。"
            )
        if plugin_id != directory_name:
            raise ValidationError(
                f"MANIFEST 的 id（{plugin_id}）与目录名（{directory_name}）不一致。"
            )
        schema = getattr(module, "SETTINGS_SCHEMA", [])
        if not isinstance(schema, list):
            raise ValidationError("SETTINGS_SCHEMA 应是一个列表。")
        seen: set[str] = set()
        for entry in schema:
            if not isinstance(entry, dict):
                raise ValidationError("SETTINGS_SCHEMA 的每一项应是一个字典。")
            key = entry.get("key")
            label = entry.get("label")
            kind = entry.get("type")
            if not isinstance(key, str) or not key:
                raise ValidationError("配置项缺少 key。")
            if key in seen:
                raise ValidationError(f"配置项 key「{key}」重复。")
            seen.add(key)
            if not isinstance(label, str) or not label:
                raise ValidationError(f"配置项 {key} 缺少 label。")
            if kind not in SETTING_TYPES:
                raise ValidationError(f"配置项 {key} 的 type 必须是 {'、'.join(SETTING_TYPES)} 之一。")
            default = entry.get("default")
            if not isinstance(default, _DEFAULT_TYPES[kind]) or (
                kind == "int" and isinstance(default, bool)
            ):
                raise ValidationError(f"配置项 {key} 的 default 与其类型 {kind} 不符。")
        if not callable(getattr(module, "create_router", None)):
            raise ValidationError("plugin.py 缺少 create_router(ctx) 函数。")
        return manifest, schema

    def _ensure(self, plugin_id: str) -> None:
        """Run the optional ensure_services hook; a crash there is the plugin's
        broken state, never the host's."""
        module = self._loaded[plugin_id].module
        ensure = getattr(module, "ensure_services", None)
        if not callable(ensure):
            return
        context = PluginContext(lambda: self._current_services, plugin_id)
        try:
            ensure(context)
        except Exception as error:  # noqa: BLE001
            self._broken[plugin_id] = f"初始化失败：{error}"
            del self._loaded[plugin_id]

    def bind_services(self, services: Any) -> None:
        """Point the load-time context at the services instance being built.

        ``CoreServices.create`` constructs this service before the instance
        exists, so the binding lands right after; the hook contexts resolve
        through it, and every property still reads live off that one
        instance's store, database and provider.
        """
        self._current_services = services

    # --- mounting --------------------------------------------------------

    def mount_into(self, app: FastAPI) -> None:
        """Mount every loaded plugin's router once per app.

        Mounted ids are tracked on the app, not the service: the same service
        can meet several apps across a test session, and each needs its own
        copy of the routes.

        The routes are inserted *before* the frontend's catch-all rather than
        appended after it - Starlette matches in registration order, and a
        plugin mounted by a live reload would otherwise be shadowed forever
        by the ``/{path:path}`` route that serves index.html.
        """
        mounted: set[str] = getattr(app.state, "plugin_mounted_ids", set())
        for plugin_id, loaded in list(self._loaded.items()):
            if plugin_id in mounted:
                continue
            context = PluginContext(
                lambda app=app: app.state.runtime.services(), plugin_id
            )
            try:
                router: APIRouter = loaded.module.create_router(context)
            except Exception as error:  # noqa: BLE001
                del self._loaded[plugin_id]
                self._broken[plugin_id] = f"create_router 失败：{error}"
                continue
            routes_before = len(app.router.routes)
            app.include_router(
                router,
                prefix=f"/api/plugins/{plugin_id}",
                dependencies=[Depends(self._gate(plugin_id))],
            )
            self._lift_before_catch_all(app, routes_before)
            mounted.add(plugin_id)
        app.state.plugin_mounted_ids = mounted

    @staticmethod
    def _lift_before_catch_all(app: FastAPI, first_new: int) -> None:
        """Move just-added routes in front of the frontend catch-all."""
        routes = app.router.routes
        added = routes[first_new:]
        del routes[first_new:]
        position = len(routes)
        for index, route in enumerate(routes):
            if getattr(route, "path", None) == "/{path:path}":
                position = index
                break
        routes[position:position] = added

    @staticmethod
    def _gate(plugin_id: str) -> Callable[[Request], None]:
        """The mounted router's switch. Read per request, so flipping it in the
        settings takes effect on the next call, no restart involved."""

        def check(request: Request) -> None:
            plugins = request.app.state.runtime.services().plugins
            if not plugins.is_enabled(plugin_id):
                raise PluginDisabledError(f"插件 {plugin_id} 已停用，请先在设置里开启。")

        return check

    # --- reading ---------------------------------------------------------

    def descriptions(self) -> list[dict[str, Any]]:
        """Everything the settings screen draws, in one pass.

        A directory that exists but was never imported shows as ``loaded:
        False`` - it arrived after the last load, and one reload brings it in.
        """
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for directory in self._directories():
            name = directory.name
            if name in self._loaded:
                loaded = self._loaded[name]
                entries.append(
                    _description(
                        name,
                        manifest=loaded.manifest,
                        schema=loaded.settings_schema,
                        config=self.config_for(name),
                        enabled=self.is_enabled(name),
                    )
                )
                seen.add(name)
            elif name in self._broken:
                entries.append(
                    _description(
                        name,
                        manifest=None,
                        schema=None,
                        config={},
                        enabled=self.is_enabled(name),
                        broken=True,
                        error=self._broken[name],
                    )
                )
                seen.add(name)
            else:
                entries.append(
                    _description(
                        name,
                        manifest=None,
                        schema=None,
                        config={},
                        enabled=self.is_enabled(name),
                        loaded=False,
                    )
                )
        # Anything loaded but whose directory has vanished keeps its entry
        # until a reload - deleting a folder mid-session should not make its
        # config row unreachable in the same breath.
        for plugin_id, loaded in self._loaded.items():
            if plugin_id not in seen:
                entries.append(
                    _description(
                        plugin_id,
                        manifest=loaded.manifest,
                        schema=loaded.settings_schema,
                        config=self.config_for(plugin_id),
                        enabled=self.is_enabled(plugin_id),
                        broken=True,
                        error="插件目录已不存在，重新扫描后会移除。",
                    )
                )
        for plugin_id, error in self._broken.items():
            if plugin_id not in seen:
                entries.append(
                    _description(
                        plugin_id,
                        manifest=None,
                        schema=None,
                        config={},
                        enabled=self.is_enabled(plugin_id),
                        broken=True,
                        error=error,
                    )
                )
        return entries

    def is_enabled(self, plugin_id: str) -> bool:
        return plugin_id not in self._disabled()

    def config_for(self, plugin_id: str) -> dict[str, Any]:
        """The config in force: stored values over schema defaults."""
        defaults = {
            entry["key"]: entry["default"]
            for entry in self._loaded[plugin_id].settings_schema
        }
        stored = self.store.get_setting(CONFIG_PREFIX + plugin_id)
        return {**defaults, **(stored if isinstance(stored, dict) else {})}

    def state_for(self, plugin_id: str) -> dict[str, Any]:
        return self._loaded[plugin_id].state

    # --- writing ---------------------------------------------------------

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        self._require(plugin_id)
        disabled = [entry for entry in self._disabled() if entry != plugin_id]
        if not enabled:
            disabled.append(plugin_id)
        if disabled:
            self.store.set_setting(PLUGINS_DISABLED, disabled)
        else:
            # Everything on again is the shipped default: the row would only
            # be a stale copy of that, the same deal skills make.
            self.store.delete_setting(PLUGINS_DISABLED)
        return self._describe_loaded(plugin_id)

    def set_config(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        self._require(plugin_id)
        schema = {entry["key"]: entry for entry in self._loaded[plugin_id].settings_schema}
        for key, value in values.items():
            if key not in schema:
                raise ValidationError(f"插件 {plugin_id} 没有名为 {key} 的配置项。")
            kind = schema[key]["type"]
            if not isinstance(value, _DEFAULT_TYPES[kind]) or (kind == "int" and isinstance(value, bool)):
                raise ValidationError(f"配置项 {key} 的值应是 {kind} 类型。")
            if kind == "hotkey" and not value.strip():
                raise ValidationError(f"配置项 {key} 不能为空。")
        stored = self.store.get_setting(CONFIG_PREFIX + plugin_id)
        merged = {**(stored if isinstance(stored, dict) else {}), **values}
        self.store.set_setting(CONFIG_PREFIX + plugin_id, merged)
        return self._describe_loaded(plugin_id)

    # --- helpers ---------------------------------------------------------

    def _require(self, plugin_id: str) -> None:
        if plugin_id not in self._loaded:
            raise NotFoundError(f"没有这个插件：{plugin_id}。")

    def _describe_loaded(self, plugin_id: str) -> dict[str, Any]:
        loaded = self._loaded[plugin_id]
        return _description(
            plugin_id,
            manifest=loaded.manifest,
            schema=loaded.settings_schema,
            config=self.config_for(plugin_id),
            enabled=self.is_enabled(plugin_id),
        )

    def _disabled(self) -> list[str]:
        stored = self.store.get_setting(PLUGINS_DISABLED)
        if not isinstance(stored, list):
            return []
        return [entry for entry in stored if isinstance(entry, str)]
