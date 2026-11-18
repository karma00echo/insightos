"""Only explicit user requests may create or change approved tasks."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.decision_schemas import DecisionReference
from app.memory_schemas import StrictModel

Status = Literal["todo", "in_progress", "completed", "cancelled"]


class ActionContent(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    due_date: date | None = None


class ActionCreate(ActionContent):
    request_id: UUID
    approved: StrictBool
    source: DecisionReference | None = None

    @model_validator(mode="after")
    def approval(self):
        if self.approved is not True:
            raise ValueError("Explicit approval required")
        return self


class ActionCommand(StrictModel):
    request_id: UUID
    expected_version: int = Field(ge=1, strict=True)


class ActionEdit(ActionContent, ActionCommand):
    reason: str = Field(default="", max_length=500)


class ActionStatus(ActionCommand):
    status: Status
    reason: str = Field(default="", max_length=500)
