from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.settings import COMPACT_PERCENT_DEFAULT

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
    embedding_model: str | None = Field(default=None, max_length=200)
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
    embedding_model: str | None = Field(default=None, max_length=200)
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


class SettingsUpdate(BaseModel):
    """Prompts to save, and prompts to put back.

    A key that is absent from the request is not touched; a key sent as null
    goes back to the text the code ships. The three are independent, which is
    what lets the settings screen save one tab's worth of edits at a time.
    """

    system_prompt: str | None = None
    compaction_prompt: str | None = None
    memory_prompt: str | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_pinned: bool = False


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
    content: str = Field(min_length=1, max_length=200_000)
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


class FeedbackCreate(BaseModel):
    kind: Literal["up", "down"]
