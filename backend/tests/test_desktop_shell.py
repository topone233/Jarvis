"""Shell 弹窗生命周期里「壳外关闭」的账目测试，以及位置/固定的持久化。

弹窗被任务栏/Alt+F4 关掉后，popups 与 visible 必须清干净，下一次唤出要
重建新窗——引用留着的话，热键从此在对已销毁的窗口上操作，唤出永远无效
（2026-10-07 事故）。重建的新窗要恢复保存的位置与固定状态：拖动经 moved
事件防抖落盘（window_state.py 的 shell_windows.json），图钉经 js_api 切
换。pywebview 只在 desktop 依赖组里，服务器环境跳过；
``webview.create_window`` 换成替身，事件对象用最小实现。
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("webview")

import webview  # noqa: E402

from app.desktop.__main__ import POPUP_WIDTH, Shell, SummonInfo  # noqa: E402
from app.desktop.window_state import WindowStateStore  # noqa: E402


class FakeEvents:
    """pywebview 事件对象的最小替身：``+=`` 收集回调，``fire()`` 手动触发。

    真事件触发时不带参数（winforms 后端里 closed/loaded 都是 ``set()``）；
    moved 例外——pywebview 原样转发 .set() 的参数（逻辑像素 x, y），所以
    fire 也透传。
    """

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler) -> "FakeEvents":
        self.handlers.append(handler)
        return self

    def fire(self, *args) -> None:
        for handler in list(self.handlers):
            handler(*args)

    def wait(self, timeout: float = 0.0) -> bool:
        return True


class FakeWindow:
    """记录调用的窗口替身：``_create_popup``/``_toggle_popup`` 触碰到的面。"""

    def __init__(self) -> None:
        self.events = SimpleNamespace(
            closed=FakeEvents(), loaded=FakeEvents(), shown=FakeEvents(), moved=FakeEvents()
        )
        self.native = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 0x1234))
        self.shown = False
        self.scripts: list[str] = []
        self.create_kwargs: dict = {}
        self.on_top = None
        # _deliver_summon 派发前读的地址；默认就是对的路由。
        self.pathname = "/popup/notepad"

    def show(self) -> None:
        self.shown = True

    def hide(self) -> None:
        self.shown = False

    def evaluate_js(self, script: str) -> object:
        self.scripts.append(script)
        if "location.pathname" in script:
            return self.pathname
        # _deliver_summon 轮询挂载点：答「已渲染」让它一轮就返回。
        return "document.querySelector" in script


def make_shell(monkeypatch, tmp_path) -> tuple[Shell, list[FakeWindow]]:
    windows: list[FakeWindow] = []

    def fake_create_window(title, url, **kwargs):
        window = FakeWindow()
        window.create_kwargs = kwargs
        window.on_top = kwargs.get("on_top")  # 真窗口由 create_window 落这个状态
        windows.append(window)
        return window

    monkeypatch.setattr(webview, "create_window", fake_create_window)
    # Shell 只在 run() 里碰 backend；这里的用例都到不了那里。
    shell = Shell("http://test", SimpleNamespace())  # type: ignore[arg-type]
    # 状态文件指向临时目录，读写都碰不到真实的 bootstrap 目录。
    shell.window_state = WindowStateStore(tmp_path / "shell_windows.json")
    shell.summons["notepad"] = SummonInfo(quick_capture=True, summon_path=None)
    return shell, windows


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # _deliver_summon 每轮 0.28 秒的等待对替身窗口毫无意义；只换 __main__
    # 眼里的 time，不动全局 time 模块。
    monkeypatch.setattr("app.desktop.__main__.time", SimpleNamespace(sleep=lambda seconds: None))


class TestPopupLifecycle:
    def test_external_close_cleans_up_and_next_summon_recreates(
        self, monkeypatch, tmp_path
    ) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")
        assert shell.popups["notepad"] is windows[0]  # type: ignore[comparison-overlap]
        assert "notepad" in shell.visible

        # 任务栏关闭 = 窗口销毁，pywebview 随之触发 closed。
        windows[0].events.closed.fire()
        assert shell.popups == {}
        assert "notepad" not in shell.visible

        shell.on_activate("notepad")
        assert len(windows) == 2  # 重建了新窗，而不是对僵尸窗口操作
        assert windows[1].shown

    def test_destroy_popup_then_closed_event_is_idempotent(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")
        shell.destroy_popup("notepad")
        assert shell.popups == {}
        # destroy 触发的 closed 回来时账已经清过——不能再出错或复插引用。
        windows[0].events.closed.fire()
        assert shell.popups == {}

    def test_toggle_keeps_one_window_across_hide_and_summon(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")
        shell.on_activate("notepad")  # 第二次按键 = 收窗
        assert not windows[0].shown
        assert "notepad" not in shell.visible
        assert shell.popups["notepad"] is windows[0]  # type: ignore[comparison-overlap]
        # 收窗先派发合成 Esc，让页内弹窗走它自己的关闭路径。
        assert any("Escape" in script for script in windows[0].scripts)

        shell.on_activate("notepad")  # 第三次按键 = 唤回同一个窗
        assert windows[0].shown
        assert len(windows) == 1

    def test_shown_strips_taskbar_icon(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")
        style_calls: list[tuple[int, int]] = []
        fake_user32 = SimpleNamespace(
            GetWindowLongW=lambda hwnd, index: 0x00040000,
            SetWindowLongW=lambda hwnd, index, value: style_calls.append((index, value)),
            SetWindowPos=lambda *args: None,
        )
        # _strip_taskbar_icon 只经 __main__ 里的 ctypes 名字碰到 user32，
        # 整个名字换掉即可，不动真窗口。
        monkeypatch.setattr(
            "app.desktop.__main__.ctypes",
            SimpleNamespace(windll=SimpleNamespace(user32=fake_user32)),
        )

        windows[0].events.shown.fire()

        assert (-20, 0x00040000 | 0x00000080) in style_calls  # GWL_EXSTYLE += WS_EX_TOOLWINDOW


class TestSummonGuard:
    """唤出前的地址核对：跑偏的弹窗拽回，在轨的弹窗零动作。

    弹窗页面被插件自己的页内跳转带离 /popup/<id> 后渲染成整个主应用，
    挂载点消失、合成按键全打空——表现是热键按了闪几下、唤出永远无效
    （2026-10-08 事故）。核对在派发前做：跑偏先 location.href 拽回，页面
    重载的竞速由既有的重试循环兜底。
    """

    def test_off_route_popup_is_pulled_back_before_dispatch(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")
        windows[0].pathname = "/notes"  # 模拟弹窗被页内导航带跑

        shell._deliver_summon(windows[0], "notepad")  # type: ignore[arg-type]

        redirects = [script for script in windows[0].scripts if "location.href" in script]
        assert redirects == ['location.href = "/popup/notepad"']
        # 拽回之后照常派发，唤出链路不断。
        assert any("summonPlugin" in script for script in windows[0].scripts)

    def test_on_route_popup_is_left_alone(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.on_activate("notepad")

        assert not any("location.href" in script for script in windows[0].scripts)


class TestPopupGeometry:
    """位置与固定的持久化：创建时恢复、拖动后落盘、图钉切换。

    位置是 pywebview 的逻辑像素，``events.moved`` 与 ``create_window`` 的
    x/y 同一坐标系；恢复前的可视性校验挡住「显示器拔了」的恢复点位。
    """

    SCREENS = [SimpleNamespace(x=0, y=0, width=1920, height=1080)]

    def test_saved_position_and_pin_are_restored(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.window_state.update_popup("notepad", x=300, y=200, pinned=False)
        monkeypatch.setattr(webview, "screens", self.SCREENS)

        shell.on_activate("notepad")

        kwargs = windows[0].create_kwargs
        assert kwargs["x"] == 300
        assert kwargs["y"] == 200
        assert kwargs["on_top"] is False

    def test_no_state_uses_default_position_and_pinned(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        monkeypatch.setattr(webview, "screens", self.SCREENS)

        shell.on_activate("notepad")

        kwargs = windows[0].create_kwargs
        assert kwargs["x"] == (1920 - POPUP_WIDTH) // 2
        assert kwargs["y"] == int(1080 * 0.18)
        assert kwargs["on_top"] is True

    def test_off_screen_position_falls_back_to_default(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        shell.window_state.update_popup("notepad", x=50000, y=-50000)
        monkeypatch.setattr(webview, "screens", self.SCREENS)

        shell.on_activate("notepad")

        kwargs = windows[0].create_kwargs
        assert kwargs["x"] == (1920 - POPUP_WIDTH) // 2
        assert kwargs["y"] == int(1080 * 0.18)

    def test_moved_event_lands_in_state_file_after_flush(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        monkeypatch.setattr(webview, "screens", self.SCREENS)
        shell.on_activate("notepad")

        windows[0].events.moved.fire(123, 250)
        shell._flush_popup_save("notepad")

        state = shell.window_state.popup("notepad")
        assert state.x == 123
        assert state.y == 250

    def test_destroy_flushes_pending_position(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        monkeypatch.setattr(webview, "screens", self.SCREENS)
        shell.on_activate("notepad")
        windows[0].events.moved.fire(77, 88)

        shell.destroy_popup("notepad")  # 防抖还没到点，销毁前的 flush 兜底

        state = shell.window_state.popup("notepad")
        assert state.x == 77
        assert state.y == 88

    def test_set_popup_pin_updates_window_and_file(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        monkeypatch.setattr(webview, "screens", self.SCREENS)
        shell.on_activate("notepad")

        shell.set_popup_pin("notepad", False)

        assert windows[0].on_top is False
        assert shell.window_state.popup("notepad").pinned is False

    def test_pin_roundtrip_through_the_bridge(self, monkeypatch, tmp_path) -> None:
        shell, windows = make_shell(monkeypatch, tmp_path)
        monkeypatch.setattr(webview, "screens", self.SCREENS)

        assert shell.bridge.get_popup_pin("notepad") is True  # 无记录 = 固定开
        shell.on_activate("notepad")
        shell.bridge.set_popup_pin("notepad", False)

        assert shell.bridge.get_popup_pin("notepad") is False
        assert windows[0].on_top is False

    def test_pin_for_missing_popup_still_persists(self, monkeypatch, tmp_path) -> None:
        # 页面比窗口活得久的滞后调用：窗口没了，偏好照存，下次重建生效。
        shell, _ = make_shell(monkeypatch, tmp_path)

        shell.set_popup_pin("notepad", False)

        assert shell.window_state.popup("notepad").pinned is False
