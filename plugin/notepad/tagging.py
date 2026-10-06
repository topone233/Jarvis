"""The notepad plugin's automatic tagging: one background call per new note.

The model is asked for a small JSON array of short tags; the reply is parsed
by finding the first ``[...]`` in it, because models fence JSON in prose no
matter what the instructions say. The write goes through ``apply_tags``,
which lands only while the note is still ``pending`` - a hand-set tag list,
a deletion, or an external edit that arrived first all win over the
suggestion. Failure of any kind is a status, not a crash: the note is marked
``failed`` and the UI offers a retry.
"""

from __future__ import annotations

import asyncio
import json
import re

from app.plugin_host import PluginContext

#: How much of a note the model sees. A note is quick jottings, but a pasted
#: log can be enormous, and no tag needs the tail of it.
_CONTENT_LIMIT = 2_000

_ARRAY_PATTERN = re.compile(r"\[[^\[\]]*\]", re.DOTALL)


def schedule_autotag(context: PluginContext, note_id: str) -> None:
    """Decide whether a note gets tagged, and kick the work off detached.

    The decision happens here, at request time, so a note created with the
    switch off keeps ``none`` instead of pending forever; the task itself
    outlives the response by design.
    """
    if not context.config.get("auto_tag", True):
        return
    store = context.store
    try:
        store.get_default_model_profile()
    except Exception:  # noqa: BLE001 - no profile: not an error, just not possible
        context.state["index"].mark_tag_failed(note_id)
        return
    context.state["index"].update(note_id, tag_status="pending")
    asyncio.get_running_loop().create_task(_tag(context, note_id))


async def retag(context: PluginContext, note_id: str) -> None:
    """The retry: synchronous, so the endpoint can answer with the failure."""
    try:
        context.store.get_default_model_profile()
    except Exception as error:  # noqa: BLE001
        raise RuntimeError("未配置默认模型，无法自动打标签。请先在设置里选一个默认模型。") from error
    context.state["index"].update(note_id, tag_status="pending")
    await _tag(context, note_id)


def tags_from_reply(reply: str) -> list[str]:
    """The tags in a model's reply, or what could be salvaged of them."""
    match = _ARRAY_PATTERN.search(reply)
    if match is None:
        return []
    try:
        values = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for value in values:
        tag = str(value).strip().strip("#").strip()
        if not tag or tag in cleaned:
            continue
        cleaned.append(tag[:24])
        if len(cleaned) == 4:
            break
    return cleaned


async def _tag(context: PluginContext, note_id: str) -> None:
    index = context.state["index"]
    try:
        note = index.get(note_id)
        profile = context.store.get_default_model_profile()
        reply = await context.provider.complete_chat(
            profile,
            [
                {
                    "role": "system",
                    "content": (
                        "你是便签的标签助手。根据便签内容输出 2 到 4 个简短标签，"
                        "用于日后检索。只输出一个 JSON 数组，例如 [\"部署\", \"nginx\"]，"
                        "不要输出任何其他内容。标签用与内容相同的语言。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"标题：{note.title or '（无）'}\n\n"
                        f"内容：\n{note.content[:_CONTENT_LIMIT]}"
                    ),
                },
            ],
            thinking="off",
        )
        tags = tags_from_reply(reply)
        if not tags:
            raise RuntimeError("模型没有返回可用的标签。")
        # apply_tags itself declines when the note is no longer pending - a
        # hand-set list or a deletion that raced ahead wins.
        index.apply_tags(note_id, tags)
    except Exception:  # noqa: BLE001 - a dead endpoint is a failed status, not an outage
        index.mark_tag_failed(note_id)
