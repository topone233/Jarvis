"""弹窗唤出链路探针（2026-10-07，一次性）。

无头 Edge 打开 /popup/notepad，等 shell 桥挂上后调用
window.__jarvisShell.summonPlugin('notepad') —— 与 Shell._deliver_summon
派发的脚本一致 —— 然后轮询 .popup-holder 是否出现子节点，报告唤出是否
真正生效。只用我自己启动的进程；结束杀掉自己的 Edge。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import websockets

BASE = "http://127.0.0.1:8787"
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
DEBUG_PORT = 9334

SUMMON_JS = "window.__jarvisShell && window.__jarvisShell.summonPlugin('notepad') !== undefined"
OPENED_JS = "!!document.querySelector('.popup-holder > *')"
SNIPPET_JS = "document.querySelector('.popup-holder').childElementCount + ':' + document.querySelector('.popup-holder').firstElementChild.className"


async def cdp_evaluate(ws, expression: str, msg_id: int):
    await ws.send(
        json.dumps(
            {
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
            }
        )
    )
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == msg_id:
            return message.get("result", {}).get("result", {}).get("value")


async def wait_for(ws, expression: str, timeout: float, msg_id_start: int):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    msg_id = msg_id_start
    while loop.time() < deadline:
        value = await cdp_evaluate(ws, expression, msg_id)
        msg_id += 1
        if value:
            return True, msg_id
        await asyncio.sleep(0.3)
    return False, msg_id


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"
    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-popup-probe-')}",
            "--no-first-run",
            "--window-size=520,500",
            f"{BASE}/popup/notepad",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = asyncio.get_event_loop().time() + 15
        targets = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=2) as response:
                    targets = json.loads(response.read().decode())
                break
            except Exception:
                await asyncio.sleep(0.3)
        assert targets is not None, "CDP 端口没有就绪"
        page = next(t for t in targets if t.get("type") == "page" and "popup" in (t.get("url") or ""))
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024) as ws:
            ok, msg_id = await wait_for(ws, "!!window.__jarvisShell", 15, 1)
            print(f"[probe] shell 桥挂载：{ok}")
            if not ok:
                return
            await cdp_evaluate(ws, SUMMON_JS, msg_id)
            msg_id += 1
            opened, msg_id = await wait_for(ws, OPENED_JS, 5, msg_id)
            print(f"[probe] 唤出后 .popup-holder 出现内容：{opened}")
            if opened:
                snippet = await cdp_evaluate(ws, SNIPPET_JS, msg_id)
                print(f"[probe] 内容：{snippet}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
