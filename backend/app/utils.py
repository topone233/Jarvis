from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
