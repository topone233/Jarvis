"""便签图片丢失探针 v2：复刻 block 菜单插入图片的完整用户路径。

v1 已证明"粘贴图片"全链路（上传->插入->序列化->自动保存->落盘）正常。
本探针走用户实际用的路径：hover 出 block 手柄 -> 点开菜单 -> 点图片 ->
插入空 image-block -> （等一次自动保存，看空块是否破坏 markdown 流）->
对组件的 <input type=file> 用 CDP 设文件 -> onUpload 上传 -> setAttr(src)
-> 看编辑器 DOM 与落盘文件。全程收集页面未捕获异常。
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
DEBUG_PORT = 9334

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
            self.exceptions.append(text[:300])
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args", [])
            self.console_errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in args)[:300])

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


MOUSE_AND_CLICK_HANDLE = """
(async () => {
  const pm = document.querySelector('.ProseMirror');
  if (pm === null) return 'NO_EDITOR';
  pm.focus();
  const first = pm.querySelector('p, h1, h2, li, div');
  const rect = first.getBoundingClientRect();
  return { x: rect.left + 30, y: rect.top + rect.height / 2 };
})()
"""

# 手柄里的 + 按钮：pointerdown + pointerup -> 插入新段落并打开插入菜单。
CLICK_ADD = """
(() => {
  const handle = document.querySelector('.milkdown-block-handle');
  if (handle === null) return 'NO_HANDLE';
  const add = handle.querySelector('.operation-item');
  if (add === null) return 'NO_ADD';
  const rect = add.getBoundingClientRect();
  const opts = { bubbles: true, cancelable: true, pointerId: 1, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 };
  add.dispatchEvent(new PointerEvent('pointerdown', opts));
  add.dispatchEvent(new PointerEvent('pointerup', opts));
  return 'add-clicked';
})()
"""

# slash 菜单里挑图片项：整行文本匹配（第一个 span 是图标包装），pointerup 触发。
CLICK_IMAGE_ITEM = """
(() => {
  const menu = document.querySelector('.milkdown-slash-menu');
  if (menu === null) return { menu: false };
  const items = Array.from(menu.querySelectorAll('.menu-group li'));
  const target = items.find((li) => /^(图片|Image)$/.test(li.textContent.trim()));
  if (target === undefined) return { menu: true, count: items.length };
  const rect = target.getBoundingClientRect();
  const opts = { bubbles: true, cancelable: true, pointerId: 1, clientX: rect.left + 5, clientY: rect.top + 5 };
  target.dispatchEvent(new PointerEvent('pointerdown', opts));
  target.dispatchEvent(new PointerEvent('pointerup', opts));
  return { menu: true, count: items.length, clicked: target.textContent.trim().slice(0, 20) };
})()
"""

IMG_STATE = """
(() => {
  const img = document.querySelector('.ProseMirror img[data-type="image-block"]');
  if (img === null) return { node: false };
  return { node: true, src: img.getAttribute('src'), w: img.getBoundingClientRect().width };
})()
"""


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"

    probe_note = http_json("POST", "/api/plugins/notepad/notes", {"title": "__probe2__", "content": "菜单路径探针"})
    note_id = probe_note["id"]
    note_file = next((DATA_DIR / "notes").glob(f"*-{note_id.split('-')[0]}.md"))
    print(f"[probe2] 探针便签 id={note_id} 文件={note_file.name}")

    # 给 CDP 设文件用的临时图片。
    tmp_png = Path(tempfile.gettempdir()) / "jarvis-probe-image.png"
    tmp_png.write_bytes(base64.b64decode(PNG_B64))

    proc = subprocess.Popen(
        [
            edge,
            "--headless=new",
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe2-')}",
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

            # 1. 鼠标移到首块，等手柄出现，点 + 打开插入菜单。
            info = await cdp.evaluate(MOUSE_AND_CLICK_HANDLE, await_promise=True)
            await cdp.call(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": info["x"], "y": info["y"]},
            )
            await asyncio.sleep(0.6)
            add_result = await cdp.evaluate(CLICK_ADD)
            print(f"[probe2] 点 + = {add_result}")
            findings.append(f"点 + = {add_result}")
            await asyncio.sleep(0.8)

            # 2. 点菜单里的图片项（onPointerdown）。
            item = await cdp.evaluate(CLICK_IMAGE_ITEM)
            print(f"[probe2] 菜单 dump + 点击 = {item}")
            findings.append(f"菜单项点击 = {item}")
            await asyncio.sleep(0.8)

            # 3. 空图片块出现？它的 markdown 流效应对？（等一次自动保存）
            state = await cdp.evaluate(IMG_STATE)
            print(f"[probe2] 空图片块 {state}")
            findings.append(f"插入后 DOM 图片节点 = {state}")
            await asyncio.sleep(3)
            text = note_file.read_text(encoding="utf-8")
            findings.append(f"空块阶段落盘含 ![ = {'![' in text}")
            print(f"[probe2] 空块阶段落盘含 ![ = {'![' in text} 片段={text[-120:]!r}")

            # 4. 对组件 file input 设文件（真实 change 事件 -> onUploadFile）。
            doc = await cdp.call("DOM.getDocument")
            root = doc["result"]["root"]["nodeId"]
            q = await cdp.call("DOM.querySelectorAll", {"nodeId": root, "selector": "input[type=file]"})
            node_ids = q["result"]["nodeIds"]
            print(f"[probe2] file input 数量 = {len(node_ids)}")
            findings.append(f"file input 数量 = {len(node_ids)}")
            if node_ids:
                await cdp.call(
                    "DOM.setFileInputFiles",
                    {"files": [str(tmp_png)], "nodeId": node_ids[-1]},
                )
                # 5. 轮询 src 变化。
                deadline = asyncio.get_event_loop().time() + 8
                while asyncio.get_event_loop().time() < deadline:
                    state = await cdp.evaluate(IMG_STATE)
                    if state.get("node") and str(state.get("src", "")).startswith("/api/"):
                        break
                    await asyncio.sleep(0.3)
                print(f"[probe2] 选文件后 {state}")
                findings.append(f"选文件后 DOM 图片节点 = {state}")
                await asyncio.sleep(4)
                text = note_file.read_text(encoding="utf-8")
                has = "![" in text
                idx = text.find("![")
                snippet = text[idx : idx + 120] if has else ""
                print(f"[probe2] 最终落盘含 ![ = {has} 片段={snippet!r}")
                findings.append(f"最终落盘含图片引用 = {has}")
                if has:
                    findings.append(f"落盘片段 = {snippet!r}")

            if cdp.exceptions:
                findings.append(f"页面未捕获异常 {len(cdp.exceptions)} 条：{cdp.exceptions[:3]}")
            if cdp.console_errors:
                findings.append(f"console.error {len(cdp.console_errors)} 条：{cdp.console_errors[:3]}")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}")
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}/purge")
            print("[probe2] 探针便签已清理")
        except Exception as error:
            print(f"[probe2] 清理失败（请手动删 __probe2__）：{error}")

    print("\n[probe2] ===== 结论素材 =====")
    for line in findings:
        print(" -", line)


if __name__ == "__main__":
    asyncio.run(main())
