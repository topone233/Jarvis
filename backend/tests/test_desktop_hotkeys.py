"""parse_combo 的金色用例，与 ``frontend/src/plugins/hotkey.test.ts`` 同构。

两边是同一条契约（设置页存下的字符串，页内监听和系统注册都要认），
测试把语义各自钉死，改一边就得改另一边。
"""

from app.desktop.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    HotkeyManager,
    parse_combo,
)


class TestParseCombo:
    def test_reads_modifiers_in_any_case_and_order(self) -> None:
        assert parse_combo("alt+n") == (MOD_ALT, ord("N"))
        assert parse_combo("Ctrl+Shift+N") == (MOD_CONTROL | MOD_SHIFT, ord("N"))

    def test_reads_function_keys(self) -> None:
        assert parse_combo("f9") == (0, 0x70 + 9 - 1)
        assert parse_combo("Ctrl+F12") == (MOD_CONTROL, 0x70 + 12 - 1)

    def test_maps_win_to_mod_win(self) -> None:
        assert parse_combo("Win+M") == (MOD_WIN, ord("M"))
        assert parse_combo("Win+9") == (MOD_WIN, ord("9"))

    def test_refuses_bare_key_bare_modifier_two_keys_and_junk(self) -> None:
        assert parse_combo("N") is None
        assert parse_combo("Alt") is None
        assert parse_combo("Alt+N+M") is None
        assert parse_combo("Alt+Enter") is None
        assert parse_combo("") is None


class TestHotkeyManager:
    def test_rebind_without_a_running_loop_reports_and_does_not_raise(self) -> None:
        manager = HotkeyManager(lambda plugin_id: None)
        assert manager.rebind({"notepad": "Alt+N"}) == ["快捷键线程没有在运行。"]
        manager.stop()  # 没启动过也必须是安全的空操作
