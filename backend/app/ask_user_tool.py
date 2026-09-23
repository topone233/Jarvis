"""The `ask_user` tool: the model asks, the user answers from a popup.

One tool, two parameters - a question, and the options to answer with. When
the model calls it, the run pauses until the user picks an option or types
their own answer, and that answer comes back as the tool result. The point is
decisions the model should not guess: which of two fixes to apply, which file
to touch, whether to proceed at all.

Like every tool here, every failure answers as text - the message is the
model's only schema. The popup itself is the frontend's business; the backend
only emits `user_input.requested` and waits (see runs.py).
"""

from __future__ import annotations

from typing import Any

#: Bounds that keep one question from becoming a context-window event. A
#: question over this long is the model thinking out loud, not asking.
QUESTION_MAX_CHARS = 500

#: Options beyond half a dozen stop being a choice and become a form.
MAX_OPTIONS = 6
OPTION_MAX_CHARS = 200

USAGE = (
    f'ask_user 用法：ask_user(question="问题", options=["选项一", "选项二", …])，'
    f"问题不超过 {QUESTION_MAX_CHARS} 字，选项 0 到 {MAX_OPTIONS} 个、每个不超过 "
    f"{OPTION_MAX_CHARS} 字。用户可以不选而自由回答。"
)

ASK_USER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "当需要用户本人在明确的选项之间做决定，或必须由用户补充信息才能继续时，"
                "向用户提问并暂停等待回答。用户会看到选项按钮，也可以自由输入；"
                "不要用它问你自己能从对话或工具结果里得到答案的问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "要问用户的问题，一句话说清需要用户决定什么。",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("给用户的选择项，每项一句话；用户也可以不选而自由回答。"),
                    },
                },
                "required": ["question"],
            },
        },
    }
]


def validate_ask_user(arguments: dict[str, Any]) -> tuple[str | None, list[str], str | None]:
    """Check one ask_user call's arguments; (question, options, error).

    Exactly one of question/error is set. Options are optional - a question
    with none still pauses for a free-text answer - but whatever is provided
    must be a clean list of non-empty strings, because the popup renders them
    as buttons, not as prose to guess at.
    """
    question = arguments.get("question")
    if not isinstance(question, str) or not question.strip():
        return None, [], "ask_user: 缺少要问的问题。" + USAGE
    if len(question) > QUESTION_MAX_CHARS:
        return None, [], f"ask_user: 问题超过 {QUESTION_MAX_CHARS} 字。" + USAGE
    raw_options = arguments.get("options")
    options: list[str] = []
    if raw_options is not None:
        if not isinstance(raw_options, list) or not all(isinstance(o, str) for o in raw_options):
            return None, [], "ask_user: options 必须是字符串数组。" + USAGE
        options = [option.strip() for option in raw_options]
        if any(not option for option in options):
            return None, [], "ask_user: 选项不能是空字符串。" + USAGE
        if len(options) > MAX_OPTIONS:
            return None, [], f"ask_user: 选项最多 {MAX_OPTIONS} 个。" + USAGE
        if any(len(option) > OPTION_MAX_CHARS for option in options):
            return None, [], f"ask_user: 单个选项不超过 {OPTION_MAX_CHARS} 字。" + USAGE
    return question, options, None
