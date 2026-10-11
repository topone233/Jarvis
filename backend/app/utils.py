from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_last_stamp_us = 0
_stamp_lock = threading.Lock()


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    """A UTC ISO stamp, strictly increasing within the process.

    The coarse Windows clock (15.6ms ticks on CPython 3.12) hands identical
    stamps to writes that land in the same tick, and lists everywhere sort on
    these strings - two notes saved back-to-back would order by whatever the
    filesystem glob feels like. A repeat is bumped by one microsecond, which
    makes the sort total. The value is a sort key and a conflict token, not a
    clock reading, so the invisible skew inside one tick costs nothing.
    """
    global _last_stamp_us
    with _stamp_lock:
        now = datetime.now(UTC)
        stamp_us = int(now.timestamp()) * 1_000_000 + now.microsecond
        if stamp_us <= _last_stamp_us:
            stamp_us = _last_stamp_us + 1
        _last_stamp_us = stamp_us
    return (
        datetime.fromtimestamp(stamp_us // 1_000_000, UTC)
        .replace(microsecond=stamp_us % 1_000_000)
        .isoformat()
    )


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def normalize_key(value: str) -> str:
    value = value.strip().lower()
    return re.sub(r"\s+", " ", value)


# U+4E00-U+9FFF: CJK Unified Ideographs.
_CJK_RUN = re.compile(r"[一-鿿]+")


def _expand_cjk_bigrams(match: re.Match[str]) -> str:
    run = match.group(0)
    if len(run) < 2:
        return run
    return " ".join(run[index : index + 2] for index in range(len(run) - 1))


def segment_for_index(value: str) -> str:
    """Split CJK runs into overlapping bigrams so FTS5 can match sub-phrases.

    The default unicode61 tokenizer treats a whole CJK run as one token, so
    "上下文" would never match "并支持上下文压缩". Overlapping bigrams make
    two-character queries - the common case in Chinese - searchable without
    pulling in a segmentation dependency. Indexing and querying must both go
    through this function; see tests/test_knowledge.py.
    """
    return _CJK_RUN.sub(_expand_cjk_bigrams, value)


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value).strip("._")
    return cleaned or "document.txt"
