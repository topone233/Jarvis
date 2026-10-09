"""弹窗窗口的持久状态：位置与「固定」（置顶）。

桌面壳给每个插件的呼出弹窗记一条：上次拖到哪、是否固定。窗口几何是机器
级的 UI 状态，不随数据目录走，所以文件放在 bootstrap 目录
（``shell_windows.json``），与 bootstrap.json 同层，读写套路也照搬
``app/config.py`` 的 BootstrapStore：读改写、临时文件 + replace 原子落盘。
文件缺失或坏 JSON 都按「没有记录」处理——给一个从未保存过的弹窗回默认
值，不值得当成错误。

位置是 pywebview 的逻辑像素：``events.moved`` 给出的坐标除过 DPI 缩放，
``create_window`` 的 x/y 再乘回来，同一坐标系进出，往返不漂。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PopupWindowState:
    """一个插件弹窗的持久状态。x/y 是逻辑像素；没有记录时给默认值。"""

    x: int | None = None
    y: int | None = None
    pinned: bool = True


class WindowStateStore:
    """shell_windows.json 的读改写。

    写入方不止一个：拖动的防抖 Timer、js_api 线程（图钉）、销毁前的
    flush，全靠这把锁串行化。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def popup(self, plugin_id: str) -> PopupWindowState:
        with self._lock:
            entries = self._read()
        entry = entries.get(plugin_id)
        if not isinstance(entry, dict):
            return PopupWindowState()
        x = entry.get("x")
        y = entry.get("y")
        pinned = entry.get("pinned")
        # `isinstance(True, int)` 成立，所以 x/y 的 bool 守卫不能省。
        return PopupWindowState(
            x=x if isinstance(x, int) and not isinstance(x, bool) else None,
            y=y if isinstance(y, int) and not isinstance(y, bool) else None,
            pinned=pinned if isinstance(pinned, bool) else True,
        )

    def update_popup(
        self,
        plugin_id: str,
        *,
        x: int | None = None,
        y: int | None = None,
        pinned: bool | None = None,
    ) -> None:
        """只更新给出的字段，其余保持原样；没有记录则从默认值起。"""
        with self._lock:
            entries = self._read()
            entry = dict(entries.get(plugin_id) or {})
            if x is not None:
                entry["x"] = x
            if y is not None:
                entry["y"] = y
            if pinned is not None:
                entry["pinned"] = pinned
            entries[plugin_id] = entry
            self._write(entries)

    def _read(self) -> dict[str, dict]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        popups = payload.get("popups") if isinstance(payload, dict) else None
        return popups if isinstance(popups, dict) else {}

    def _write(self, entries: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"popups": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)
