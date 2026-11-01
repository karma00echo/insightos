"""Strict wire and model contracts for the single-agent research workflow."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ResearchCreate(Strict):
    question: str = Field(min_length=5, max_length=1200)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=30)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w-]+$")
    generate_report: bool = True


class ResearchRetry(Strict):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w-]+$")


class ResearchStep(Strict):
    id: str = Field(pattern=r"^s[1-4]$")
    question: str = Field(min_length=4, max_length=240)
    expected_output: str = Field(min_length=2, max_length=120)


class ResearchPlan(Strict):
    objective: str = Field(min_length=5, max_length=1200)
    steps: list[ResearchStep] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def unique_ordered(self):
        if [s.id for s in self.steps] != [f"s{i}" for i in range(1, len(self.steps) + 1)]:
            raise ValueError("Steps must have sequential unique IDs")
        normalized = [re.sub(r"[\W_]+", "", s.question).casefold() for s in self.steps]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate research questions")
        return self


class Finding(Strict):
    kind: Literal["evidence", "synthesis"]
    text: str = Field(min_length=4, max_length=900)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)
    step_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("text")
    @classmethod
    def no_inline_ids(cls, value):
        if re.search(r"\[\d+\]", value):
            raise ValueError("Citations are bound by the server, not the model")
        return value


class Synthesis(Strict):
    findings: list[Finding] = Field(min_length=1, max_length=8)
    analysis: list[Finding] = Field(max_length=4)
    limitations: list[str] = Field(max_length=8)
    follow_up_questions: list[str] = Field(max_length=6)

    @field_validator("limitations", "follow_up_questions")
    @classmethod
    def bounded_strings(cls, values):
        if any(not v.strip() or len(v) > 300 for v in values):
            raise ValueError("Invalid research note")
        return values
