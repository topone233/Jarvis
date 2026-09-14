"""What a user can change without touching the source.

Two kinds of thing live here: the three prompts the model is driven by, and the
defaults the call parameters fall back to.

Neither is written into the database until it is changed. A setting with no row
has never been touched, which buys two things: an improved default reaches
everyone who left it alone, and "restore the default" is a delete rather than a
copy of whatever the code happened to say on the day it was first saved.
"""

from __future__ import annotations

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
COMPACT_PERCENT_DEFAULT = 72


def read_prompt(store: Store, key: str) -> str:
    """The text in force: what the user saved, or the one the code ships."""
    stored = store.get_setting(key)
    return PROMPT_DEFAULTS[key] if stored is None else stored


def overview(store: Store) -> dict[str, Any]:
    """Everything the prompts tab needs, in one read.

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
        }
    }
