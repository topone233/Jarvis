"""便签图片丢失探针 v3：在任务列表项里粘贴图片（复刻用户便签的真实形态）。

v1（普通段落粘贴）与 v2（菜单插入）都全链路正常。用户的便签是满屏任务
列表，粘贴光标大概率在列表项里——本探针在列表项文本位置点出光标，再合成
图片粘贴，看插入/序列化是否在这个上下文里失效。
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import websockets

BASE = "http://127.0.0.1:8787"
DATA_DIR = Path(r"C:\Users\80954\Desktop\Jarvis")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
DEBUG_PORT = 9335

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

LIST_CONTENT = "- [ ] 第一条任务\n- [ ] 第二条任务\n- [ ] 第三条任务\n"


def http_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode()
        return json.loads(body) if body else {}


class Cdp:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.next_id = 1000
        self.exceptions: list[str] = []
        self.console_errors: list[str] = []

    async def call(self, method: str, params: dict | None = None) -> dict:
        msg_id = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == msg_id:
                return message
            self._absorb(message)

    def _absorb(self, message: dict) -> None:
        method = message.get("method", "")
        params = message.get("params", {})
        if method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails", {})
            text = detail.get("exception", {}).get("description") or detail.get("text", "")
            self.exceptions.append(text[:400])
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args", [])
            self.console_errors.append(
                " ".join(str(a.get("value", a.get("description", ""))) for a in args)[:400]
            )

    async def evaluate(self, expression: str, await_promise: bool = False):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": await_promise, "returnByValue": True},
        )
        inner = result.get("result", {}).get("result", {})
        if inner.get("subtype") == "error":
            self.console_errors.append(f"evaluate failed: {inner.get('description', '')[:200]}")
            return None
        return inner.get("value")

    async def wait_for(self, expression: str, timeout: float):
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            value = await self.evaluate(expression)
            if value:
                return value
            await asyncio.sleep(0.3)
        return None


# 在第一条列表项的文字上点一下（真实鼠标事件），把光标放进列表项。
CLICK_INTO_LIST_ITEM = """
(async () => {
  const pm = document.querySelector('.ProseMirror');
  if (pm === null) return 'NO_EDITOR';
  const li = pm.querySelector('li');
  if (li === null) return 'NO_LIST';
  const range = document.createRange();
  range.selectNodeContents(li);
  const rect = range.getBoundingClientRect();
  return { x: rect.left + 10, y: rect.top + rect.height / 2, text: li.textContent.slice(0, 20) };
})()
"""

PASTE_JS = """
(async () => {
  const bytes = Uint8Array.from(atob("%s"), (c) => c.charCodeAt(0));
  const file = new File([bytes], "probe.png", { type: "image/png" });
  const dt = new DataTransfer();
  dt.items.add(file);
  const el = document.querySelector(".ProseMirror");
  el.focus();
  const evt = new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true });
  el.dispatchEvent(evt);
  return "dispatched";
})()
""" % PNG_B64

IMG_STATE = """
(() => {
  const img = document.querySelector('.ProseMirror img[data-type="image-block"]');
  if (img === null) return { node: false };
  return { node: true, src: img.getAttribute('src') };
})()
"""


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"

    probe_note = http_json(
        "POST",
        "/api/plugins/notepad/notes",
        {"title": "__probe3__", "content": LIST_CONTENT},
    )
    note_id = probe_note["id"]
    note_file = next((DATA_DIR / "notes").glob(f"*-{note_id.split('-')[0]}.md"))
    print(f"[probe3] 探针便签 id={note_id} 文件={note_file.name}")

    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe3-')}",
            "--no-first-run",
            "--window-size=1280,900",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    findings: list[str] = []
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
            cdp = Cdp(ws)
            await cdp.call("Runtime.enable")
            await cdp.call("Page.enable")
            await cdp.call("Page.navigate", {"url": f"{BASE}/notes?note={note_id}"})
            if not await cdp.wait_for("!!document.querySelector('.ProseMirror')", 20):
                findings.append("编辑器没有出现")
                return
            await asyncio.sleep(1.5)

            # 光标点进第一条列表项。
            spot = await cdp.evaluate(CLICK_INTO_LIST_ITEM, await_promise=True)
            print(f"[probe3] 列表项坐标 {spot}")
            for t in ("mousePressed", "mouseReleased"):
                await cdp.call(
                    "Input.dispatchMouseEvent",
                    {"type": t, "x": spot["x"], "y": spot["y"], "button": "left", "clickCount": 1},
                )
            await asyncio.sleep(0.4)

            # 在列表项里粘贴图片。
            paste_result = await cdp.evaluate(PASTE_JS, await_promise=True)
            print(f"[probe3] paste -> {paste_result}")

            deadline = asyncio.get_event_loop().time() + 8
            state = {"node": False}
            while asyncio.get_event_loop().time() < deadline:
                state = await cdp.evaluate(IMG_STATE)
                if state.get("node"):
                    break
                await asyncio.sleep(0.3)
            print(f"[probe3] 列表项粘贴后 DOM = {state}")
            findings.append(f"列表项粘贴后 DOM 图片节点 = {state}")

            await asyncio.sleep(4)
            text = note_file.read_text(encoding="utf-8")
            idx = text.find("![")
            has = idx >= 0
            snippet = text[max(0, idx - 40) : idx + 120] if has else text[-200:]
            print(f"[probe3] 落盘含 ![ = {has} 上下文={snippet!r}")
            findings.append(f"落盘含图片引用 = {has}")
            findings.append(f"落盘上下文 = {snippet!r}")

            if cdp.exceptions:
                findings.append(f"页面未捕获异常 {len(cdp.exceptions)} 条：{cdp.exceptions[:3]}")
            if cdp.console_errors:
                findings.append(f"console.error {len(cdp.console_errors)} 条：{cdp.console_errors[:3]}")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}")
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}/purge")
            print("[probe3] 探针便签已清理")
        except Exception as error:
            print(f"[probe3] 清理失败（请手动删 __probe3__）：{error}")

    print("\n[probe3] ===== 结论素材 =====")
    for line in findings:
        print(" -", line)


if __name__ == "__main__":
    asyncio.run(main())
