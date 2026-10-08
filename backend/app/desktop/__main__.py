"""桌面壳：``uv run python -m app.desktop``（见 ``scripts/start-desktop.ps1``）。

一个进程三条线：pywebview 的 GUI 主线程（主窗口 + 各插件的呼出弹窗）、
uvicorn 服务线程（复用已跑的后端，否则自己起一个）、RegisterHotKey 消息
循环线程（全局热键）。托盘图标（``tray.py``）再带一条自己的消息循环线程，
常驻后台。

主窗口无边框（frameless）：原生标题栏连着 python 图标和应用名一起去掉，
页内也没有标题条——窗口控制是前端 ``frontend/src/components/WindowControls.tsx``
的右上角悬浮三键，拖拽区挂在页面空白容器上（``pywebview-drag-region``，
只认直接目标）。窗口控制走 ``DesktopBridge`` 的三个 ``*_main`` 方法（方法名
是两端各写一半的契约）。无边框摘掉了 WS_THICKFRAME，窗口本来不能拖边缩放，
``shown`` 时把尺寸样式位补回去。□/❐ 状态经 ``jarvis-shell-maximized``
事件广播（前端 shell.ts 的 SHELL_MAXIMIZED_EVENT，字符串同样两端各写一半）。

关闭不退出：标题条的 ✕ 是「隐藏到后台」（``hide_main``）；Alt+F4 这类
系统级关闭被 ``closing`` 事件取消并同样收进托盘，热键和托盘随时唤回。
真退出只有两个入口——托盘「退出」和 Windows 会话结束（SessionEnding，
不拦它，否则系统弹"应用阻止关机"）。开机自启动默认开启：注册表 Run 键
的读写在 ``autostart.py``，托盘菜单可勾选关闭，``--hidden``（自启动）
时窗口直接隐藏进托盘。

唤出走 ``on_activate``：``quick_capture`` 插件切换无边框置顶弹窗窗口
（``/popup/<id>`` 页面只渲染插件的 quickCapture 组件）；其余插件唤出主
窗口并跳到 ``summon_path``。弹窗不占任务栏，被壳外关闭（任务栏、Alt+F4）
时由 ``closed`` 事件清账，下次唤出重建新窗。Python 只发 pluginId——组合
键到合成 keydown 的翻译在前端 ``frontend/src/plugins/shell.ts``，热键语
义仍然只有 hotkey.ts 一处。
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import httpx
import uvicorn
import webview

from app.desktop import autostart, tray
from app.desktop.hotkeys import HotkeyManager

BACKEND_PORT = 8787

#: pywebview 的拖拽区默认"任一祖先命中即拖"（DRAG_REGION_DIRECT_TARGET_ONLY
#: 默认 False），点正文任何地方都会拖走窗口、选中全废。本应用的拖拽区挂在
#: 页面空白容器上，两端注释里的契约都是"只认直接目标"
#: （WindowControls.tsx、下方主窗口注释），这里把默认掰回来。
webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True

#: 无边框窗体被 ``FormBorderStyle.None`` 摘掉的样式位。补回它们，边缘拖拽
#: 缩放、Aero Snap、Alt+F4 和任务栏最小化都回来，而标题栏（图标 + 应用名）
#: 依然不存在。数值是 Win32 的标准值，与 pywebview 无关。
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
#: 扩展样式：工具窗不进任务栏也不进 Alt-Tab，快搜框的定位正合适。
WS_EX_TOOLWINDOW = 0x00000080
GWL_STYLE = -16
GWL_EXSTYLE = -20
#: SetWindowPos 的组合：不挪不缩不改层级，只让新样式立刻生效。
SWP_FRAMECHANGED = 0x0001 | 0x0002 | 0x0004 | 0x0020


def _restore_sizing_styles(window: webview.Window) -> None:
    """给无边框窗口补回尺寸样式（必须在窗口就绪后、GUI 线程里调）。

    失败只意味着这个窗口退化为固定尺寸——不动启动流程，也不弹任何东西。
    """
    try:
        hwnd = int(window.native.Handle.ToInt64())  # type: ignore[attr-defined]
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(
            hwnd,
            GWL_STYLE,
            style | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX,
        )
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED)
    except Exception as error:
        print(f"[desktop] 补回窗口缩放样式失败（窗口将是固定尺寸）：{error}", flush=True)


def _strip_taskbar_icon(window: webview.Window) -> None:
    """给弹窗补上 WS_EX_TOOLWINDOW：不进任务栏、不进 Alt-Tab（GUI 线程调用）。

    与 ``_restore_sizing_styles`` 同一套位操作——SetWindowLongW 原地改扩展
    样式、不重建句柄，对挂在窗体上的 WebView2 无扰动。绝不能用
    ``ShowInTaskbar`` 属性代替：那个 setter 走 RecreateHandle，而且从
    loaded 的 worker 线程设它会 Invoke 回 GUI，与 create_window 的同步等待
    互相卡死，整条唤出链锁死（2026-10-07 死锁实测）。失败只意味着这条目
    还在，不动启动流程。
    """
    try:
        hwnd = int(window.native.Handle.ToInt64())  # type: ignore[attr-defined]
        user32 = ctypes.windll.user32
        exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_TOOLWINDOW)
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FRAMECHANGED)
    except Exception as error:
        print(f"[desktop] 摘除弹窗任务栏图标失败：{error}", flush=True)

#: 与 app/main.py 的 FRONTEND_DIST 同一个目录；本文件深两级，所以多一层。
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

#: 弹窗窗口尺寸 = 快速呼出卡片本身：宽装下 quick-note-box 的 520px；高装下
#: 页签、标题、正文拉满 320px 的空间和底部操作行。弹窗里卡片撑满窗口、没有
#: 遮罩和留白（notepad.css 的 .popup-holder 分支），窗口边就是卡片边。
POPUP_WIDTH = 520
POPUP_HEIGHT = 500


@dataclass
class SummonInfo:
    """一个可唤出插件的形状，来自 /api/plugins 的 MANIFEST 透传字段。"""

    quick_capture: bool
    summon_path: str | None


def _base_url() -> str:
    return os.environ.get("JARVIS_SHELL_URL", f"http://127.0.0.1:{BACKEND_PORT}").rstrip("/")


class Backend:
    """后端的进程内化：能复用就不自起（dev 的 uvicorn 已在跑）。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.server: uvicorn.Server | None = None

    def alive(self) -> bool:
        try:
            return httpx.get(f"{self.base}/api/health", timeout=1.0).status_code == 200
        except httpx.HTTPError:
            return False

    def ensure(self) -> None:
        if self.alive():
            print(f"[desktop] 复用已在运行的后端 {self.base}", flush=True)
            return
        if os.environ.get("JARVIS_SHELL_URL"):
            print(f"[desktop] 指定的 {self.base} 无响应，窗口里会看到连接失败。", flush=True)
            return
        from app.main import app as fastapi_app

        config = uvicorn.Config(
            fastapi_app, host="127.0.0.1", port=BACKEND_PORT, log_level="warning"
        )
        self.server = uvicorn.Server(config)
        threading.Thread(target=self.server.run, name="jarvis-server", daemon=True).start()
        deadline = time.monotonic() + 15.0
        while not self.alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        if self.alive():
            print(f"[desktop] 后端已随壳启动 {self.base}", flush=True)
        else:
            print("[desktop] 后端 15 秒内没有就绪，请检查 8787 端口被谁占用。", flush=True)

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True


