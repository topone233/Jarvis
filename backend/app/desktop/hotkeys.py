"""全局快捷键的 OS 层：组合键解析与 RegisterHotKey 注册。

组合键字符串的语法与前端 ``frontend/src/plugins/hotkey.ts`` 的
``parseHotkey`` 对齐（字母/数字必须带修饰键，F1–F12 可单独使用，win 是
meta 的显示别名），金色用例与 ``hotkey.test.ts`` 同构——两边的语义是同一
条契约，测试把它们钉死。

``HotkeyManager`` 负责真正的系统注册：``RegisterHotKey`` 的 hotkey id 是
线程作用域的（hwnd 传 NULL 时 WM_HOTKEY 投递到注册线程的消息队列），所以
全部注册/注销都必须在消息循环线程上执行；其他线程通过命令队列 +
``PostThreadMessage`` 唤醒它干活。本模块不 import webview——组合键解析是
纯逻辑，dev 环境（不装 desktop 依赖组）也要能跑测试。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import re
import threading
import traceback
from collections.abc import Callable

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_QUIT = 0x0012

VK_F1 = 0x70

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "meta": MOD_WIN,
    "win": MOD_WIN,
}

_FUNCTION_KEY = re.compile(r"^F([1-9]|1[0-2])$")
_MAIN_KEY = re.compile(r"^[a-z0-9]$")


def parse_combo(text: str) -> tuple[int, int] | None:
    """``"Alt+N"`` → ``(modifiers, virtual key)``；语法不合法给 ``None``。

    与前端 ``parseHotkey`` 相同的拒绝规则：裸字母/数字、纯修饰键、两个
    主键、看不懂的键名都不要。
    """
    modifiers = 0
    key = ""
    for part in (chunk.strip().lower() for chunk in text.split("+")):
        if part == "":
            continue
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
            continue
        if key:
            return None  # 两个主键说不通
        upper = part.upper()
        if _FUNCTION_KEY.match(upper):
            key = upper
            continue
        if not _MAIN_KEY.match(part):
            return None
        key = upper
    if key == "":
        return None
    if _FUNCTION_KEY.match(key) is None and modifiers == 0:
        return None  # 没有修饰键的字母会在打字时不断触发
    vk = VK_F1 + int(key[1:]) - 1 if _FUNCTION_KEY.match(key) else ord(key)
    return modifiers, vk


class HotkeyManager:
    """一组 ``插件 id → 组合键`` 的系统注册与 WM_HOTKEY 分发。

    全部 Win32 调用都发生在消息循环线程上；``rebind``/``stop`` 从别的线程
    调用，命令进队列后被 ``PostThreadMessage`` 唤醒的循环取走执行。注册失
    败（组合键被其他程序占用、或与已注册插件重复）不会抛异常——返回值里
    的说明字符串就是给用户看的报告。
    """

    def __init__(self, on_activate: Callable[[str], None]) -> None:
        self._on_activate = on_activate
        self._commands: queue.Queue[Callable[[], None]] = queue.Queue()
        # plugin_id → (hotkey_id, modifiers, vk)；hotkey_id → plugin_id。
        # 只有循环线程读写它们。
        self._registered: dict[str, tuple[int, int, int]] = {}
        self._owner_of: dict[int, str] = {}
        self._next_id = 1
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._running = False

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="jarvis-hotkeys", daemon=True)
        self._thread.start()

    def rebind(self, bindings: dict[str, str]) -> list[str]:
        """换成新的一组注册。返回失败说明（含冲突），空列表即全部成功。"""
        if self._thread is None or not self._thread.is_alive():
            return ["快捷键线程没有在运行。"]
        done = threading.Event()
        failures: list[str] = []

        def job() -> None:
            failures.extend(self._rebind_now(bindings))
            done.set()

        self._submit(job)
        done.wait(timeout=5.0)
        return failures

    def stop(self) -> None:
        self._running = False
        if self._thread is None or not self._thread.is_alive():
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=2.0)

    # --- 消息循环线程上执行的部分 -----------------------------------------

    def _loop(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        message = ctypes.wintypes.MSG()
        while self._running:
            self._drain()
            # 返回 0 是 WM_QUIT，-1 是错误；两者都意味着收工。
            if ctypes.windll.user32.GetMessageW(ctypes.byref(message), None, 0, 0) <= 0:
                break
            if message.message == WM_HOTKEY:
                plugin_id = self._owner_of.get(message.wParam)
                if plugin_id is not None:
                    try:
                        self._on_activate(plugin_id)
                    except Exception:  # noqa: BLE001 - 回调属于壳，崩不得线程
                        traceback.print_exc()
            # WM_APP 只是"队列里有活"的敲门声，回到循环头去取。
        self._unregister_all_now()

    def _drain(self) -> None:
        while True:
            try:
                job = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                job()
            except Exception:  # noqa: BLE001 - 一条命令失败不能带走循环线程
                traceback.print_exc()

    def _submit(self, job: Callable[[], None]) -> None:
        self._commands.put(job)
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0)

    def _rebind_now(self, bindings: dict[str, str]) -> list[str]:
        self._unregister_all_now()
        failures: list[str] = []
        taken: dict[tuple[int, int], str] = {}
        for plugin_id, combo in bindings.items():
            parsed = parse_combo(combo)
            if parsed is None:
                failures.append(f"插件 {plugin_id} 的组合键 “{combo}” 无法识别。")
                continue
            modifiers, vk = parsed
            if (modifiers, vk) in taken:
                failures.append(
                    f"插件 {plugin_id} 的组合键与 {taken[(modifiers, vk)]} 重复，未注册。"
                )
                continue
            hotkey_id = self._next_id
            self._next_id += 1
            if not ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                failures.append(f"插件 {plugin_id} 的组合键 “{combo}” 已被其他程序占用。")
                continue
            self._registered[plugin_id] = (hotkey_id, modifiers, vk)
            self._owner_of[hotkey_id] = plugin_id
            taken[(modifiers, vk)] = plugin_id
        return failures

    def _unregister_all_now(self) -> None:
        for hotkey_id, _, _ in self._registered.values():
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()
        self._owner_of.clear()
