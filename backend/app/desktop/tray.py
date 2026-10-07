"""托盘图标：主窗口收进后台后的常驻入口，也是真正的退出出口。

pystray 的 ``run_detached`` 自带线程消息循环，与 pywebview 的 GUI 主线程
并存；pywebview 的窗口方法内部自行跨线程封送，托盘线程直接调用是安全的。
本模块不 import Shell——回调由调用方注入，依赖单向；初始化失败（比如
explorer 尚未就绪）返回 None，调用方降级。
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from app.desktop import autostart

#: 应用主色 #4176e6，托盘里不用文字也有品牌感。
_ACCENT = (65, 118, 230, 255)
_WHITE = (255, 255, 255, 255)


def _draw_icon() -> Image.Image:
    """小机器人头像：果冻圆角方块 + 两个竖椭圆眼睛 + 一根天线。

    256px 超采样绘制 → LANCZOS 缩到 64px。曾在亮/暗两种背景下对比过三个
    变体（无天线 / 有天线 / 有天线加微笑）：微笑弧 16px 下糊成噪点，
    无天线又少了机器人的识别度——按 16px 实拍效果定稿这版。
    """
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 天线先画：杆的下端压进头部，接缝干净。
    draw.line((128, 68, 128, 36), fill=_ACCENT, width=11)
    draw.ellipse((115, 17, 141, 43), fill=_ACCENT)
    draw.rounded_rectangle((28, 64, 228, 232), radius=78, fill=_ACCENT)
    for cx in (88, 168):  # 两只竖椭圆眼睛，位于中线上方
        draw.ellipse((cx - 17, 116, cx + 17, 164), fill=_WHITE)
    return image.resize((64, 64), Image.LANCZOS)


class Tray:
    """托盘图标与菜单。用 :func:`start` 工厂创建，失败返回 None。"""

    def __init__(self, show_main: Callable[[], None], exit_app: Callable[[], None]) -> None:
        self._show_main = show_main
        self._exit_app = exit_app
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._on_show, default=True),
            pystray.MenuItem(
                "开机自启动",
                self._on_toggle_autostart,
                # 每次打开菜单实时求值，注册表是唯一事实来源。
                checked=lambda _item: autostart.is_on(),
            ),
            pystray.MenuItem("退出", self._on_exit),
        )
        self._icon = pystray.Icon("Jarvis", _draw_icon(), "Jarvis", menu)

    def run(self) -> None:
        self._icon.run_detached()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:
            pass  # 退出路上托盘摘不掉就算了，进程结束系统会回收

    def _on_show(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._show_main()

    def _on_toggle_autostart(self, icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        if autostart.is_on():
            autostart.disable()
        else:
            autostart.enable()
        icon.update_menu()  # 立即刷新勾选态，不等下次打开菜单

    def _on_exit(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        self._exit_app()


def start(
    show_main: Callable[[], None], exit_app: Callable[[], None]
) -> Tray | None:
    """构造并启动托盘。失败只打日志，返回 None 由调用方降级。"""
    try:
        tray = Tray(show_main, exit_app)
        tray.run()
        return tray
    except Exception as error:
        print(f"[desktop] 托盘初始化失败：{error}", flush=True)
        return None
