from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IngredientInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=40)
    quantity: float = Field(ge=0, le=1_000_000)
    unit: str = Field(min_length=1, max_length=20)
    expiration_date: date | None = None
    notes: str = Field(default="", max_length=500)
    low_stock_threshold: float | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("name", "category", "unit")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str) -> str:
        return value.strip()


class Ingredient(IngredientInput):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime
    updated_at: datetime


class IngredientSummary(BaseModel):
    total: int
    fresh: int
    soon: int
    expired: int
    low: int


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)
    request_id: str | None = Field(default=None, min_length=8, max_length=80)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


class ChatResponse(BaseModel):
    answer: str
    source: Literal["inventory", "model"]
    model: str | None


class ChatStatus(BaseModel):
    online: bool
    model: str
    installed: bool
