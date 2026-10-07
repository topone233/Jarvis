"""autostart 的纯函数部分：Run 键命令串的拼装。

winreg 的读写是与注册表的系统交互，按约定不测（也不能在测试里碰用户真实
的 Run 键）。这里只钉住命令串的形状：双引号包裹、pythonw 静默执行、
--hidden 进托盘。
"""

from app.desktop import autostart


def test_command_targets_pythonw_launcher_hidden() -> None:
    command = autostart.autostart_command()
    assert command.endswith('" --hidden')
    assert "pythonw.exe" in command  # pythonw：无控制台窗口
    assert "autostart_launch.pyw" in command
    # 两个路径各自用双引号包裹——路径含空格也不裂。
    assert command.count('"') == 4
