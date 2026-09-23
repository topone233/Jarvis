"""The bash tool on its own: finding a shell, running one command, bounding it.

The subprocess tests drive the machine's real Git Bash, exactly as the skill
tests drive the real interpreter - the thing under test is the launch, the
decoding, the bounds, and the answers, none of which a fake would exercise.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from app.bash_tool import BASH_STOPPED_TEXT, BASH_TOOLS, BashToolService, find_bash
from app.runtime import CoreServices
from app.settings import read_bash_settings


async def _one(command: str, cwd: Path, *, should_cancel: Any = None) -> str:
    return await BashToolService().execute(command, cwd=cwd, should_cancel=should_cancel)


async def test_a_command_runs_and_its_output_comes_back(core: CoreServices, tmp_path: Path) -> None:
    output = await _one("echo 你好 bash", tmp_path)
    assert "你好 bash" in output


async def test_stderr_rides_with_stdout(core: CoreServices, tmp_path: Path) -> None:
    output = await _one("echo 坏消息 >&2", tmp_path)
    assert "坏消息" in output


async def test_a_multiline_script_rides_stdin(core: CoreServices, tmp_path: Path) -> None:
    output = await _one("echo 一\necho 二", tmp_path)
    assert "一" in output and "二" in output


async def test_a_failure_reports_its_exit_code(core: CoreServices, tmp_path: Path) -> None:
    output = await _one("echo 坏输出; exit 3", tmp_path)
    assert "坏输出" in output
    assert "退出码 3" in output


async def test_a_command_runs_in_the_working_directory(core: CoreServices, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    await _one("touch marker.txt", work)
    assert (work / "marker.txt").is_file()


async def test_a_command_killed_by_a_cancel_answers_the_stopped_text(
    core: CoreServices, tmp_path: Path
) -> None:
    output = await _one("sleep 5", tmp_path, should_cancel=lambda: True)
    assert output == BASH_STOPPED_TEXT


async def test_a_command_that_overruns_the_timeout_is_killed(
    core: CoreServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.bash_tool.BASH_TIMEOUT_SECONDS", 0.3)
    output = await _one("sleep 5", tmp_path)
    assert "超过 0.3 秒" in output


async def test_enormous_output_is_truncated(
    core: CoreServices, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.bash_tool.BASH_OUTPUT_MAX_CHARS", 100)
    output = await _one("printf 'x%.0s' $(seq 500)", tmp_path)
    assert "已截断" in output
    assert len(output) < 200


def test_tool_definition_is_single_function_with_command() -> None:
    assert len(BASH_TOOLS) == 1
    function: dict[str, Any] = BASH_TOOLS[0]["function"]
    assert function["name"] == "bash"
    assert function["parameters"]["required"] == ["command"]


def test_find_bash_prefers_the_known_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    known = tmp_path / "Git" / "bin" / "bash.exe"
    known.parent.mkdir(parents=True)
    known.write_bytes(b"")
    monkeypatch.setattr("app.bash_tool._bash_candidates", lambda: [known])

    assert find_bash() == str(known)


def test_find_bash_refuses_the_wsl_bash_in_system32(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing installed in the usual places, and PATH only offers System32's
    # WSL bash - which would run the command in Linux. The honest answer is
    # that there is no Git Bash.
    monkeypatch.setattr("app.bash_tool._bash_candidates", lambda: [])
    monkeypatch.setattr("app.bash_tool.which", lambda name: r"C:\Windows\System32\bash.exe")
    assert find_bash() is None

    monkeypatch.setattr("app.bash_tool.which", lambda name: r"D:\Git\bin\bash.exe")
    assert find_bash() == r"D:\Git\bin\bash.exe"


def test_find_bash_finds_this_machine_git_bash() -> None:
    # The one environment guarantee the feature rests on: this test suite
    # runs where Git for Windows exists, so the real search must find it.
    assert find_bash() is not None


def test_read_bash_settings_falls_back_to_the_defaults(core: CoreServices) -> None:
    settings = read_bash_settings(core.store)
    assert settings.enabled is True
    assert settings.working_dir is None
    assert settings.grace_seconds == 5
    assert settings.approval_mode == "ask"

    core.store.set_setting("bash_enabled", False)
    core.store.set_setting("bash_working_dir", "  C:/work  ")
    # isinstance(True, int) holds, so a boolean must not become a grace of 1.
    core.store.set_setting("bash_grace_seconds", True)
    settings = read_bash_settings(core.store)
    assert settings.enabled is False
    assert settings.working_dir == "C:/work"
    assert settings.grace_seconds == 5
    assert settings.approval_mode == "ask"

    core.store.set_setting("bash_approval_mode", "grace")
    assert read_bash_settings(core.store).approval_mode == "grace"
    # A mode the code has never heard of is the default, not a crash.
    core.store.set_setting("bash_approval_mode", "auto")
    assert read_bash_settings(core.store).approval_mode == "ask"


async def test_the_grace_window_reports_a_stop_in_time(core: CoreServices) -> None:
    from app.runs import grace_window

    registry = core.run_registry
    # Nothing cancels inside a short window: the call goes through.
    assert await grace_window(registry, "run-1", 0.05) is False

    # A cancel that is already set when the window opens stops it at once.
    registry.cancel("run-2")
    assert await grace_window(registry, "run-2", 5) is True


async def test_the_grace_window_sees_a_cancel_that_arrives_midway(core: CoreServices) -> None:
    from app.runs import grace_window

    registry = core.run_registry
    task = asyncio.ensure_future(grace_window(registry, "run-3", 5))
    await asyncio.sleep(0.05)
    registry.cancel("run-3")
    assert await asyncio.wait_for(task, 2) is True


@pytest.mark.skipif(sys.platform != "win32", reason="the selector loop is a Windows affair")
def test_a_command_runs_on_the_selector_loop(tmp_path: Path) -> None:
    """The regression behind `执行异常：` once coming out blank.

    uvicorn's --reload server runs Windows' selector event loop, which cannot
    spawn subprocesses: its async API raises a bare NotImplementedError. The
    tool drives the subprocess from a worker thread precisely so that it also
    works here - under a selector loop chosen on purpose.
    """
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        output = asyncio.run(_one("echo 你好 selector", tmp_path))
    finally:
        asyncio.set_event_loop_policy(previous)
    assert "你好 selector" in output


@pytest.mark.skipif(sys.platform != "win32", reason="the selector loop is a Windows affair")
def test_a_cancel_is_seen_on_the_selector_loop(tmp_path: Path) -> None:
    """The thread path must keep the cancel contract, not only the happy path."""
    previous = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        output = asyncio.run(_one("sleep 5", tmp_path, should_cancel=lambda: True))
    finally:
        asyncio.set_event_loop_policy(previous)
    assert output == BASH_STOPPED_TEXT
