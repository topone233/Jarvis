"""The plugin host: loading, validation, the switch, and the config store.

The host tests run against synthetic plugin folders in a tmp directory - the
real notepad plugin belongs to the API tests, and a host test that depends on
a plugin shipped with the app could not say anything about third-party
folders. The synthetic plugin is the smallest honest one: manifest, one
setting, a router with one route, an ensure hook that records its calls.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BootstrapStore
from app.errors import ValidationError
from app.main import create_app
from app.plugin_host import PluginService
from app.runtime import CoreServices, Runtime
from app.secrets import InMemorySecretStore

GOOD_PLUGIN = """
from fastapi import APIRouter

MANIFEST = {
    "id": "gadget",
    "name": "小工具",
    "description": "一个用来测试的插件。",
    "version": "0.1.0",
}

SETTINGS_SCHEMA = [
    {"key": "mode", "label": "模式", "type": "text", "default": "fast"},
]

ENSURE_CALLS = []

def ensure_services(ctx):
    ENSURE_CALLS.append(ctx.data_directory)
    ctx.state["ready"] = True

def create_router(ctx):
    router = APIRouter()

    @router.get("/probe")
    async def probe():
        return {"ready": ctx.state.get("ready"), "config": ctx.config}

    return router
"""

MODULE_NAME = "jarvis_plugin_gadget.plugin"


def _write_plugin(root: Path, name: str, source: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "plugin.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return directory


@pytest.fixture
def plugin_root(tmp_path: Path) -> Path:
    return tmp_path / "plugin"


@pytest.fixture
def service(core: CoreServices, plugin_root: Path) -> PluginService:
    """A host over the synthetic folder, installed as the live one.

    Installed - not merely constructed - because a plugin context resolves
    state and config through ``services.plugins``: whatever is installed
    there is the service the hooks and handlers talk to.
    """
    service = PluginService(core.store, plugin_root)
    service.bind_services(core)
    core.plugins = service
    return service


@pytest.fixture
def api_client(service: PluginService, tmp_path: Path) -> Iterator[TestClient]:
    runtime = Runtime(BootstrapStore(tmp_path / "bootstrap"))
    runtime._services = service._current_services
    with TestClient(create_app(runtime)) as test_client:
        yield test_client


def test_a_good_plugin_loads_and_serves(service: PluginService, plugin_root: Path) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    service.load_all()

    entry = service.descriptions()[0]
    assert entry["id"] == "gadget"
    assert entry["name"] == "小工具"
    assert entry["enabled"] is True
    assert entry["broken"] is False
    assert entry["config"] == {"mode": "fast"}


def test_a_folder_without_plugin_py_is_broken_not_missing(
    service: PluginService, plugin_root: Path
) -> None:
    (plugin_root / "empty").mkdir(parents=True)
    service.load_all()

    entry = service.descriptions()[0]
    assert entry["broken"] is True
    assert "plugin.py" in entry["error"]


def test_a_manifest_id_must_match_its_directory(service: PluginService, plugin_root: Path) -> None:
    _write_plugin(
        plugin_root,
        "folder-name",
        'MANIFEST = {"id": "other-name", "name": "X", "description": "d", "version": "1"}',
    )
    service.load_all()

    entry = service.descriptions()[0]
    assert entry["broken"] is True
    assert "不一致" in entry["error"]


def test_a_schema_default_must_fit_its_declared_type(
    service: PluginService, plugin_root: Path
) -> None:
    _write_plugin(
        plugin_root,
        "gadget",
        'MANIFEST = {"id": "gadget", "name": "X", "description": "d", "version": "1"}\n'
        'SETTINGS_SCHEMA = [{"key": "n", "label": "N", "type": "int", "default": "不是数字"}]',
    )
    service.load_all()

    assert service.descriptions()[0]["broken"] is True


def test_ensure_services_gets_the_data_directory_and_reruns_per_services(
    service: PluginService, plugin_root: Path, core: CoreServices, tmp_path: Path
) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    service.load_all()

    assert service.state_for("gadget")["ready"] is True
    first_module = sys.modules[MODULE_NAME]
    assert first_module.ENSURE_CALLS == [core.database.data_directory]

    # A second data directory is a second services instance and a second
    # host over the same folder: the hook must run again there, with fresh
    # module state - which is what the host's re-exec buys.
    (tmp_path / "other-data").mkdir()
    other = CoreServices.create(tmp_path / "other-data", InMemorySecretStore())
    second = PluginService(other.store, plugin_root)
    second.bind_services(other)
    other.plugins = second
    second.load_all()

    second_module = sys.modules[MODULE_NAME]
    assert second_module is not first_module
    assert second.state_for("gadget")["ready"] is True
    assert second_module.ENSURE_CALLS == [other.database.data_directory]


def test_reload_picks_up_new_folders_but_keeps_loaded_ones(
    service: PluginService, plugin_root: Path
) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    service.load_all()
    _write_plugin(plugin_root, "second", GOOD_PLUGIN.replace('"id": "gadget"', '"id": "second"'))

    outcome = service.reload()

    assert outcome["added"] == ["second"]
    assert {entry["id"] for entry in service.descriptions()} == {"gadget", "second"}


def test_the_switch_flips_without_touching_the_folder(
    service: PluginService, plugin_root: Path
) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    service.load_all()

    service.set_enabled("gadget", False)
    assert service.is_enabled("gadget") is False
    # The disabled list is the only thing written; the folder still loads.
    assert service.store.get_setting("plugins_disabled") == ["gadget"]

    service.set_enabled("gadget", True)
    assert service.is_enabled("gadget") is True
    # All-on deletes the row: no stale copy of the default.
    assert service.store.get_setting("plugins_disabled") is None


def test_config_validates_against_the_schema(service: PluginService, plugin_root: Path) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    service.load_all()

    with pytest.raises(ValidationError, match="没有名为"):
        service.set_config("gadget", {"nonsense": 1})
    with pytest.raises(ValidationError, match="text 类型"):
        service.set_config("gadget", {"mode": 3})

    updated = service.set_config("gadget", {"mode": "slow"})
    assert updated["config"] == {"mode": "slow"}


# --- the mounted API -----------------------------------------------------------


def test_the_api_lists_plugins_and_the_gate_refuses_the_disabled(
    api_client: TestClient, plugin_root: Path
) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    # The app was built before the folder existed - what a reload is for.
    response = api_client.post("/api/plugins/reload")
    assert response.status_code == 200

    listed = response.json()["plugins"]
    assert [entry["id"] for entry in listed] == ["gadget"]

    probe = api_client.get("/api/plugins/gadget/probe")
    assert probe.status_code == 200
    assert probe.json() == {"ready": True, "config": {"mode": "fast"}}

    off = api_client.put("/api/plugins/gadget/enabled", json={"enabled": False})
    assert off.status_code == 200
    refused = api_client.get("/api/plugins/gadget/probe")
    assert refused.status_code == 409
    assert refused.json()["code"] == "plugin_disabled"

    # Flipping it back on takes effect on the very next call - no remount.
    api_client.put("/api/plugins/gadget/enabled", json={"enabled": True})
    assert api_client.get("/api/plugins/gadget/probe").status_code == 200


def test_config_saves_and_reaches_the_plugin(api_client: TestClient, plugin_root: Path) -> None:
    _write_plugin(plugin_root, "gadget", GOOD_PLUGIN)
    api_client.post("/api/plugins/reload")

    saved = api_client.put("/api/plugins/gadget/config", json={"settings": {"mode": "slow"}})
    assert saved.status_code == 200
    assert saved.json()["config"] == {"mode": "slow"}

    probe = api_client.get("/api/plugins/gadget/probe")
    assert probe.json()["config"] == {"mode": "slow"}
