"""No inferred approval: only the final explicitly confirmed structured request."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.memory_schemas import StrictModel

ShortText = Annotated[str, Field(min_length=1, max_length=2000)]


class Option(StrictModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: ShortText


class BasisInput(StrictModel):
    type: Literal["user_input", "memory", "user_message", "document_chunk"]
    id: UUID | None = None
    version: str | None = Field(default=None, min_length=1, max_length=128)
    revision: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def reference_shape(self):
        if self.type == "user_input":
            valid = self.id is None and self.version is None and self.revision is None
        elif self.type == "memory":
            valid = self.id is not None and self.revision is not None and self.version is None
        else:
            valid = self.id is not None and self.version is not None and self.revision is None
        if not valid:
            raise ValueError("Invalid basis reference")
        return self


class DecisionCreate(StrictModel):
    request_id: UUID
    confirmed: StrictBool
    question: ShortText
    options: list[Option] = Field(min_length=2, max_length=20)
    selected_option: str = Field(min_length=1, max_length=64)
    reasons: list[ShortText] = Field(min_length=1, max_length=20)
    assumptions: list[ShortText] = Field(max_length=20)
    review_conditions: list[ShortText] = Field(max_length=20)
    basis: list[BasisInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def final_confirmation(self):
        keys = [o.id for o in self.options]
        if not self.confirmed or len(set(keys)) != len(keys) or self.selected_option not in keys:
            raise ValueError("Explicit final choice required")
        return self


class DecisionEdit(DecisionCreate):
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=1, max_length=500)


class DecisionAction(StrictModel):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class DecisionSearch(StrictModel):
    query: str = Field(default="", max_length=200)
    status: Literal["active", "withdrawn"] = "active"
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=20, ge=1, le=100, strict=True)


class DecisionReference(StrictModel):
    id: UUID
    version: int = Field(ge=1, strict=True)
