from __future__ import annotations

import math


def estimate_tokens(value: str) -> int:
    """Conservative local estimate suitable for a context-budget indicator."""

    cjk_count = sum("\u4e00" <= character <= "\u9fff" for character in value)
    other_count = len(value) - cjk_count
    return max(1, math.ceil(cjk_count * 1.2 + other_count / 4))


#: What one pasted image costs, roughly, in whatever the endpoint's own vision
#: pricing turns out to be. A constant rather than a pixel count: the composer
#: compresses every image to the same long edge before it is ever sent, so one
#: number is as honest as a formula - and the frontend's budget ring counts
#: images with the same constant, so both sides of the budget agree.
IMAGE_TOKEN_ESTIMATE = 1_000
