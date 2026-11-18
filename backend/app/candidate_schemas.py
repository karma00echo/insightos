"""Transient inputs and incomplete proposals, separate from confirmed decisions."""

from typing import Literal

from pydantic import Field, model_validator

from app.decision_schemas import BasisInput, DecisionReference, Option, ShortText
from app.memory_schemas import StrictModel


class Turn(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class CandidateRequest(StrictModel):
    messages: list[Turn] = Field(min_length=1, max_length=12)
    decision_references: list[DecisionReference] = Field(default_factory=list, max_length=3)
    sources: list[BasisInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def limited_context(self):
        if sum(len(t.content) for t in self.messages) > 8000 or self.messages[-1].role != "user":
            raise ValueError("Limited context must end with user input")
        return self


class CandidateDraft(StrictModel):
    question: str = Field(max_length=2000)
    options: list[Option] = Field(max_length=20)
    selected_option: str = Field(max_length=64)
    reasons: list[ShortText] = Field(max_length=20)
    assumptions: list[ShortText] = Field(max_length=20)
    review_conditions: list[ShortText] = Field(max_length=20)

    @model_validator(mode="after")
    def choice_matches(self):
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids) or (self.selected_option and self.selected_option not in ids):
            raise ValueError("Invalid candidate choice")
        return self


class ModelProposal(StrictModel):
    classification: Literal["decision", "plan", "consideration", "negation", "ai_suggestion", "unrelated"]
    reply: str = Field(min_length=1, max_length=2000)
    candidate: CandidateDraft | None


class CandidateResponse(ModelProposal):
    readiness: Literal["ready", "incomplete", "no_decision"]
    missing_fields: list[str]
    basis: list[BasisInput]
    confirmed: Literal[False] = False
    evidence_status: Literal["unverified"] = "unverified"
