"""Offline correctness, semantic abstention, provenance and durable API isolation."""
import copy
import json
from pathlib import Path

import pytest
from app.config import Settings
from app.models import DocumentRevision
from app.research_evaluation_schemas import Benchmark, ResearchCase
from app.services import research
from app.services.research_evaluation import digest, evaluate_research
from pydantic import ValidationError
from test_api import create_kb, register, upload, wait_job
from test_research import create, execute, get

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "evaluation/research-quality"


def sample(cid="RQ-D04"):
    if not (DATA / "packets.json").is_file():
        pytest.skip("Private historical evaluation packets are not distributed")
    benchmark = json.loads((DATA / "benchmark.json").read_text(encoding="utf8"))
    packet = json.loads((DATA / "packets.json").read_text(encoding="utf8"))[cid]
    return next(c for c in benchmark["cases"] if c["id"] == cid), packet["task"], packet["sources"]


def evaluate(case, task, sources, **kw):
    return evaluate_research(case, task, sources, benchmark_version="research-benchmark-v1", **kw)


def test_benchmark_validation_frozen_identity_and_ground_truth_boundary():
    if not (DATA / "freeze.json").is_file():
        pytest.skip("Private historical benchmark freeze is not distributed")
    b = json.loads((DATA / "benchmark.json").read_text(encoding="utf8"))
    frozen = json.loads((DATA / "freeze.json").read_text(encoding="utf8"))
    assert digest(b) == frozen["benchmark_sha256"]
    assert len(Benchmark.model_validate(b).cases) == 11
    for mutate in (lambda c: c.update(provenance="model_generated"),
                   lambda c: c.update(provenance="human_confirmed"),
                   lambda c: c["anchors"][0].update(document_name="outside.txt"),
                   lambda c: c["anchors"].append(c["anchors"][0]),
                   lambda c: c.update(expected_conflicts=[["a1", "missing"]])):
        case, _, _ = sample()
        mutate(case)
        with pytest.raises(ValidationError):
            ResearchCase.model_validate(case)
    b["cases"].append(b["cases"][0])
    with pytest.raises(ValidationError):
        Benchmark.model_validate(b)


def test_exact_metrics_keep_semantic_quality_pending():
    row = evaluate(*sample())
    m = row["metrics"]
    for name in ("evidence_coverage", "citation_validity", "fact_support"):
        assert m[name]["numerator"] == m[name]["denominator"] == 1
        assert m[name]["score"] == 1
    assert m["fact_support"]["units"][0]["support_scope"] == "textual_attribution"
    assert m["task_completion"]["score"] is None and m["task_completion"]["pending"] == 1
    assert m["result_consistency"]["denominator"] is None
    assert row["quality_status"] == "尚未完成质量验证"
    assert row["new_model_calls"] == row["estimated_cost_cny"] == 0


def test_mixed_semantics_do_not_report_supported_subset_as_full_accuracy():
    row = evaluate(*sample("RQ-D01"))
    m = row["metrics"]["fact_support"]
    assert m["denominator"] == 8 and m["numerator"] == 6 and m["pending"] == 2
    assert m["score"] is None and m["reviewed_subset_score"] == 1
    assert row["engineering"]["status"] == "passed"
    assert row["model_config"]["historical_configuration"] == "not_recorded"


@pytest.mark.parametrize("field", ["revision_id", "document_id", "knowledge_base_id", "locator", "page"])
def test_source_identity_tampering_fails_and_traces(field):
    case, task, sources = sample()
    task["evidence"][0]["origins"][0][field] = "wrong"
    row = evaluate(case, task, sources)
    assert row["metrics"]["citation_validity"]["score"] == 0
    assert row["engineering"]["status"] == "failed"
    assert any(c.get("evidence_id") == "e1" for c in row["failures"])


def test_unavailable_source_and_anchor_version_are_unknown_not_success():
    case, task, sources = sample()
    row = evaluate(case, task, {})
    assert row["metrics"]["citation_validity"]["score"] is None
    assert row["source_attribution"][0]["verdict"] == "unknown"
    case["anchors"][0]["source_sha256"] = "0" * 64
    row = evaluate(case, task, sources)
    assert row["metrics"]["evidence_coverage"]["score"] is None


def test_missing_anchor_is_zero_but_empty_denominators_not_100():
    case, task, sources = sample()
    case["anchors"][0]["quote"] = "不存在的准确原文锚点"
    assert evaluate(case, task, sources)["metrics"]["evidence_coverage"]["score"] == 0
    row = evaluate(*sample("RQ-D05"))
    for name in ("citation_validity", "evidence_coverage", "fact_support"):
        assert row["metrics"][name]["score"] is None
        assert row["metrics"][name]["status"] == "not_applicable"


def test_synthetic_conflict_detection_uses_fixed_pairs_and_reports_miss():
    case, task, sources = sample("RQ-D06")
    assert evaluate(case, task, sources)["metrics"]["conflict_identification"]["score"] == 1
    task["result"]["conflicts"] = []
    assert evaluate(case, task, sources)["metrics"]["conflict_identification"]["score"] == 0


def test_invalid_binding_and_duplicate_ids_cannot_pass():
    case, task, sources = sample()
    task["result"]["findings"][0]["evidence_ids"] = ["e9"]
    row = evaluate(case, task, sources)
    assert row["metrics"]["citation_validity"]["score"] == 0
    assert row["metrics"]["fact_support"]["score"] == 0
    task["evidence"].append(task["evidence"][0])
    assert evaluate(case, task, sources)["engineering"]["status"] == "failed"


