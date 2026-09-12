from __future__ import annotations

import math


def estimate_tokens(value: str) -> int:
    """Conservative local estimate suitable for a context-budget indicator."""

    cjk_count = sum("\u4e00" <= character <= "\u9fff" for character in value)
    other_count = len(value) - cjk_count
    return max(1, math.ceil(cjk_count * 1.2 + other_count / 4))
