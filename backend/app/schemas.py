from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    reasoning_levels: list[str] = Field(default_factory=list)
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
    reasoning_levels: list[str] | None = None
    is_default: bool | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        return value.strip().rstrip("/") if value else value


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
    reasoning_level: str | None = Field(default=None, max_length=30)


class RegenerateRequest(BaseModel):
    model_profile_id: str | None = None
    reasoning_level: str | None = Field(default=None, max_length=30)


class CompactRequest(BaseModel):
    model_profile_id: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5_000)
    memory_key: str | None = Field(default=None, min_length=1, max_length=200)
    confidence: float | None = Field(default=None, ge=0, le=1)


class FeedbackCreate(BaseModel):
    kind: Literal["up", "down"]
