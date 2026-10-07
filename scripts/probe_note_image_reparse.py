"""便签图片丢失探针 v4：最小化重解析实验。

已知：保存成功（文件里有 ![1.00](url)），重开后编辑器里 img 数量为 0。
本探针在自己的临时便签里放入最小变体，重开后 dump 编辑器结构：
- 裸图片 markdown 是否能重解析为可见节点；
- <br /> 是否影响后续内容；
- 图片到底被解析成了什么（img？空的 image-block 占位？还是彻底没了）。
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
DEBUG_PORT = 9337
IMG_URL = "/api/plugins/notepad/assets/e9874ad3661d41b7b2bfc4d6c1298c6b.png"

CONTENT = (
    "段落A前文\n"
    "\n"
    f"![1.00]({IMG_URL})\n"
    "\n"
    "段落B后文\n"
    "\n"
    "<br />\n"
    "\n"
    "段落C\n"
    "\n"
    "<br />\n"
    "\n"
    "<br />\n"
    "\n"
    "段落D\n"
)

STATE_JS = """
(() => {
  const pm = document.querySelector('.ProseMirror');
  if (pm === null) return 'NO_EDITOR';
  return {
    imgs: Array.from(pm.querySelectorAll('img')).map((img) => ({
      dataType: img.getAttribute('data-type'),
      src: (img.getAttribute('src') || '').slice(0, 60),
    })),
    blocks: Array.from(pm.querySelectorAll('[data-type]')).map((e) => e.getAttribute('data-type')),
    children: Array.from(pm.children).map((e) => ({
      tag: e.tagName,
      text: e.textContent.slice(0, 24),
      html: e.innerHTML.slice(0, 90),
    })),
  };
})()
"""


def http_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"
    probe_note = http_json("POST", "/api/plugins/notepad/notes", {"title": "__probe4__", "content": CONTENT})
    note_id = probe_note["id"]
    print(f"[probe4] 探针便签 id={note_id}")

    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe4b-')}",
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
            msg_id = 10
            async def call(method, params=None):
                nonlocal msg_id
                msg_id += 1
                await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    message = json.loads(await ws.recv())
                    if message.get("id") == msg_id:
                        return message

            await call("Runtime.enable")
            await call("Page.enable")
            await call("Page.navigate", {"url": f"{BASE}/notes?note={note_id}"})
            deadline = asyncio.get_event_loop().time() + 20
            state = None
            while asyncio.get_event_loop().time() < deadline:
                result = await call("Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True})
                value = result.get("result", {}).get("result", {}).get("value")
                if isinstance(value, dict):
                    state = value
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(1.5)
            result = await call("Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True})
            state = result.get("result", {}).get("result", {}).get("value")
            print("重解析结果：", json.dumps(state, ensure_ascii=False, indent=1))
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}")
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}/purge")
            print("[probe4] 探针便签已清理")
        except Exception as error:
            print(f"[probe4] 清理失败：{error}")


if __name__ == "__main__":
    asyncio.run(main())
