"""What a user can change without touching the source.

Two kinds of thing live here: the three prompts the model is driven by, and the
defaults the call parameters fall back to.

Neither is written into the database until it is changed. A setting with no row
has never been touched, which buys two things: an improved default reaches
everyone who left it alone, and "restore the default" is a delete rather than a
copy of whatever the code happened to say on the day it was first saved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.prompts import (
    BASE_INSTRUCTION,
    COMPACTION_INSTRUCTION,
    MEMORY_MANAGEMENT_INSTRUCTION,
)

if TYPE_CHECKING:
    from app.store import Store

SYSTEM_PROMPT = "system_prompt"
COMPACTION_PROMPT = "compaction_prompt"
MEMORY_PROMPT = "memory_prompt"

#: Every prompt the settings screen offers. The order here is the order it
#: draws them in.
PROMPTS: tuple[str, ...] = (SYSTEM_PROMPT, COMPACTION_PROMPT, MEMORY_PROMPT)

PROMPT_DEFAULTS: dict[str, str] = {
    SYSTEM_PROMPT: BASE_INSTRUCTION,
    COMPACTION_PROMPT: COMPACTION_INSTRUCTION,
    MEMORY_PROMPT: MEMORY_MANAGEMENT_INSTRUCTION,
}

#: How full the input budget may get before the conversation is compacted, as a
#: percentage of it. A percentage rather than a token count because the budget
#: is derived from the context window, and a fixed count would silently stop
#: making sense the moment that window is changed. Shared with the API's
#: validation so the form cannot offer a value the store rejects.
#: The composer's quick prompts: named buttons above an empty input box, one
#: click filling the prompt in for the user to finish. The list's order is the
#: order the buttons draw in - "order" is a position here, not a field of its
#: own. No row means these two; a stored empty list is a real choice (no
#: buttons at all) and must survive the round-trip, so only null, which is
#: what 恢复默认 sends, puts this pair back.
QUICK_PROMPTS_DEFAULT: list[dict[str, str]] = [
    {"name": "记一下", "prompt": "记一下："},
    {"name": "记待办", "prompt": "记个待办："},
]

#: The settings row the quick-prompt list lives under. A single JSON array
#: rather than a row per button: the list is edited and sent as a whole, and
#: its order is the only ordering there is.
QUICK_PROMPTS = "quick_prompts"

COMPACT_PERCENT_DEFAULT = 72

#: How many tool rounds one run may spend before it is finished whether it
#: likes it or not. Generous on purpose - a list-type question over a
#: sectioned document legitimately wants ls, inspect, and several reads - but
#: bounded, because an unbounded loop is one confused model away from an open
#: tab that never ends.
TOOL_MAX_ROUNDS = "tool_max_rounds"
TOOL_MAX_ROUNDS_DEFAULT = 100
TOOL_MAX_ROUNDS_CEILING = 1000

#: The repeat gate: the same call - same tool, same arguments - may execute
#: this many times inside this many seconds before the next one is turned away
#: at the gate with a redirect as its tool result. What stops a stuck loop
#: burning the round budget above on one identical question.
TOOL_REPEAT_WINDOW_SECONDS = "tool_repeat_window_seconds"
TOOL_REPEAT_WINDOW_SECONDS_DEFAULT = 30
TOOL_REPEAT_WINDOW_CEILING = 3600
TOOL_REPEAT_LIMIT = "tool_repeat_limit"
TOOL_REPEAT_LIMIT_DEFAULT = 10
TOOL_REPEAT_LIMIT_CEILING = 1000


def read_prompt(store: Store, key: str) -> str:
    """The text in force: what the user saved, or the one the code ships."""
    stored = store.get_setting(key)
    return PROMPT_DEFAULTS[key] if stored is None else stored


@dataclass(frozen=True)
class ToolLimits:
    """The tool-call budget one run is given, read once at its start.

    Changing the settings mid-run therefore does nothing to the run already
    going; the next one picks the new values up.
    """

    max_rounds: int = TOOL_MAX_ROUNDS_DEFAULT
    repeat_window_seconds: int = TOOL_REPEAT_WINDOW_SECONDS_DEFAULT
    repeat_limit: int = TOOL_REPEAT_LIMIT_DEFAULT


def read_tool_limits(store: Store) -> ToolLimits:
    """The three tool-call settings in force. No row means the code default."""
    stored = store.list_settings()
    rounds = stored.get(TOOL_MAX_ROUNDS)
    window = stored.get(TOOL_REPEAT_WINDOW_SECONDS)
    limit = stored.get(TOOL_REPEAT_LIMIT)
    return ToolLimits(
        max_rounds=rounds if isinstance(rounds, int) else TOOL_MAX_ROUNDS_DEFAULT,
        repeat_window_seconds=(
            window if isinstance(window, int) else TOOL_REPEAT_WINDOW_SECONDS_DEFAULT
        ),
        repeat_limit=limit if isinstance(limit, int) else TOOL_REPEAT_LIMIT_DEFAULT,
    )


def overview(store: Store) -> dict[str, Any]:
    """Everything the settings screen reads, in one call.

    The built-in text travels with the text in force, so "restore the default"
    can show what it is about to restore instead of just claiming to know.
    """
    stored = store.list_settings()
    return {
        "prompts": {
            key: {
                "text": read_prompt(store, key),
                "default_text": PROMPT_DEFAULTS[key],
                "is_default": key not in stored,
            }
            for key in PROMPTS
        },
        "quick_prompts": {
            "items": stored.get(QUICK_PROMPTS, QUICK_PROMPTS_DEFAULT),
            "is_default": QUICK_PROMPTS not in stored,
        },
        "tool_limits": {
            "max_rounds": {
                "value": stored.get(TOOL_MAX_ROUNDS, TOOL_MAX_ROUNDS_DEFAULT),
                "default": TOOL_MAX_ROUNDS_DEFAULT,
                "is_default": TOOL_MAX_ROUNDS not in stored,
            },
            "repeat_window_seconds": {
                "value": stored.get(TOOL_REPEAT_WINDOW_SECONDS, TOOL_REPEAT_WINDOW_SECONDS_DEFAULT),
                "default": TOOL_REPEAT_WINDOW_SECONDS_DEFAULT,
                "is_default": TOOL_REPEAT_WINDOW_SECONDS not in stored,
            },
            "repeat_limit": {
                "value": stored.get(TOOL_REPEAT_LIMIT, TOOL_REPEAT_LIMIT_DEFAULT),
                "default": TOOL_REPEAT_LIMIT_DEFAULT,
                "is_default": TOOL_REPEAT_LIMIT not in stored,
            },
        },
    }
