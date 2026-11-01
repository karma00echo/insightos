"""Report input is an existing task ID, never client-authored conclusions."""
from typing import Literal

from pydantic import Field, model_validator

from app.research_schemas import Strict


class ReportCreate(Strict):
    task_id: str = Field(min_length=1, max_length=36)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[\w-]+$")


class WrittenParagraph(Strict):
    text: str = Field(min_length=40, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=6,
        description="仅选提供的证据；每条证据的 allowed_step_ids 必须与本段 step_ids 至少相交一次，不得重复。")
    step_ids: list[str] = Field(min_length=1, max_length=4,
        description="本段实际讨论的研究步骤；每个步骤必须由本段至少一条合法关联证据覆盖，不得为通过检查而虚列。")


class WrittenSection(Strict):
    id: Literal["abstract", "background", "findings", "discussion", "conclusion"]
    paragraphs: list[WrittenParagraph] = Field(min_length=1, max_length=3)


class WrittenReport(Strict):
    sections: list[WrittenSection] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def outline(self):
        if [s.id for s in self.sections] != ["abstract", "background", "findings", "discussion", "conclusion"]:
            raise ValueError("Report writing requires the complete ordered outline")
        if len(self.sections[3].paragraphs) < 2:
            raise ValueError("Discussion needs multiple evidence-bound analysis paragraphs")
        paragraphs = [p.text for s in self.sections for p in s.paragraphs]
        if len(set(paragraphs)) != len(paragraphs) or sum(map(len, paragraphs)) < 400:
            raise ValueError("Report writing is incomplete or repeats paragraphs")
        return self


class ReportClaim(Strict):
    id: str
    source_pointer: str
    kind: Literal["evidence", "synthesis"]
    text: str = Field(min_length=1, max_length=10000)
    evidence_ids: list[str]
    step_ids: list[str]
    quality_status: Literal["TEXT_ATTRIBUTED", "NEEDS_REVIEW", "INSUFFICIENT_EVIDENCE", "POTENTIAL_CONFLICT"]


class ReportSection(Strict):
    id: str
    title: str
    paragraphs: list[str]
    claim_ids: list[str]


class ReportContent(Strict):
    schema_version: Literal["research-report-v1"]
    task_id: str
    task_sha256: str
    title: str
    generation: dict
    research: dict
    sections: list[ReportSection] = Field(min_length=8, max_length=8)
    claims: list[ReportClaim] = Field(max_length=32)
    evidence: list[dict]
    validation: dict
    quality_status: Literal["QUALITY_PENDING_HUMAN_REVIEW"]
    evaluations: list[dict]

    @model_validator(mode="after")
    def references(self):
        ids = [c.id for c in self.claims]
        expected = ["abstract", "background", "method", "findings", "discussion", "conclusion", "limitations", "references"]
        if len(set(ids)) != len(ids) or [s.id for s in self.sections] != expected:
            raise ValueError("Invalid report structure")
        if any(cid not in ids for s in self.sections for cid in s.claim_ids):
            raise ValueError("Unknown report claim")
        return self