class DesktopBridge:
    """pywebview 的 js_api：页面反向调用 Python 的入口。

    方法名即 JS 侧 ``window.pywebview.api.<name>``，两端各写一半，别改名。
    """

    def __init__(self, shell: Shell) -> None:
        self._shell = shell

    def refresh_hotkeys(self) -> None:
        self._shell.refresh_hotkeys()

    def hide_popup(self, plugin_id: str) -> None:
        self._shell.hide_popup(plugin_id)

    # 窗口控制（frontend/src/components/WindowControls.tsx）的三个入口。

    def minimize_main(self) -> None:
        self._shell.minimize_main()

    def toggle_maximize_main(self) -> None:
        self._shell.toggle_maximize_main()

    def hide_main(self) -> None:
        self._shell.hide_main()


class Shell:
    """窗口与热键的编排：注册表来自 /api/plugins，动作落在窗口上。"""

    def __init__(self, base: str, backend: Backend) -> None:
        self.base = base
        self.backend = backend
        self.bridge = DesktopBridge(self)
        self.manager = HotkeyManager(self.on_activate)
        self.main_window: webview.Window | None = None
        self.popups: dict[str, webview.Window] = {}
        self.visible: set[str] = set()
        self.summons: dict[str, SummonInfo] = {}
        self.closing = False
        # 真退出在即（托盘退出 / 会话结束）：closing 事件据此放行关闭。
        self.exiting = False
        self.tray: tray.Tray | None = None
        # 主窗口是否处于最大化。来源是 pywebview 的 maximized/restored 事件
        # （WinForms 的 on_resize 触发，Win+方向键这类系统操作也会经过），
        # 所以标题条的 □/❐ 和系统状态不会走散。
        self.maximized = False

    def run(self, hidden: bool = False) -> None:
        self.backend.ensure()
        main = webview.create_window(
            "Jarvis",
            f"{self.base}/",
            js_api=self.bridge,
            width=1280,
            height=860,
            frameless=True,
            easy_drag=False,  # 整窗拖拽会吞掉正文的选择；拖拽区由标题条负责
            hidden=hidden,  # 自启动 --hidden：直接进托盘，不弹窗
        )
        assert main is not None  # create_window 的 None 分支是内部契约，实际不发生
        self.main_window = main
        main.events.closed += self._on_main_closed
        main.events.closing += self._on_main_closing
        main.events.shown += lambda: _restore_sizing_styles(main)
        main.events.maximized += lambda: self._sync_maximized(True)
        main.events.restored += lambda: self._sync_maximized(False)
        self._watch_session_ending()
        self.tray = tray.start(self.show_main, self.exit)
        if self.tray is None and hidden:
            main.show()  # 托盘没起来，藏着的主窗口就再也找不回了
        self.manager.start()
        self.refresh_hotkeys()
        autostart.ensure_on_start()
        webview.start()
        self.backend.stop()

    # --- 标题条窗口控制 ----------------------------------------------------

    def minimize_main(self) -> None:
        if self.main_window is not None:
            self.main_window.minimize()

    def toggle_maximize_main(self) -> None:
        window = self.main_window
        if window is None:
            return
        if self.maximized:
            window.restore()
        else:
            window.maximize()

    def hide_main(self) -> None:
        """✕ = 隐藏到后台：窗口与任务栏条目消失，托盘和热键继续活着。"""
        if self.main_window is not None:
            self.main_window.hide()

    def show_main(self) -> None:
        """托盘「显示主窗口」（左键单击同效）：唤回主窗口，不导航。"""
        self._summon_main(None)

    def exit(self) -> None:
        """真退出的唯一入口（托盘「退出」/ 会话结束共用）。

        先立 ``exiting`` 放行 closing 事件，再摘托盘、销毁主窗口——
        FormClosed 落到 ``_on_main_closed``，热键、后端、弹窗的收尾照旧。
        """
        if self.exiting:
            return
        self.exiting = True
        if self.tray is not None:
            self.tray.stop()
        if self.main_window is not None:
            self.main_window.destroy()

    def _on_main_closing(self) -> bool:
        """FormClosing：未退出时取消系统级关闭（Alt+F4、任务栏关闭），
        顺手把窗口收进托盘。返回 False 让 pywebview 置 ``args.Cancel``。

        handler 同步跑在 GUI 线程，所以直接调原生 ``Hide``，不走 pywebview
        的封送——这正是 WinForms「关闭收进托盘」的官方写法。前端 ✕ 已改走
        ``hide_main``，不经过这里。
        """
        if self.exiting:
            return True
        try:
            if self.main_window is not None:
                self.main_window.native.Hide()  # type: ignore[attr-defined]
        except Exception:
            pass
        return False

    def _watch_session_ending(self) -> None:
        """Windows 关机/注销时放行退出，避免被记成"应用阻止关机"。

        pywebview 的 winforms 后端已加载 pythonnet 与 SystemEvents，这里
        用同一机制。订阅失败不致命：残余风险是极端路径下关机弹一次提示。
        """
        try:
            # SystemEvents 随 pywebview 的 winforms 后端一起加载；在
            # webview.start() 之前 pythonnet 尚未就绪，所以借它的模块导入
            # （winforms.py 顶部就是同一条 import）。
            from webview.platforms.winforms import SystemEvents

            SystemEvents.SessionEnding += lambda sender, args: self.exit()
        except Exception as error:
            print(f"[desktop] 订阅会话结束事件失败：{error}", flush=True)

    def _sync_maximized(self, maximized: bool) -> None:
        """把最大化状态广播给标题条（□/❐ 图形跟随真实窗口状态）。"""
        self.maximized = maximized
        window = self.main_window
        if window is None:
            return
        self._evaluate(
            window,
            "window.dispatchEvent(new CustomEvent('jarvis-shell-maximized', {detail: "
            + ("true" if maximized else "false")
            + "}))",
        )

    # --- 热键注册 ---------------------------------------------------------

    def refresh_hotkeys(self) -> None:
        """重读插件列表并整组重注册。设置页改完配置会经 js_api 到这里。"""
        bindings = self._read_bindings()
        for failure in self.manager.rebind(bindings):
            self._notice(failure)
        # 停用/移除的插件顺手收掉它的弹窗窗口。
        for plugin_id in list(self.popups):
            if plugin_id not in bindings:
                self.destroy_popup(plugin_id)
        if bindings:
            listing = ", ".join(f"{plugin_id}={combo}" for plugin_id, combo in bindings.items())
            print(f"[desktop] 全局热键：{listing}", flush=True)

    def _read_bindings(self) -> dict[str, str]:
        """启用的插件里，带 hotkey 设置的都算可唤出（取第一个 hotkey 字段）。"""
        try:
            response = httpx.get(f"{self.base}/api/plugins", timeout=3.0)
            response.raise_for_status()
            plugins = response.json()
        except (httpx.HTTPError, ValueError) as error:
            print(f"[desktop] 读取插件列表失败：{error}", flush=True)
            return {}
        bindings: dict[str, str] = {}
        summons: dict[str, SummonInfo] = {}
        for plugin in plugins:
            if not plugin.get("enabled") or plugin.get("broken"):
                continue
            field = next(
                (
                    entry
                    for entry in plugin.get("settings_schema", [])
                    if entry.get("type") == "hotkey"
                ),
                None,
            )
            if field is None:
                continue
            value = plugin.get("config", {}).get(field["key"])
            if not isinstance(value, str) or value == "":
                continue
            plugin_id = str(plugin["id"])
            bindings[plugin_id] = value
            summons[plugin_id] = SummonInfo(
                quick_capture=bool(plugin.get("quick_capture", False)),
                summon_path=plugin.get("summon_path"),
            )
        self.summons = summons
        return bindings

    # --- 唤出动作 ---------------------------------------------------------

    def on_activate(self, plugin_id: str) -> None:
        """WM_HOTKEY 落地（热键线程）。窗口方法内部自行跨线程。"""
        info = self.summons.get(plugin_id)
        if info is None:
            return  # 注册表已刷新、这条是滞后按键
        try:
            if info.quick_capture:
                self._toggle_popup(plugin_id)
            else:
                self._summon_main(info.summon_path)
        except Exception:
            traceback.print_exc()

    def _toggle_popup(self, plugin_id: str) -> None:
        window = self.popups.get(plugin_id)
        if window is None:
            window = self._create_popup(plugin_id)
        if plugin_id in self.visible:
            # 先合成 Esc 让页内弹窗走它自己的关闭路径（状态归位、观察者收窗），
            # 再藏窗口——下次唤出一定是干净的打开。
            self._evaluate(
                window, "window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"
            )
            window.hide()
            self.visible.discard(plugin_id)
        else:
            window.show()
            self.visible.add(plugin_id)
            self._deliver_summon(window, plugin_id)

    def _deliver_summon(self, window: webview.Window, plugin_id: str) -> None:
        """把唤出可靠地送进插件：派发合成按键，直到插件内容真的渲染出来。

        弹窗窗口刚加载完就唤出必输一场竞速——quickCapture 的监听要等它自己
        的配置请求挂上。派发后盯着挂载点（``.popup-holder`` 的子节点，核心
        自己拥有的类），没出现就补发，有界重试后放弃。全程占着热键线程
        ~1.6 秒的上限，对一次按键来说无感。

        派发前先核对地址：弹窗页面可能被插件自己的页内跳转带离
        ``/popup/<id>``——渲染成整个主应用、挂载点消失，唤出从此全废
        （2026-10-08 事故）。跑偏就先拽回来，重载后的渲染竞速由下面的重试
        循环兜底，不需要额外等 loaded。
        """
        try:
            pathname = window.evaluate_js("location.pathname")
        except Exception:
            pathname = None  # 页面尚不可读也按跑偏算：重定向是无害的
        if pathname != f"/popup/{plugin_id}":
            print(
                f"[desktop] 弹窗 {plugin_id} 的页面跑偏到了 {pathname}，唤出前拽回。",
                flush=True,
            )
            self._evaluate(window, f"location.href = {json.dumps(f'/popup/{plugin_id}')}")
        for _ in range(6):
            self._evaluate(
                window,
                "window.__jarvisShell && window.__jarvisShell.summonPlugin("
                + json.dumps(plugin_id)
                + ")",
            )
            time.sleep(0.28)
            try:
                opened = window.evaluate_js("!!document.querySelector('.popup-holder > *')")
            except Exception:
                opened = None
            if opened:
                return
        print(f"[desktop] 插件 {plugin_id} 的弹窗内容迟迟没有渲染，唤出可能没有生效。", flush=True)

    def _create_popup(self, plugin_id: str) -> webview.Window:
        x = None
        y = None
        try:
            screen = webview.screens[0]
            x = screen.x + (screen.width - POPUP_WIDTH) // 2
            y = screen.y + int(screen.height * 0.18)
        except Exception:
            pass  # 拿不到屏幕信息就让 pywebview 自己居中
        window = webview.create_window(
            f"Jarvis · {plugin_id}",
            f"{self.base}/popup/{plugin_id}",
            js_api=self.bridge,
            width=POPUP_WIDTH,
            height=POPUP_HEIGHT,
            x=x,
            y=y,
            frameless=True,
            easy_drag=False,
            on_top=True,
            hidden=True,
            focus=False,
        )
        assert window is not None  # create_window 的 None 分支是内部契约，实际不发生
        self.popups[plugin_id] = window
        # 壳外关闭（任务栏右键、Alt+F4）也要清账：closed 一响就摘引用，下一
        # 次唤出重建新窗——引用留着的话，热键从此都在对已销毁的窗口操作，
        # 唤出永远无效。destroy_popup 主动销毁同样触发 closed，pop/discard
        # 幂等，两条路共用这一个清理。事件不带参数，回调闭包住 plugin_id。
        window.events.closed += lambda: self._on_popup_closed(plugin_id)
        # 首次显示时（GUI 线程上）摘任务栏图标：快搜框不占任务栏，也从源头
        # 断了「从任务栏关掉弹窗」这条路。必须在 shown 里做——loaded 在
        # worker 线程上派发，任何会 Invoke 回 GUI 的属性调用都会和
        # create_window 的同步等待互相卡死（2026-10-07 实测）。
        window.events.shown += lambda: _strip_taskbar_icon(window)
        if not window.events.loaded.wait(10.0):
            print(f"[desktop] 弹窗页面 {plugin_id} 加载超时。", flush=True)
        return window

    def _on_popup_closed(self, plugin_id: str) -> None:
        """弹窗被壳外关闭：摘掉引用与可见标记，下一次唤出重建新窗。"""
        self.popups.pop(plugin_id, None)
        self.visible.discard(plugin_id)

    def hide_popup(self, plugin_id: str) -> None:
        """弹窗内容没了（quickCapture 关闭时返回 null），页面观察者来收窗。"""
        window = self.popups.get(plugin_id)
        if window is None:
            return
        try:
            window.hide()
        except Exception:
            pass
        self.visible.discard(plugin_id)

    def destroy_popup(self, plugin_id: str) -> None:
        window = self.popups.pop(plugin_id, None)
        self.visible.discard(plugin_id)
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    def _summon_main(self, path: str | None) -> None:
        window = self.main_window
        if window is None:
            return
        window.restore()  # 最小化时恢复；普通状态是无害的空操作
        window.show()
        if path:
            self._evaluate(
                window,
                "window.__jarvisShell && window.__jarvisShell.navigate(" + json.dumps(path) + ")",
            )

    # --- 零碎 -------------------------------------------------------------

    def _evaluate(self, window: webview.Window, script: str) -> None:
        try:
            window.evaluate_js(script)
        except Exception as error:
            print(f"[desktop] evaluate_js 失败：{error}", flush=True)

    def _notice(self, message: str) -> None:
        """冲突之类的提示：控制台一份，主窗口的 toast 一份（若来得及）。"""
        print(f"[desktop] {message}", flush=True)
        window = self.main_window
        if window is None:
            return
        try:
            window.events.loaded.wait(3.0)
            window.evaluate_js(
                "window.dispatchEvent(new CustomEvent('jarvis-shell-notice', {detail: "
                + json.dumps(message)
                + "}))"
            )
        except Exception:
            pass

    def _on_main_closed(self) -> None:
        # 事件回调跑在 pywebview 的线程里；主窗口一关，壳的寿命就到头。
        if self.closing:
            return
        self.closing = True
        self.manager.stop()
        self.backend.stop()
        # 弹窗全部销毁后 start() 自然返回；万一收尾卡死，硬保险兜底。
        threading.Timer(3.0, os._exit, args=(0,)).start()
        for plugin_id in list(self.popups):
            self.destroy_popup(plugin_id)


def main(hidden: bool = False) -> None:
    base = _base_url()
    if not os.environ.get("JARVIS_SHELL_URL") and not FRONTEND_DIST.is_dir():
        print(
            "[desktop] 未找到 frontend/dist —— 先在 frontend/ 下执行 npm run build，"
            "或设 JARVIS_SHELL_URL 指向 vite 开发服务器（http://127.0.0.1:5173）。",
            flush=True,
        )
    backend = Backend(base)
    shell = Shell(base, backend)
    print(
        f"[desktop] Jarvis 桌面壳启动（{'隐藏进托盘' if hidden else '显示主窗口'}），"
        f"界面地址 {base}",
        flush=True,
    )
    shell.run(hidden)
    print("[desktop] Jarvis 桌面壳已退出。", flush=True)


if __name__ == "__main__":
    main()
