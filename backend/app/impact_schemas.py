"""Bound, ephemeral model output. No model-controlled identities or write tools."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.memory_schemas import StrictModel

Text = Annotated[str, Field(min_length=1, max_length=1000)]


class ImpactRequest(StrictModel):
    decision_id: UUID
    decision_version: int = Field(ge=1, strict=True)
    change_id: UUID


class Quote(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=1, strict=True)
    text: Text


class Finding(StrictModel):
    decision: Quote
    changes: list[Quote] = Field(min_length=1, max_length=8)
    explanation: Text


class ImpactOutput(StrictModel):
    status: Literal['potential_impact', 'no_clear_impact', 'insufficient_evidence']
    findings: list[Finding] = Field(max_length=8)
    uncertainties: list[Text] = Field(min_length=1, max_length=8)
    questions: list[Text] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def supported_status(self):
        if self.status != 'insufficient_evidence' and not self.findings:
            raise ValueError('A conclusion needs bound evidence')
        return self


class ReviewChoice(StrictModel):
    request_id: UUID
    expected_sequence: int = Field(ge=1, strict=True)
    status: Literal['viewed', 'needs_review', 'keep', 'revise']