def test_same_snapshot_replay_is_deterministic_except_timestamp():
    one, two = evaluate(*sample()), evaluate(*sample())
    one.pop("executed_at")
    two.pop("executed_at")
    assert one == two
    assert one["metrics"]["result_consistency"]["score"] is None


def test_human_review_has_version_basis_locations_and_stale_rejection():
    case, task, sources = sample()
    review = {"method": "human", "reviewer": "test fixture reviewer (simulated)", "reviewed_at": "2026-10-30T00:00:00Z",
        "annotation_version": "review-test-v1", "task_sha256": digest(task), "case_sha256": digest(ResearchCase.model_validate(case).model_dump()),
        "source_snapshot_sha256": digest(sources),
        "decisions": [{"metric": "task_completion", "unit_id": "point.0", "verdict": "fail",
                       "rationale": "模拟人工输入通道测试，不是真实人工标注", "evidence_locations": ["leave.txt:L1"]}]}
    row = evaluate(case, task, sources, review=review)
    assert row["metrics"]["task_completion"]["score"] == 0
    task["question"] += "请说明。"
    case["question"] = task["question"]
    with pytest.raises(ValueError, match="Stale review"):
        evaluate(case, task, sources, review=review)
    review["method"] = "llm"
    with pytest.raises(ValidationError):
        evaluate(*sample(), review=review)


def test_rejects_mismatched_question_restricted_and_live_task():
    case, task, sources = sample()
    for patch in ({"question": "不同的问题无法冒充历史研究"}, {"restricted": True}, {"status": "running"}):
        with pytest.raises(ValueError):
            evaluate(case, {**task, **patch}, sources)


def test_independent_consistency_requires_comparable_runs_and_human_review():
    case, task, sources = sample()
    peer = copy.deepcopy(task)
    peer["id"] = "another-execution"
    row = evaluate(case, task, sources, comparison_task=peer)
    assert row["metrics"]["result_consistency"]["pending"] == 1
    assert row["metrics"]["result_consistency"]["denominator"] == 1
    assert row["metrics"]["result_consistency"]["score"] is None
    peer["usage"]["configuration"]["model"] = "different-model"
    with pytest.raises(ValueError, match="Independent-run comparison"):
        evaluate(case, task, sources, comparison_task=peer)


def test_kind_boundary_prevents_synthesis_masquerading_as_original():
    case, task, sources = sample("RQ-D07")
    row = evaluate(case, task, sources)
    assert row["engineering"]["status"] == "failed"
    base = task["result"]
    data = {k: base[k] for k in ("findings", "analysis", "limitations", "follow_up_questions")}
    with pytest.raises(ValueError, match="Findings must"):
        research.validate_synthesis(data, task["evidence"], task["steps"], Settings(), [])
    data = copy.deepcopy(data)
    data["findings"][0].update(kind="evidence", text=task["evidence"][0]["text"])
    data["analysis"] = [copy.deepcopy(data["findings"][0])]
    with pytest.raises(ValueError, match="Findings must"):
        research.validate_synthesis(data, task["evidence"], task["steps"], Settings(), [])


def test_api_offline_history_persistence_owner_and_revocation(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    client = request.getfixturevalue("client")
    owner, other = register(client), register(client, "other-evaluation@example.com")
    kb = create_kb(client, owner)
    wait_job(client, owner, upload(client, owner, kb, "员工每年享有十五天带薪年假。".encode(), "leave.txt")["job_id"])
    tid = create(client, owner, kb, "员工年假政策如何规定？").json()["id"]
    execute(client)
    task = get(client, owner, tid)
    assert task["usage"]["configuration"]["execution_sha256"]
    case, _, _ = sample()
    e = task["evidence"][0]
    with client.app.state.database.session() as db:
        case["anchors"][0]["source_sha256"] = db.get(DocumentRevision, e["revision_id"]).sha256.lower()
    body = {"name": "离线研究复评", "task_id": tid, "case": case, "benchmark_version": "test-v1"}
    monkeypatch.setattr(research, "configured_llm", lambda: pytest.fail("Offline evaluator must never generate"))
    monkeypatch.setattr(research, "retrieve", lambda *a, **kw: pytest.fail("Offline evaluator must not retrieve"))
    assert client.post("/api/research-evaluations", headers=other, json=body).status_code == 404
    response = client.post("/api/research-evaluations", headers=owner, json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["results"][0]["engineering"]["status"] == "passed"
    assert run["results"][0]["metrics"]["citation_validity"]["score"] == 1
    assert run["metrics"]["new_model_calls"] == 0
    path = "/api/evaluations/" + run["id"]
    assert client.get(path, headers=other).status_code == 404
    assert client.get(path, headers=owner).json() == run
    assert any(r["id"] == run["id"] for r in client.get("/api/evaluations", headers=owner).json())
    assert get(client, owner, tid) == task
    assert client.delete("/api/knowledge-bases/" + kb, headers=owner).status_code == 204
    hidden = client.get(path, headers=owner).json()
    assert hidden["status"] == "restricted" and not hidden["results"]
    assert client.post("/api/research-evaluations", headers=owner, json=body).status_code == 403
