"""便签图片丢失的端到端探针（2026-10-07）。

流程：建一条临时便签 -> 无头 Edge 打开 /notes?note=<id> -> 等编辑器就绪 ->
向 .ProseMirror 合成一次带 PNG 文件的 paste -> 轮询 DOM 里的 image-block 节点
和落盘文件里的 markdown 引用 -> 报告丢失点 -> 清理（purge 探针便签 + 探针资产 +
杀掉自己启动的 Edge 进程树）。

只用我自己启动的进程与数据；真实便签只读不改。
"""

from __future__ import annotations

import asyncio
import base64
import datetime
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
DEBUG_PORT = 9333
# 标准 1x1 透明 PNG。
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PASTE_JS = """
(async () => {
  const bytes = Uint8Array.from(atob("%s"), (c) => c.charCodeAt(0));
  const file = new File([bytes], "probe.png", { type: "image/png" });
  const dt = new DataTransfer();
  dt.items.add(file);
  const el = document.querySelector(".ProseMirror");
  if (el === null) return "NO_EDITOR";
  el.focus();
  const evt = new ClipboardEvent("paste", {
    clipboardData: dt,
    bubbles: true,
    cancelable: true,
  });
  el.dispatchEvent(evt);
  return "dispatched";
})()
""" % PNG_B64

IMG_JS = """
(() => {
  const img = document.querySelector('.ProseMirror img[data-type="image-block"]');
  return img === null ? null : img.getAttribute("src");
})()
"""

TOAST_JS = "document.body.innerText.includes('上传失败')"


def http_json(method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


async def cdp_call(ws, method: str, params: dict | None = None, msg_id: int = 0) -> dict:
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await ws.recv())
        if message.get("id") == msg_id:
            return message


async def cdp_evaluate(ws, expression: str, msg_id: int) -> dict:
    result = await cdp_call(
        ws,
        "Runtime.evaluate",
        {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        },
        msg_id,
    )
    return result.get("result", {}).get("result", {}).get("value")


async def wait_for(ws, expression: str, timeout: float, msg_id_start: int) -> tuple[bool, object]:
    deadline = asyncio.get_event_loop().time() + timeout
    msg_id = msg_id_start
    while asyncio.get_event_loop().time() < deadline:
        value = await cdp_evaluate(ws, expression, msg_id)
        msg_id += 1
        if value:
            return True, value
        await asyncio.sleep(0.3)
    return False, None


async def main() -> None:
    edge = next((path for path in EDGE_CANDIDATES if Path(path).exists()), None)
    assert edge is not None, "未找到 Edge"

    # --- 建探针便签 --------------------------------------------------------
    probe_note = http_json("POST", "/api/plugins/notepad/notes", {"title": "__probe__", "content": "探针起点"})
    note_id = probe_note["id"]
    note_file = next((DATA_DIR / "notes").glob(f"*-{note_id.split('-')[0]}.md"))
    print(f"[probe] 探针便签 id={note_id} 文件={note_file.name}")

    edge_args = [
        edge,
        "--headless=new",
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={tempfile.mkdtemp(prefix='jarvis-probe-')}",
        "--no-first-run",
        "--window-size=1280,900",
        "about:blank",
    ]
    proc = subprocess.Popen(edge_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    findings: list[str] = []
    probe_asset: Path | None = None
    try:
        # --- 等 CDP 端口 ---------------------------------------------------
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
        page = next(t for t in targets if t["type"] == "page")
        print(f"[probe] CDP 就绪 {page['webSocketDebuggerUrl'][:60]}…")

        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=20 * 1024 * 1024) as ws:
            await cdp_call(ws, "Runtime.enable", {}, 900)
            await cdp_call(ws, "Page.enable", {}, 901)
            await cdp_call(
                ws, "Page.navigate", {"url": f"{BASE}/notes?note={note_id}"}, 902
            )
            # 深链直选探针便签；等编辑器实例出现。
            ok, _ = await wait_for(ws, "!!document.querySelector('.ProseMirror')", 20, 910)
            if not ok:
                findings.append("编辑器 20 秒内没有出现（页面加载/路由问题）。")
                return
            await asyncio.sleep(1.5)  # crepe create 是异步的，多等一拍

            # --- 合成粘贴 ---------------------------------------------------
            paste_result = await cdp_evaluate(ws, PASTE_JS, 920)
            print(f"[probe] paste 派发 -> {paste_result}")

            # --- 轮询 DOM 与异常 --------------------------------------------
            exceptions: list[str] = []
            deadline = asyncio.get_event_loop().time() + 8
            dom_src = None
            while asyncio.get_event_loop().time() < deadline:
                dom_src = await cdp_evaluate(ws, IMG_JS, 930)
                await asyncio.sleep(0.3)
                if dom_src:
                    break

            print(f"[probe] 编辑器 DOM 里的 image-block src = {dom_src}")
            findings.append(f"DOM 图片节点 src = {dom_src!r}")
            toast = await cdp_evaluate(ws, TOAST_JS, 931)
            findings.append(f"上传失败 toast 出现 = {toast}")

            # --- 等自动保存（1.5s 防抖 + PATCH），看落盘 ----------------------
            await asyncio.sleep(4)
            text = note_file.read_text(encoding="utf-8")
            has_ref = "![" in text
            print(f"[probe] 落盘 markdown 含图片引用 = {has_ref}")
            if has_ref:
                idx = text.index("![")
                print(f"[probe] 引用片段：{text[idx:idx+120]}")
            findings.append(f"落盘 markdown 含图片引用 = {has_ref}")

            # 若 DOM 有图而落盘无引用：再验证"后续编辑是否还能持久化"，
            # 以区分"markdownUpdated 从此停摆"与"只是图片被序列化丢掉"。
            if dom_src and not has_ref:
                probe_asset = next((DATA_DIR / "notes" / ".assets").iterdir(), None)
                marker = f"探针后续编辑{datetime.datetime.now().strftime('%H%M%S')}"
                await cdp_evaluate(
                    ws,
                    "document.querySelector('.ProseMirror').focus();"
                    "document.execCommand('insertText', false, '%s');" % marker,
                    932,
                )
                await asyncio.sleep(4)
                text2 = note_file.read_text(encoding="utf-8")
                marker_persisted = marker in text2
                findings.append(f"图片之后的后续编辑能落盘 = {marker_persisted}")
                print(f"[probe] 后续编辑落盘 = {marker_persisted}")
    finally:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        try:
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}")
            http_json("DELETE", f"/api/plugins/notepad/notes/{note_id}/purge")
        except Exception as error:
            print(f"[probe] 探针便签清理失败（请手动删 __probe__）：{error}")

    print("\n[probe] ===== 结论素材 =====")
    for line in findings:
        print(" -", line)


if __name__ == "__main__":
    asyncio.run(main())
