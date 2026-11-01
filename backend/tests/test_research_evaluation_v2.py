"""Adversarial development fixtures; never semantic ground truth or model calls."""
import copy

import pytest
from app.research_evaluation_schemas import ResearchCase
from app.services.research_evaluation import digest
from pydantic import ValidationError
from test_research_evaluation import evaluate, sample


def simulated_review(case, task, sources, verdict="pass", metric="fact_support", uid="findings.0"):
    return {"method": "human", "reviewer": "simulated API test; not a real reviewer",
            "reviewed_at": "2026-10-30T00:00:00Z", "annotation_version": "simulated-v2",
            "task_sha256": digest(task), "case_sha256": digest(ResearchCase.model_validate(case).model_dump()),
            "source_snapshot_sha256": digest(sources), "decisions": [{"metric": metric, "unit_id": uid,
                "verdict": verdict, "rationale": "Simulated test annotation; not human ground truth",
                "evidence_locations": ["leave.txt:L1"]}]}


@pytest.mark.parametrize("outside", ["case", "knowledge_base"])
def test_out_of_scope_sources_cannot_receive_valid_citation_credit(outside):
    case, task, sources = sample()
    if outside == "case":
        case.update(material_scope=["unrelated.txt"], anchors=[])
    else:
        task["knowledge_base_ids"] = ["different-library"]
    row = evaluate(case, task, sources)
    assert row["engineering"]["status"] == "failed"
    assert row["metrics"]["citation_validity"]["score"] == 0
    assert row["metrics"]["fact_support"]["score"] == 0
    assert any("范围" in f.get("reason", "") for f in row["failures"])


def test_reverse_step_binding_and_unrelated_extra_citation_are_detected():
    case, task, sources = sample("RQ-D08")
    evidence = next(e for e in task["evidence"] if "s1" in e["step_ids"])
    task["steps"][0]["evidence_ids"].remove(evidence["id"])
    row = evaluate(case, task, sources)
    assert any(c["check"] == "step_evidence_bindings" and c["status"] == "failed"
               for c in row["engineering"]["checks"])
    assert any(c["verdict"] == "fail" for c in row["metrics"]["citation_validity"]["units"])
    case, task, sources = sample("RQ-D08")
    finding = task["result"]["findings"][0]
    unrelated = next(e for e in task["evidence"] if not set(e["step_ids"]) & set(finding["step_ids"]))
    finding["evidence_ids"].append(unrelated["id"])
    row = evaluate(case, task, sources)
    assert row["metrics"]["fact_support"]["units"][0]["verdict"] == "fail"


def test_execution_must_match_saved_plan():
    case, task, sources = sample()
    task["steps"][0]["question"] = "执行了与计划不同的研究问题"
    row = evaluate(case, task, sources)
    assert any(c["check"] == "plan_execution_binding" and c["status"] == "failed"
               for c in row["engineering"]["checks"])
    assert row["metrics"]["task_completion"]["score"] is None


@pytest.mark.parametrize("verdict", ["pass", "unknown"])
def test_human_review_cannot_erase_failed_source_checks(verdict):
    case, task, sources = sample()
    task["evidence"][0]["origins"][0]["revision_id"] = "wrong"
    review = simulated_review(case, task, sources, verdict)
    with pytest.raises(ValueError, match="cannot override"):
        evaluate(case, task, sources, review=review)


def test_missing_source_cannot_be_promoted_by_semantic_review():
    case, task, _ = sample()
    with pytest.raises(ValueError, match="cannot override"):
        evaluate(case, task, {}, review=simulated_review(case, task, {}))


def test_review_binds_source_snapshot_and_requires_new_field():
    case, task, sources = sample()
    review = simulated_review(case, task, sources)
    changed = copy.deepcopy(sources)
    next(iter(changed.values()))["text"] = "source body changed"
    with pytest.raises(ValueError, match="Stale review: source"):
        evaluate(case, task, changed, review=review)
    del review["source_snapshot_sha256"]
    with pytest.raises(ValidationError):
        evaluate(case, task, sources, review=review)


def test_human_semantic_decision_keeps_automatic_attribution_verdict():
    case, task, sources = sample()
    row = evaluate(case, task, sources, review=simulated_review(case, task, sources, "fail"))
    fact = row["metrics"]["fact_support"]["units"][0]
    assert fact["automatic_verdict"] == "pass" and fact["verdict"] == "fail"
    assert fact["automatic_reason"] and fact["human_review"]["rationale"]
    assert row["metrics"]["citation_validity"]["score"] == 1
    assert row["case_reference"] == ResearchCase.model_validate(case).model_dump()


def test_synthesis_failure_has_safe_specific_rule_and_location():
    case, task, sources = sample()
    task["result"]["findings"][0]["text"] = "PRIVATE SENTINEL: invented quote"
    row = evaluate(case, task, sources)
    failure = next(f for f in row["failures"] if f.get("check") == "synthesis_contract")
    assert failure["code"] == "QUOTE_NOT_VERBATIM"
    assert failure["validation_checks"] == [{"rule": "verbatim", "path": ["findings", 0, "text"]}]
    assert "PRIVATE SENTINEL" not in str(failure)
    assert row["new_model_calls"] == row["estimated_cost_cny"] == 0
