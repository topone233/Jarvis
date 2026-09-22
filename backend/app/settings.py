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

#: The bash tool. On by default - the user asked it in - with the working
#: directory commands start in, and how a command gets to run:
#: "ask" holds the run until the user approves or denies it in the popup
#: (the default - the user asked for approval over a countdown), "grace"
#: keeps the old fixed buffer every command waits through, which is what
#: leaves the user time to stop an irreversible one.
BASH_ENABLED = "bash_enabled"
BASH_WORKING_DIR = "bash_working_dir"
BASH_GRACE_SECONDS = "bash_grace_seconds"
BASH_GRACE_SECONDS_DEFAULT = 5
BASH_GRACE_SECONDS_CEILING = 60
BASH_APPROVAL_MODE = "bash_approval_mode"
BASH_APPROVAL_MODES = ("ask", "grace")
BASH_APPROVAL_MODE_DEFAULT = "ask"

#: Retrieval relevance floors. A hit below its floor is not a hit: embeddings
#: from Chinese models (bge-large especially) hand unrelated texts a
#: non-trivial baseline similarity, and without a floor that pseudo-similarity
#: turns junk into injected memories and citations. The right value depends on
#: which embedding/rerank model is configured, so they are settings rather
#: than constants - the knowledge page's try-search shows the scores to
#: calibrate against.
RETRIEVAL_MEMORY_FLOOR = "retrieval_memory_floor"
RETRIEVAL_MEMORY_FLOOR_DEFAULT = 0.55
RETRIEVAL_SEMANTIC_FLOOR = "retrieval_semantic_floor"
RETRIEVAL_SEMANTIC_FLOOR_DEFAULT = 0.50
RETRIEVAL_RERANK_FLOOR = "retrieval_rerank_floor"
RETRIEVAL_RERANK_FLOOR_DEFAULT = 0.25
#: All three are cosine/relevance scores on 0..1, so one shared ceiling.
RETRIEVAL_FLOOR_CEILING = 0.99


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


@dataclass(frozen=True)
class BashSettings:
    """The bash tool's configuration, read once at the start of a run.

    A stored empty working directory is treated as absent: it names nothing,
    and the one thing a blank box could mean is "back to the default".
    """

    enabled: bool = True
    working_dir: str | None = None
    grace_seconds: int = BASH_GRACE_SECONDS_DEFAULT
    approval_mode: str = BASH_APPROVAL_MODE_DEFAULT


def read_bash_settings(store: Store) -> BashSettings:
    stored = store.list_settings()
    enabled = stored.get(BASH_ENABLED)
    working = stored.get(BASH_WORKING_DIR)
    grace = stored.get(BASH_GRACE_SECONDS)
    mode = stored.get(BASH_APPROVAL_MODE)
    return BashSettings(
        enabled=enabled if isinstance(enabled, bool) else True,
        working_dir=(working.strip() if isinstance(working, str) and working.strip() else None),
        # `isinstance(True, int)` holds, so the bool guard is what keeps a
        # stray boolean from becoming a grace period of 1.
        grace_seconds=(
            grace
            if isinstance(grace, int) and not isinstance(grace, bool)
            else BASH_GRACE_SECONDS_DEFAULT
        ),
        approval_mode=(
            mode
            if isinstance(mode, str) and mode in BASH_APPROVAL_MODES
            else BASH_APPROVAL_MODE_DEFAULT
        ),
    )


@dataclass(frozen=True)
class RetrievalThresholds:
    """The three relevance floors a retrieval pass applies.

    Read per call rather than per process: a settings change reaches the very
    next search. Values are raw cosine similarities (memory, semantic) or a
    rerank relevance score, all on 0..1.
    """

    memory_floor: float = RETRIEVAL_MEMORY_FLOOR_DEFAULT
    semantic_floor: float = RETRIEVAL_SEMANTIC_FLOOR_DEFAULT
    rerank_floor: float = RETRIEVAL_RERANK_FLOOR_DEFAULT


def read_retrieval_thresholds(store: Store) -> RetrievalThresholds:
    """The three floors in force. No row means the code default."""
    stored = store.list_settings()
    memory = stored.get(RETRIEVAL_MEMORY_FLOOR)
    semantic = stored.get(RETRIEVAL_SEMANTIC_FLOOR)
    rerank = stored.get(RETRIEVAL_RERANK_FLOOR)
    return RetrievalThresholds(
        memory_floor=memory
        if isinstance(memory, (int | float))
        else RETRIEVAL_MEMORY_FLOOR_DEFAULT,
        semantic_floor=(
            semantic if isinstance(semantic, (int | float)) else RETRIEVAL_SEMANTIC_FLOOR_DEFAULT
        ),
        rerank_floor=rerank
        if isinstance(rerank, (int | float))
        else RETRIEVAL_RERANK_FLOOR_DEFAULT,
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
        "bash_tool": {
            "enabled": {
                "value": stored.get(BASH_ENABLED, True),
                "is_default": BASH_ENABLED not in stored,
            },
            "working_dir": {
                "value": stored.get(BASH_WORKING_DIR, ""),
                "is_default": BASH_WORKING_DIR not in stored,
            },
            "grace_seconds": {
                "value": stored.get(BASH_GRACE_SECONDS, BASH_GRACE_SECONDS_DEFAULT),
                "default": BASH_GRACE_SECONDS_DEFAULT,
                "is_default": BASH_GRACE_SECONDS not in stored,
            },
            "approval_mode": {
                "value": stored.get(BASH_APPROVAL_MODE, BASH_APPROVAL_MODE_DEFAULT),
                "default": BASH_APPROVAL_MODE_DEFAULT,
                "is_default": BASH_APPROVAL_MODE not in stored,
            },
        },
    }
