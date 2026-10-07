"""开机自启动：注册表 Run 键的读写（HKCU，不需要管理员权限）。

自启动 = 当前用户的 Run 键里有一条指向 ``autostart_launch.pyw`` 的命令：
venv 的 pythonw 静默执行（无控制台窗口），带 ``--hidden``——登录后直接进
托盘，不弹窗。

开关的"事实来源"就是这条键本身：在 → 开。默认开启（用户拍板）：壳每次
启动调 ``ensure_on_start``，没有拒绝标记就重写键值——重写而不只是补写，
项目搬家后路径自动修复。托盘取消勾选走 ``disable``，写入拒绝标记，之后
启动不再自动开启。
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

#: 当前用户的登录自启动键。
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "Jarvis"

#: "用户明确拒绝过自启动" 的标记。在这，``ensure_on_start`` 就不再写键。
DECLINED_KEY_PATH = r"Software\Jarvis"
DECLINED_VALUE_NAME = "AutostartDisabled"


def autostart_command() -> str:
    """Run 键的命令串。路径全部运行时推导：venv 的 pythonw + 本目录的启动器。"""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parent / "autostart_launch.pyw"
    return f'"{pythonw}" "{launcher}" --hidden'


def is_on() -> bool:
    """Run 键里有没有这条自启动。读不到按“关”处理。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, RUN_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        print(f"[desktop] 读取自启动状态失败：{error}", flush=True)
        return False


def _declined() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DECLINED_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, DECLINED_VALUE_NAME)
        return bool(value)
    except FileNotFoundError:
        return False
    except OSError as error:
        print(f"[desktop] 读取自启动拒绝标记失败：{error}", flush=True)
        return False  # 标记读不到按“没拒绝”算，宁可多写一条键


def enable() -> None:
    """写入自启动（覆盖旧值，路径自愈），并清掉拒绝标记。"""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.SetValueEx(key, RUN_VALUE_NAME, 0, winreg.REG_SZ, autostart_command())
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DECLINED_KEY_PATH) as key:
            try:
                winreg.DeleteValue(key, DECLINED_VALUE_NAME)
            except FileNotFoundError:
                pass
        print("[desktop] 已开启开机自启动。", flush=True)
    except OSError as error:
        print(f"[desktop] 写入开机自启动失败：{error}", flush=True)


def disable() -> None:
    """删掉自启动并记下拒绝标记——之后 ``ensure_on_start`` 不再自动开。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            try:
                winreg.DeleteValue(key, RUN_VALUE_NAME)
            except FileNotFoundError:
                pass
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, DECLINED_KEY_PATH) as key:
            winreg.SetValueEx(key, DECLINED_VALUE_NAME, 0, winreg.REG_DWORD, 1)
        print("[desktop] 已关闭开机自启动。", flush=True)
    except OSError as error:
        print(f"[desktop] 关闭开机自启动失败：{error}", flush=True)


def ensure_on_start() -> None:
    """默认开启：没有拒绝标记就确保 Run 键存在且路径是最新的。"""
    if _declined():
        return
    enable()
