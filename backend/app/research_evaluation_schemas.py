"""Versioned research rubrics. References are annotations, never generated truth."""
from typing import Literal

from pydantic import Field, model_validator

from app.research_schemas import Strict


class Anchor(Strict):
    id: str = Field(min_length=1, max_length=80)
    document_name: str = Field(min_length=1, max_length=240)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    locator: str = Field(min_length=1, max_length=160)
    quote: str = Field(min_length=4, max_length=1600)
    basis: str = Field(min_length=4, max_length=500)


class ResearchCase(Strict):
    id: str = Field(min_length=1, max_length=80)
    split: Literal["development", "holdout"]
    scenario: str = Field(min_length=2, max_length=100)
    question: str = Field(min_length=5, max_length=1200)
    material_scope: list[str] = Field(min_length=1, max_length=20)
    provenance: Literal["source_reference_unreviewed", "synthetic_fixture", "human_confirmed"]
    annotation_version: str = Field(min_length=1, max_length=80)
    annotated_by: str = Field(min_length=1, max_length=160)
    expected_points: list[str] = Field(min_length=1, max_length=20)
    anchors: list[Anchor] = Field(max_length=20)
    rubric: list[str] = Field(min_length=1, max_length=20)
    failure_conditions: list[str] = Field(min_length=1, max_length=20)
    expected_conflicts: list[list[str]] | None = Field(default=None, max_length=12)
    context_questions: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def references(self):
        ids = [a.id for a in self.anchors]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate anchor ID")
        if any(a.document_name not in self.material_scope for a in self.anchors):
            raise ValueError("Anchor outside material scope")
        for pair in self.expected_conflicts or []:
            if len(pair) != 2 or len(set(pair)) != 2 or not set(pair) <= set(ids):
                raise ValueError("Conflict must reference two distinct anchors")
        if self.provenance == "human_confirmed" and self.annotated_by.startswith("codex"):
            raise ValueError("Model-authored annotations are not human confirmation")
        return self


class Benchmark(Strict):
    version: str = Field(min_length=1, max_length=80)
    cases: list[ResearchCase] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case ID")
        return self


class ResearchEvaluationRequest(Strict):
    name: str = Field(min_length=1, max_length=120)
    task_id: str = Field(min_length=1, max_length=80)
    case: ResearchCase
    benchmark_version: str = Field(min_length=1, max_length=80)
    review: "HumanReview | None" = None
    comparison_task_id: str | None = Field(default=None, min_length=1, max_length=80)


class ReviewDecision(Strict):
    metric: Literal["task_completion", "fact_support", "conflict_identification", "result_consistency", "plan_quality"]
    unit_id: str = Field(min_length=1, max_length=100)
    verdict: Literal["pass", "fail", "unknown"]
    rationale: str = Field(min_length=4, max_length=1200)
    evidence_locations: list[str] = Field(min_length=1, max_length=20)


class HumanReview(Strict):
    method: Literal["human"]
    reviewer: str = Field(min_length=2, max_length=120)
    reviewed_at: str = Field(min_length=10, max_length=80)
    annotation_version: str = Field(min_length=1, max_length=80)
    task_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    case_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    comparison_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    decisions: list[ReviewDecision] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        keys = [(d.metric, d.unit_id) for d in self.decisions]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate review unit")
        return self
