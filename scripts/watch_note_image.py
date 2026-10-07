"""便签图片实时监听：每 0.5s 采样用户便签与 .assets，事件式记录变化。

用法：后台挂着，用户复现操作；结束后读日志判断图片是"从未落盘"还是
"落盘后被移除"。只读，不碰任何数据。
"""

from __future__ import annotations

import datetime
import re
import time
from pathlib import Path

NOTES_DIR = Path(r"C:\Users\80954\Desktop\Jarvis\notes")
TARGET_NOTE = NOTES_DIR / "document.txt-e3ffcca4.md"
ASSETS_DIR = NOTES_DIR / ".assets"
LOG = Path(r"C:\Users\80954\Desktop\Code\AI\Projects\Jarvis\scripts\image_watch.log")
DURATION_SECONDS = 60 * 30


def snapshot_note() -> tuple[int, int, list[str]]:
    if not TARGET_NOTE.exists():
        return (-1, -1, [])
    text = TARGET_NOTE.read_text(encoding="utf-8")
    mtime = int(TARGET_NOTE.stat().st_mtime)
    imgs = re.findall(r"!\[[^\]]*\]\(([^)]*)\)", text)
    return (mtime, len(text), imgs)


def snapshot_assets() -> dict[str, int]:
    if not ASSETS_DIR.exists():
        return {}
    return {p.name: p.stat().st_size for p in ASSETS_DIR.iterdir()}


def stamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def main() -> None:
    lines: list[str] = [f"{stamp()} 监听开始：{TARGET_NOTE.name} + .assets"]
    last_note = snapshot_note()
    last_assets = snapshot_assets()
    lines.append(f"{stamp()} 初始：note mtime={last_note[0]} len={last_note[1]} imgs={last_note[2]}")
    lines.append(f"{stamp()} 初始：assets={last_assets}")
    deadline = time.time() + DURATION_SECONDS
    while time.time() < deadline:
        time.sleep(0.5)
        note = snapshot_note()
        if note != last_note:
            lines.append(f"{stamp()} 便签变化：mtime {last_note[0]}->{note[0]} len {last_note[1]}->{note[1]} imgs={note[2]}")
            last_note = note
        assets = snapshot_assets()
        added = {k: v for k, v in assets.items() if k not in last_assets}
        removed = [k for k in last_assets if k not in assets]
        if added:
            lines.append(f"{stamp()} 资产新增：{added}")
        if removed:
            lines.append(f"{stamp()} 资产消失：{removed}")
        if added or removed:
            last_assets = assets
    lines.append(f"{stamp()} 监听结束")
    LOG.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
