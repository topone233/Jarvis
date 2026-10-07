"""Shell 弹窗生命周期里「壳外关闭」的账目测试。

弹窗被任务栏/Alt+F4 关掉后，popups 与 visible 必须清干净，下一次唤出要
重建新窗——引用留着的话，热键从此在对已销毁的窗口上操作，唤出永远无效
（2026-10-07 事故）。pywebview 只在 desktop 依赖组里，服务器环境跳过；
``webview.create_window`` 换成替身，事件对象用最小实现。
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("webview")

import webview  # noqa: E402

from app.desktop.__main__ import Shell, SummonInfo  # noqa: E402


class FakeEvents:
    """pywebview 事件对象的最小替身：``+=`` 收集回调，``fire()`` 手动触发。

    真事件触发时不带参数（winforms 后端里 closed/loaded 都是 ``set()``），
    这里保持一致。
    """

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler) -> "FakeEvents":
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in list(self.handlers):
            handler()

    def wait(self, timeout: float = 0.0) -> bool:
        return True


class FakeWindow:
    """记录调用的窗口替身：``_create_popup``/``_toggle_popup`` 触碰到的面。"""

    def __init__(self) -> None:
        self.events = SimpleNamespace(
            closed=FakeEvents(), loaded=FakeEvents(), shown=FakeEvents()
        )
        self.native = SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 0x1234))
        self.shown = False
        self.scripts: list[str] = []

    def show(self) -> None:
        self.shown = True

    def hide(self) -> None:
        self.shown = False

    def evaluate_js(self, script: str) -> object:
        self.scripts.append(script)
        # _deliver_summon 轮询挂载点：答「已渲染」让它一轮就返回。
        return "document.querySelector" in script


def make_shell(monkeypatch) -> tuple[Shell, list[FakeWindow]]:
    windows: list[FakeWindow] = []

    def fake_create_window(title, url, **kwargs):
        window = FakeWindow()
        windows.append(window)
        return window

    monkeypatch.setattr(webview, "create_window", fake_create_window)
    # Shell 只在 run() 里碰 backend；这里的用例都到不了那里。
    shell = Shell("http://test", SimpleNamespace())  # type: ignore[arg-type]
    shell.summons["notepad"] = SummonInfo(quick_capture=True, summon_path=None)
    return shell, windows


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # _deliver_summon 每轮 0.28 秒的等待对替身窗口毫无意义；只换 __main__
    # 眼里的 time，不动全局 time 模块。
    monkeypatch.setattr("app.desktop.__main__.time", SimpleNamespace(sleep=lambda seconds: None))


class TestPopupLifecycle:
    def test_external_close_cleans_up_and_next_summon_recreates(self, monkeypatch) -> None:
        shell, windows = make_shell(monkeypatch)
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

    def test_destroy_popup_then_closed_event_is_idempotent(self, monkeypatch) -> None:
        shell, windows = make_shell(monkeypatch)
        shell.on_activate("notepad")
        shell.destroy_popup("notepad")
        assert shell.popups == {}
        # destroy 触发的 closed 回来时账已经清过——不能再出错或复插引用。
        windows[0].events.closed.fire()
        assert shell.popups == {}

    def test_toggle_keeps_one_window_across_hide_and_summon(self, monkeypatch) -> None:
        shell, windows = make_shell(monkeypatch)
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

    def test_shown_strips_taskbar_icon(self, monkeypatch) -> None:
        shell, windows = make_shell(monkeypatch)
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
