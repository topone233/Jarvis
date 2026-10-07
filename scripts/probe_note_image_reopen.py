"""只读探针：打开真实便签（含已落盘的图片引用），检查重开渲染。

不写任何数据：导航到 /notes?note=<AI 便签>，等编辑器出现，检查
- 文档里有没有 img、它的 data-type 与 src；
- 图片是否真的加载（naturalWidth）；
- 是否可见（boundingRect）。
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
DEBUG_PORT = 9336
NOTE_ID = "69e557c7-5116-4e7b-bc81-ca9ecdf96df1"  # 用户的 AI 便签（只读）

STATE_JS = """
(() => {
  const pm = document.querySelector('.ProseMirror');
  if (pm === null) return 'NO_EDITOR';
  const imgs = Array.from(pm.querySelectorAll('img'));
  const children = Array.from(pm.children);
  return {
    imgs: imgs.map((img) => ({
      dataType: img.getAttribute('data-type'),
      src: (img.getAttribute('src') || '').slice(0, 80),
      naturalWidth: img.naturalWidth,
    })),
    lastChildren: children.slice(-4).map((e) => ({
      tag: e.tagName,
      cls: String(e.className).slice(0, 60),
      html: e.innerHTML.slice(0, 220),
    })),
  };
})()
"""


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"
    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe4-')}",
            "--no-first-run",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = asyncio.get_event_loop().time() + 15
        page = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=2) as response:
                    targets = json.loads(response.read().decode())
                page = next((t for t in targets if t["type"] == "page"), None)
                if page is not None:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        assert page is not None, "CDP 没有就绪"

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
            async def call(method, params=None, msg_id=1):
                await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") == msg_id:
                        return message

            await call("Runtime.enable")
            await call("Page.enable")
            await call("Page.navigate", {"url": f"{BASE}/notes?note={NOTE_ID}"}, 10)

            deadline = asyncio.get_event_loop().time() + 20
            state = None
            while asyncio.get_event_loop().time() < deadline:
                result = await call(
                    "Runtime.evaluate",
                    {"expression": STATE_JS, "returnByValue": True},
                    20,
                )
                value = result.get("result", {}).get("result", {}).get("value")
                if isinstance(value, dict):
                    state = value
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(2)  # 再等图片加载
            result = await call(
                "Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True}, 21
            )
            state = result.get("result", {}).get("result", {}).get("value")
            print("重开渲染状态：", json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    asyncio.run(main())
