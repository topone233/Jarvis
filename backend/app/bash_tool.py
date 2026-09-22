"""The `bash` tool: the model's shell on this Windows machine.

One tool, one parameter - the command line - run by Git Bash in the working
directory the user has configured. Git Bash rather than PowerShell because the
model's command vocabulary is POSIX: `grep`, `sed`, pipes and `&&` come out
right, while PowerShell 5.1 has no `&&` and its aliases swallow bash spellings
with different meanings. Bash also reaches everything PowerShell can, since
`powershell -Command ...` is itself just a command.

Every failure answers as text, like the knowledge and skill tools - the message
is the model's only schema. Execution is real and unconfined, by the same
decision that leaves skills unsandboxed; what makes an irreversible command
survivable is the grace window runs.py holds before each call, with the raw
command already on the audit trail and the stop button still ahead of it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from shutil import which
from typing import Any

#: How long a command may run before it is killed, and how much of its output
#: comes back - the same bounds a skill script lives under, for the same
#: reasons: a hung command must not hang the run, an enormous output must not
#: eat the context window in one tool message.
BASH_TIMEOUT_SECONDS = 120
BASH_OUTPUT_MAX_CHARS = 20_000

#: How often the wait checks for a cancel while a command is running. Small
#: enough that the stop button feels immediate; small enough not to matter.
CANCEL_POLL_SECONDS = 0.2

#: What `execute` answers when the child was killed because the user stopped
#: the run. Shared as a constant so runs.py can tell this from an ordinary
#: completion and mark the audit row cancelled instead of completed.
BASH_STOPPED_TEXT = "bash: 命令已被用户停止。"

#: Where Git Bash is looked for, most-specific first. PATH is the last resort
#: and even then a System32 hit is refused: Windows ships a `bash.exe` there
#: for WSL, and System32 sits early in PATH, so a plain `which` can silently
#: hand the command to a Linux environment the user never asked for.
KNOWN_BASH_PATHS = (
    Path(r"C:\Program Files\Git\bin\bash.exe"),
    Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
)


def _bash_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA")
    candidates = list(KNOWN_BASH_PATHS)
    if local:
        candidates.append(Path(local) / "Programs" / "Git" / "bin" / "bash.exe")
    return candidates


def find_bash() -> str | None:
    """The bash.exe to drive, or None when Git for Windows is not installed."""
    for candidate in _bash_candidates():
        if candidate.is_file():
            return str(candidate)
    found = which("bash")
    if found and Path(found).parent.name.casefold() != "system32":
        return found
    return None


def _git_root(bash: Path) -> Path:
    """The Git installation a bash.exe belongs to - the one holding usr/bin."""
    for ancestor in bash.parents:
        if (ancestor / "usr" / "bin").is_dir():
            return ancestor
    return bash.parent.parent


def _bash_env(root: Path) -> dict[str, str]:
    """The child environment, with Git's own tools and UTF-8 in force.

    Windows PATH carries Git's cmd directory but not usr\\bin, where ls, grep
    and the rest live; putting it ahead is what lets `ls | grep x` find them
    without running a login shell's profile. LANG pins the tools that respect
    it to UTF-8, matching how the output is decoded.
    """
    env = os.environ.copy()
    env["PATH"] = rf"{root}\usr\bin;{root}\mingw64\bin;" + env.get("PATH", "")
    env["LANG"] = "C.UTF-8"
    return env


def _run_sync(
    bash_path: str,
    command: str,
    cwd: Path,
    env: dict[str, str],
    should_cancel: Callable[[], bool] | None,
) -> str:
    """One command, run to completion - the whole wait, kill and collect.

    The subprocess is driven synchronously and `execute` puts it on a worker
    thread, so the tool works under every event loop: uvicorn's --reload
    server runs Windows' selector loop, which cannot spawn subprocesses at
    all (its async API raises a bare NotImplementedError, whose empty message
    is how `执行异常：` once came out blank). A thread is the one launcher
    that is loop-agnostic.

    communicate() is retried in short slices so a cancel is noticed within
    CANCEL_POLL_SECONDS. A slice that ends in TimeoutExpired keeps what it
    already read - CPython accumulates the partial output on the Popen object
    and the docs promise a retry loses none of it - so the final call returns
    the whole output.
    """
    try:
        process = subprocess.Popen(
            [bash_path, "-s"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=env,
        )
    except OSError as error:
        return f"bash: 无法启动（工作目录 {cwd}）：{error}"
    # The command rides on stdin, not the command line: a script longer than
    # Windows' 32k argv limit, or one carrying newlines and quotes, then costs
    # nothing special. Delivered exactly once and closed, so `bash -s` knows
    # the script has ended.
    assert process.stdin is not None
    process.stdin.write(command.encode("utf-8"))
    process.stdin.close()
    deadline = time.monotonic() + BASH_TIMEOUT_SECONDS
    while True:
        if should_cancel is not None and should_cancel():
            process.kill()
            process.communicate()
            return BASH_STOPPED_TEXT
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.communicate()
            # Only this error travels the thread boundary as a signal; the
            # caller answers with the timeout text, like it always has.
            raise TimeoutError
        try:
            stdout, _ = process.communicate(timeout=min(CANCEL_POLL_SECONDS, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    output = stdout.decode("utf-8", errors="replace").replace("\r\n", "\n") if stdout else ""
    output = output.rstrip()
    if len(output) > BASH_OUTPUT_MAX_CHARS:
        output = (
            output[:BASH_OUTPUT_MAX_CHARS]
            + f"\n…（输出超过 {BASH_OUTPUT_MAX_CHARS} 字符，已截断。）"
        )
    if process.returncode not in (0, None):
        output += f"\n（退出码 {process.returncode}）"
    return output or "bash: 命令执行完成，没有输出。"


BASH_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "在这台 Windows 电脑上用 Git Bash 执行一条 shell 命令。真实执行，不是模拟："
                "可以读写文件、运行脚本、安装依赖，改动无法撤销。命令可用管道、重定向和 &&；"
                "Windows 路径建议写成正斜杠（C:/Users/...）。执行前需要用户批准或留有缓冲期，"
                "输出最多返回约 2 万字符，超过 120 秒会被强制结束。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "要执行的完整命令行，例如 python script.py 或 ls -la | head -20。"
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
]


class BashToolService:
    """Runs one `bash` command: find the shell, start it, bound it.

    Stateless like the knowledge and skill tools - the working directory is
    the caller's decision, read once per run from the settings KV, so a
    settings change waits for the next answer exactly like every other tool
    setting. Every failure answers as text.
    """

    async def execute(
        self,
        command: str,
        *,
        cwd: Path,
        should_cancel: Callable[[], bool] | None = None,
    ) -> str:
        bash = find_bash()
        if bash is None:
            return "bash: 没有找到 Git Bash，请安装 Git for Windows 后重试。"
        bash_path = Path(bash)
        try:
            return await asyncio.to_thread(
                _run_sync,
                str(bash_path),
                command,
                cwd,
                _bash_env(_git_root(bash_path)),
                should_cancel,
            )
        except TimeoutError:
            return f"bash: 命令运行超过 {BASH_TIMEOUT_SECONDS} 秒，已强制结束。"
