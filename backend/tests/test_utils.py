from __future__ import annotations

from app.utils import utc_now


def test_stamps_are_strictly_increasing() -> None:
    """同一 coarse tick 里的连续取值也不得相等——列表按这些字符串排序，
    相等就意味着顺序交给文件系统或字典碰运气（notepad 同刻度建两张便签
    时真的发生过）。"""
    stamps = [utc_now() for _ in range(500)]
    assert all(a < b for a, b in zip(stamps, stamps[1:], strict=False))
