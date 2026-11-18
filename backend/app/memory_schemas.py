"""Explicit, strict user confirmation. Unknown properties (including owner) fail."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


class MemorySource(StrictModel):
    type: Literal["user_input", "user_message", "document_chunk"] = "user_input"
    id: UUID | None = None
    version: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid_reference(self):
        if self.type == "user_input":
            if self.id is not None or self.version is not None:
                raise ValueError("Direct input has no external reference")
        elif self.id is None or self.version is None:
            raise ValueError("Source ID and original version required")
        return self


class ConfirmedContent(StrictModel):
    kind: Literal["user_fact", "preference", "plan"]
    content: str = Field(min_length=1, max_length=4000)
    confirmed: StrictBool

    @model_validator(mode="after")
    def require_confirmation(self):
        if self.confirmed is not True:
            raise ValueError("Explicit confirmation required")
        return self


class MemoryCreate(ConfirmedContent):
    request_id: UUID
    source: MemorySource = Field(default_factory=MemorySource)


class MemoryEdit(ConfirmedContent):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=500)


class MemoryAction(StrictModel):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)
