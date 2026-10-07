"""便签图片重解析探针 v5：变体矩阵 + 控制台错误捕获。

v4 已复现：重解析时标准 ![1.00](url) 的整个段落消失。本探针在同一份
临时便签里放多个 URL 形态的图片变体 + 普通链接做对照，重开后 dump：
哪些变体活下来、哪些消失、console 有没有报错。
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
DEBUG_PORT = 9338

CONTENT_LINES = [
    "V1相对长URL：",
    "",
    "![1.00](/api/plugins/notepad/assets/e9874ad3661d41b7b2bfc4d6c1298c6b.png)",
    "",
    "V2绝对HTTP：",
    "",
    "![alt](https://example.com/x.png)",
    "",
    "V3短相对：",
    "",
    "![1.00](x.png)",
    "",
    "V4绝对本机：",
    "",
    "![1.00](http://127.0.0.1:8787/api/plugins/notepad/assets/e9874ad3661d41b7b2bfc4d6c1298c6b.png)",
    "",
    "V5链接对照：",
    "",
    "[一个普通链接](/api/plugins/notepad/assets/e9874ad3661d41b7b2bfc4d6c1298c6b.png)",
    "",
    "V6结束",
]
CONTENT = "\n".join(CONTENT_LINES)

STATE_JS = """
(() => {
  const pm = document.querySelector('.ProseMirror');
  if (pm === null) return 'NO_EDITOR';
  return {
    imgs: Array.from(pm.querySelectorAll('img')).map((img) => (img.getAttribute('src') || '').slice(0, 50)),
    links: Array.from(pm.querySelectorAll('a')).map((a) => (a.getAttribute('href') || '').slice(0, 50)),
    paras: Array.from(pm.children).map((e) => e.textContent.slice(0, 18)),
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
    probe_note = http_json("POST", "/api/plugins/notepad/notes", {"title": "__probe5__", "content": CONTENT})
    note_id = probe_note["id"]
    print(f"[probe5] 探针便签 id={note_id}")

    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe5-')}",
            "--no-first-run",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    errors: list[str] = []
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
                    m = message.get("method", "")
                    if m == "Runtime.exceptionThrown":
                        detail = message.get("params", {}).get("exceptionDetails", {})
                        errors.append((detail.get("exception", {}).get("description") or detail.get("text", ""))[:300])
                    elif m == "Runtime.consoleAPICalled" and message.get("params", {}).get("type") == "error":
                        args = message["params"].get("args", [])
                        errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in args)[:300])

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
            await asyncio.sleep(2)
            result = await call("Runtime.evaluate", {"expression": STATE_JS, "returnByValue": True})
            state = result.get("result", {}).get("result", {}).get("value")
            print("变体矩阵结果：", json.dumps(state, ensure_ascii=False, indent=1))
            if errors:
                print("console/exception:", json.dumps(errors[:6], ensure_ascii=False))
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}")
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}/purge")
            print("[probe5] 探针便签已清理")
        except Exception as error:
            print(f"[probe5] 清理失败：{error}")


if __name__ == "__main__":
    asyncio.run(main())
