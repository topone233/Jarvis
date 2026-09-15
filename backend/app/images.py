"""Pasted images: the one place that knows their shape on every side.

A pasted image arrives as a data URL, is checked and written into the data
directory's `objects/` next to the knowledge documents' files, and from then
on is referenced by filename in the user message's metadata. The database
never holds the bytes: opening a conversation loads every message, and
megabytes of base64 in each row would make that load crawl.

The composer compresses before sending, so the sizes that reach here are
already tamed - the cap below is a backstop rather than the UX.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.errors import NotFoundError, ValidationError

if TYPE_CHECKING:
    from app.database import Database

# What a pasted image may arrive as. GIF is allowed even though the composer
# re-encodes everything else: it is the one format whose animation is worth
# keeping, and it is small enough not to need the help.
MEDIA_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

#: A backstop, not a UX limit - a compressed JPEG over this would take
#: deliberate doing.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_DATA_URL_RE = re.compile(
    r"\Adata:(?P<mime>image/(?:png|jpeg|webp|gif));base64,(?P<body>[A-Za-z0-9+/=]+)\Z"
)

# Only filenames this module writes resolve to a path. The serving endpoint
# never takes one from the client - it takes an index into a message's own
# metadata - so this is a second lock on a door that is already shut.
_FILENAME_RE = re.compile(r"\Aimg-[0-9a-z-]+-\d+\.(?:png|jpg|webp|gif)\Z")


def parse_images(data_urls: list[str]) -> list[tuple[str, bytes]]:
    """Decode every data URL up front, so an invalid one fails before anything
    is written or any message exists."""
    payloads: list[tuple[str, bytes]] = []
    for data_url in data_urls:
        match = _DATA_URL_RE.match(data_url.strip())
        if match is None:
            raise ValidationError("只能发送 PNG、JPEG、WebP 或 GIF 图片。")
        try:
            payload = base64.b64decode(match.group("body"), validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError("这张图片的数据损坏了，请重新复制后再粘贴。") from None
        if len(payload) > MAX_IMAGE_BYTES:
            raise ValidationError("单张图片超过 10MB 了，压缩后再发一次。")
        payloads.append((match.group("mime"), payload))
    return payloads


def write_images(
    database: Database, message_id: str, payloads: list[tuple[str, bytes]]
) -> list[str]:
    """Store decoded images under the message's own id; return the filenames."""
    filenames: list[str] = []
    for index, (mime, payload) in enumerate(payloads):
        filename = f"img-{message_id}-{index}.{MEDIA_TYPES[mime]}"
        (database.objects_directory / filename).write_bytes(payload)
        filenames.append(filename)
    return filenames


def image_path(database: Database, filename: str) -> Path:
    """The file behind one stored image, refused unless the name is ours."""
    if _FILENAME_RE.match(filename) is None:
        raise NotFoundError("未找到这张图片。")
    return database.objects_directory / filename


def media_type_for(filename: str) -> str:
    """The Content-Type a stored image answers with."""
    suffix = filename.rsplit(".", 1)[-1]
    return next(mime for mime, ext in MEDIA_TYPES.items() if ext == suffix)


def image_data_url(database: Database, filename: str) -> str | None:
    """The stored bytes back as a data URL, for the provider call.

    A file that has gone missing - deleted out from under the database - comes
    back as None and the caller sends the message without that image, which is
    the honest reading of what is left.
    """
    path = image_path(database, filename)
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return f"data:{media_type_for(path.name)};base64,{base64.b64encode(payload).decode('ascii')}"


def message_images(message: dict[str, Any]) -> list[str]:
    """The image filenames a message carries, or none.

    Written only by `write_images`' caller, but metadata is a JSON blob by
    nature, so the read names its shape instead of trusting it.
    """
    images = (message.get("metadata") or {}).get("images")
    if not isinstance(images, list):
        return []
    return [image for image in images if isinstance(image, str)]
