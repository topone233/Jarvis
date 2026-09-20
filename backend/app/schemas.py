from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.settings import (
    COMPACT_PERCENT_DEFAULT,
    TOOL_MAX_ROUNDS_CEILING,
    TOOL_REPEAT_LIMIT_CEILING,
    TOOL_REPEAT_WINDOW_CEILING,
)

# The four positions of the composer's thinking dial, in the order the slider
# draws them. Only "off" is the profile's own business - asking a provider not
# to think has no single spelling, so the profile's "thinking off" fragment goes
# out as written. The other three are one name this server sets itself, so they
# send `reasoning_effort` and nothing else. See `provider._thinking_fields`.
ThinkingLevel = Literal["off", "low", "high", "max"]


class SetupRequest(BaseModel):
    data_directory: str = Field(min_length=1)


class ModelProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    chat_model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    context_window: int = Field(default=128_000, ge=4_096, le=2_000_000)
    output_token_reserve: int = Field(default=8_192, ge=256, le=200_000)
    # Absent means the field is not sent to the provider at all, leaving its own
    # limit in force.
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    # A share of the input budget rather than a token count: see app/settings.py.
    # The floor is not zero because compacting an almost empty conversation is
    # wasted work, and the ceiling is not 100 because the request that triggers
    # a compaction has to fit in the window it is measuring against.
    compact_percent: int = Field(default=COMPACT_PERCENT_DEFAULT, ge=10, le=95)
    # What to say to this endpoint when the thinking dial is at "off", merged
    # into the request body as written. Objects rather than a list of levels,
    # because asking a provider not to think has no single spelling. Typed as
    # dict rather than anything narrower on purpose: the fragment is the
    # provider's own dialect and this server does not get to have an opinion
    # about its keys.
    #
    # `thinking_on` is read by nobody: the three strengths send `reasoning_effort`
    # under this server's own name, not a fragment. It is still stored, and the
    # form still carries it round-trip untouched, so a profile written while the
    # switch was a switch loses nothing - but nothing new should start reading it
    # without deciding that the strengths are configurable again.
    thinking_on: dict[str, Any] = Field(default_factory=dict)
    thinking_off: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")


class ModelProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    chat_model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)
    context_window: int | None = Field(default=None, ge=4_096, le=2_000_000)
    output_token_reserve: int | None = Field(default=None, ge=256, le=200_000)
    max_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    compact_percent: int | None = Field(default=None, ge=10, le=95)
    thinking_on: dict[str, Any] | None = None
    thinking_off: dict[str, Any] | None = None
    is_default: bool | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        return value.strip().rstrip("/") if value else value


class QuickPrompt(BaseModel):
    """One button above an empty composer: the name is what the button says,
    the prompt is what one click fills the input box with."""

    name: str = Field(min_length=1, max_length=50)
    prompt: str = Field(min_length=1, max_length=2_000)


class SettingsUpdate(BaseModel):
    """Prompts to save, and prompts to put back.

    A key that is absent from the request is not touched; a key sent as null
    goes back to the text the code ships. The three are independent, which is
    what lets the settings screen save one tab's worth of edits at a time.
    """

    system_prompt: str | None = None
    compaction_prompt: str | None = None
    memory_prompt: str | None = None
    # The whole button list at once, in draw order. An empty list is a real
    # answer - the buttons were cleared on purpose - so it is stored as such
    # rather than collapsing back into the built-in pair; null is what
    # 恢复默认 sends, and it deletes the row.
    quick_prompts: list[QuickPrompt] | None = Field(default=None, max_length=12)
    # The tool-call budget, in three numbers. Absent = untouched, null = back
    # to the code default - the same bargain the prompts make.
    tool_max_rounds: int | None = Field(default=None, ge=1, le=TOOL_MAX_ROUNDS_CEILING)
    tool_repeat_window_seconds: int | None = Field(
        default=None, ge=1, le=TOOL_REPEAT_WINDOW_CEILING
    )
    tool_repeat_limit: int | None = Field(default=None, ge=1, le=TOOL_REPEAT_LIMIT_CEILING)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_pinned: bool = False


class SkillImportRequest(BaseModel):
    """A local skill folder to copy into the data directory.

    The backend reads the folder in place - the app is local and so is the
    folder - validates its SKILL.md, and copies the tree under the skill's
    own name. Nothing but the path crosses the wire.
    """

    path: str = Field(min_length=1, max_length=1000)


class SkillEnabledUpdate(BaseModel):
    enabled: bool


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_pinned: bool | None = None


class ConversationCreate(BaseModel):
    project_id: str | None = None
    title: str = Field(default="新对话", min_length=1, max_length=200)
    model_profile_id: str | None = None
    is_pinned: bool = False


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    model_profile_id: str | None = None
    is_pinned: bool | None = None


class RunRequest(BaseModel):
    """One turn: what the user wrote, the images pasted with it, and the
    composer's choice of model and thinking strength.

    `content` may be empty when images carry the message - a screenshot with
    no words is a question of its own - but something has to be there.
    """

    content: str = Field(default="", max_length=200_000)
    # Data URLs, compressed by the composer before it ever sends them. There
    # is no count cap on purpose: the budget ring is what tells the user a
    # draft is getting expensive, and the size cap below is the only hard line.
    images: list[str] = Field(default_factory=list)
    model_profile_id: str | None = None
    # The model the composer's dropdown is showing. None means "whatever the
    # profile says", which is what every internal caller wants.
    chat_model: str | None = Field(default=None, min_length=1, max_length=200)
    # Where the dial is, not whether thinking is on: see ThinkingLevel. Kept
    # under the name `thinking` on purpose. A client from before this was a dial
    # sends `true`, and a renamed field would be dropped as unknown - it would
    # answer with thinking off and say nothing about it. Same name, new type, is
    # a 422 that names the field instead.
    thinking: ThinkingLevel = "off"

    @model_validator(mode="after")
    def something_must_be_sent(self) -> RunRequest:
        if self.content.strip() == "" and not self.images:
            raise ValueError("消息不能为空。")
        return self


class RegenerateRequest(BaseModel):
    model_profile_id: str | None = None
    chat_model: str | None = Field(default=None, min_length=1, max_length=200)
    thinking: ThinkingLevel = "off"


class CompactRequest(BaseModel):
    model_profile_id: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5_000)
    memory_key: str | None = Field(default=None, min_length=1, max_length=200)
    confidence: float | None = Field(default=None, ge=0, le=1)


class RetrievalModelSpec(BaseModel):
    """One retrieval model config - the embedding endpoint or the reranker.

    `api_key` is write-only: it goes to the keyring under the kind's fixed
    identifier and never comes back out. A value replaces the stored key, an
    empty string deletes it, and anything else - absent or null - leaves the
    stored key untouched.
    """

    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        return value.strip().rstrip("/")


class RetrievalSettingsUpdate(BaseModel):
    """Both retrieval configs at once, with per-kind absent/null semantics.

    A kind absent from the request is not touched; sent as null it is cleared
    (config and keyring entry both). This is what lets one card save without
    stomping the other.
    """

    embedding: RetrievalModelSpec | None = None
    rerank: RetrievalModelSpec | None = None


class RetrievalTestRequest(BaseModel):
    """A test call against an endpoint, optionally not yet saved.

    Every field is optional: absent fields mean "test what is stored", which
    is how the 保存前测试 without re-typing the key works - the stored
    keyring entry fills in.
    """

    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=1000)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        return value.strip().rstrip("/") if value else value


class FeedbackCreate(BaseModel):
    kind: Literal["up", "down"]
