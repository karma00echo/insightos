"""Offline regression; historical evidence is not the missing historical model output."""
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from app.config import Settings
from app.models import ResearchTask
from app.services import research
from app.services.research_errors import ResearchFailure
from app.services.research_retrieval import accepted_candidates, relevant_body, scoped_sources
from app.services.retrieval import rerank_candidates
from app.services.verification import verify_answer
from openai import APITimeoutError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_api import create_kb, register, upload, wait_job
from test_research import candidate, create, execute, get

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def manual_client(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    return request.getfixturevalue("client")


def synthesis(text="员工每年享有十五天带薪年假。"):
    return {"findings": [{"kind": "evidence", "text": text, "evidence_ids": ["e1"], "step_ids": ["s1"]}],
            "analysis": [], "limitations": [], "follow_up_questions": []}


def transcript(text="员工每年享有十五天带薪年假。"):
    evidence = []
    research.add_evidence(evidence, candidate(text), "s1")
    return evidence, [{"id": "s1", "evidence_ids": ["e1"]}]


@pytest.mark.parametrize("change,code", [
    ({"evidence_ids": ["e999"]}, "EVIDENCE_ID_UNKNOWN"),
    ({"evidence_ids": ["e1", "e1"]}, "EVIDENCE_ID_DUPLICATE"),
    ({"step_ids": ["s9"]}, "STEP_ID_UNKNOWN"),
    ({"text": "员工每年享有九十天带薪年假。"}, "QUOTE_NOT_VERBATIM"),
    ({"kind": "synthesis"}, "FINDING_KIND_INVALID"),
])
def test_typed_validation_rejections(change, code):
    evidence, steps = transcript()
    raw = synthesis()
    raw["findings"][0].update(change)
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert caught.value.code == code and caught.value.checks


def test_multiline_finding_binding_does_not_change_plain_chat_citation_rules():
    text = "员工每年享有十五天带薪年假。\n\n请假需要提前三天提交申请。"
    evidence, steps = transcript(text)
    raw = synthesis(text)
    original = copy.deepcopy(raw)
    assert verify_answer(text + " [1]", evidence, Settings())["issues"] == ["uncited_claim"]
    result = research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert result["findings"][0]["verification"]["passed"]
    assert result["findings"][0]["verification"]["semantic_checked"] is False
    assert result["findings"][0]["text"] == text and raw == original
    assert not verify_answer(text, evidence, Settings())["passed"]
    assert not verify_answer("员工年假十五天。[99]", evidence, Settings())["passed"]


def test_cross_paragraph_quote_cannot_borrow_another_sources_binding():
    evidence, steps = transcript("员工年假十五天。\n\n请假需要提前三天。")
    research.add_evidence(evidence, candidate("员工住宿上限每晚500元。", "c2", "d2"), "s1")
    raw = synthesis("员工年假十五天。\n\n员工住宿上限每晚500元。")
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert caught.value.code == "QUOTE_NOT_VERBATIM"


def test_binding_gap_conflict_numeric_and_coverage_remain_fail_closed():
    evidence, steps = transcript()
    steps.append({"id": "s2", "evidence_ids": [], "gap": "资料不足"})
    assert research.validate_synthesis(synthesis(), evidence, steps, Settings(), [])
    analysis = {"kind": "synthesis", "text": "员工年假为900天。\n\n应提前三天申请。", "evidence_ids": ["e1"], "step_ids": ["s2"]}
    raw = synthesis()
    raw["analysis"] = [analysis]
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert caught.value.code == "STEP_EVIDENCE_MISMATCH"
    evidence[0]["step_ids"].append("s2")
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert caught.value.code == "EVIDENCE_GAP"
    raw["analysis"][0]["step_ids"] = ["s1"]
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [{"evidence_ids": ["e1"]}])
    assert caught.value.code == "CONFLICT_UNRESOLVED"
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(raw, evidence, steps, Settings(), [])
    assert caught.value.code == "NUMERIC_MISMATCH"
    research.add_evidence(evidence, candidate("请假需要提前三天申请。", "c2", "d2"), "s2")
    steps[1]["evidence_ids"] = ["e2"]
    with pytest.raises(ResearchFailure) as caught:
        research.validate_synthesis(synthesis(), evidence, steps, Settings(), [])
    assert caught.value.code == "COVERAGE_INCOMPLETE"


def test_historical_multiline_evidence_fixture_now_validates():
    if not (ROOT / "evaluation/end-to-end-diagnosis/failed-tasks.json").is_file():
        pytest.skip("Private archived failure tasks are not distributed")
    tasks = json.loads((ROOT / "evaluation/end-to-end-diagnosis/failed-tasks.json").read_text(encoding="utf8"))
    task = tasks[-1]
    # This is a new local extractive projection, not a replay of missing LLM output.
    before = copy.deepcopy(task)
    result = research.extractive_result(task["evidence"], task["steps"], Settings(), research.conflicts(task["evidence"]))
    assert len(result["findings"]) == 8 and not result["analysis"]
    assert all(f["verification"]["passed"] and not f["verification"]["semantic_checked"] for f in result["findings"])
    assert task == before and task["status"] == "failed" and task["result"] is None


def test_source_scoping_fixes_static_historical_crowding_without_unrelated_admission():
    from app.models import User
    from app.services.corpus import CorpusScope

    path = ROOT / "agent_app/artifacts/rag-quality/runtime/rag.db"
    if not path.is_file():
        pytest.skip("Private historical runtime database is not distributed")
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True))
    with Session(engine) as db:
        task = db.get(ResearchTask, "931ca3ce-d98e-4196-81fe-352696425ce8")
        corpus = CorpusScope(db, db.get(User, task.user_id), task.knowledge_base_ids)
        ids = [r[0] for r in db.execute(text("select id from chunks order by id"))]
        pool = list(corpus.hydrate(ids).values())
        question = task.steps[1]["question"]
        old = rerank_candidates(question, pool, 8, use_model=False)
        assert len(old) == 8 and not any("Qdrant" in c["document_name"] for c in old)
        scoped = scoped_sources(corpus, ["Qdrant"])
        narrowed = list(scoped.hydrate(ids).values())
        assert len(narrowed) == 29 and len(pool) == 76
        selected = rerank_candidates(question, narrowed, 8, use_model=False)
        accepted = accepted_candidates(question, selected, ["Qdrant"], .25)
        assert accepted and all("Qdrant" in c["document_name"] for c in accepted)
        assert scoped is not corpus and len(corpus) == 76
    engine.dispose()


@pytest.mark.parametrize("text", ["# Qdrant\n\n餐厅的甜点非常美味，交通和停车也很方便。",
    "# Qdrant Hybrid Search\n\n这是一段与混合检索和重排无关的菜谱。",
    "# Qdrant\n\n**来源机构：** Qdrant\n\nThis recipe can help you manage a meal and store food safely.",
    "# Qdrant\n\n```python\nfrom qdrant_client import QdrantClient\nclient = QdrantClient()\n```",
    "# Qdrant Search\n\nInstallation\npip install qdrant-client"])
def test_product_titles_and_code_do_not_force_role_evidence(text):
    assert not relevant_body("知识库中关于 Qdrant 在 AI Research Agent 中职责的描述有哪些？",
        {"text": text, "document_name": "Qdrant_guide.md"}, ["Qdrant"], .25)


def test_specific_product_intent_needs_body_terms_not_only_name():
    q = "Qdrant 混合检索如何工作？"
    assert relevant_body(q, {"text": "# Qdrant\n\nDense embeddings and BM25 feed hybrid search to retrieve candidate documents.", "document_name": "Qdrant.md"}, ["Qdrant"], .25)
    assert not relevant_body(q, {"text": "# Qdrant Hybrid Search\n\nQdrant stores a list of office addresses for staff.", "document_name": "Qdrant.md"}, ["Qdrant"], .25)


def prepared(c, h):
    kb = create_kb(c, h)
    wait_job(c, h, upload(c, h, kb, "员工每年享有十五天带薪年假。".encode(), "leave.md")["job_id"])
    tid = create(c, h, kb, "员工年假制度有哪些规定？").json()["id"]
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        task.plan = {**research.rule_plan(task.question).model_dump(), "planner": "rules"}
        db.commit()
    return tid


def mock_replies(monkeypatch, replies):
    calls = []
    class Fake:
        def model_copy(self, **kwargs):
            assert kwargs["update"]["max_retries"] == 0
            return self

        def invoke(self, messages):
            calls.append(json.loads(messages[-1].content))
            item = replies.pop(0)
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(content=item if isinstance(item, str) else json.dumps(item, ensure_ascii=False),
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                                "api_key": "usage-secret-DO-NOT-LOG"}, response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(research, "llm_ready", lambda: True)
    monkeypatch.setattr(research, "configured_llm", lambda: Fake())
    return calls


@pytest.mark.parametrize("payload,code,kind", [("secret-bad-json", "OUTPUT_JSON_INVALID", "JSONDecodeError"),
    ({"secret-extra-field": "private-source-value"}, "OUTPUT_SCHEMA_INVALID", "ValidationError")])
def test_persisted_diagnostics_are_precise_bounded_and_redacted(manual_client, monkeypatch, caplog, payload, code, kind):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    calls = mock_replies(monkeypatch, [payload, payload])
    execute(c)
    task = get(c, h, tid)
    detail = task["error_details"]
    assert task["status"] == "failed" and detail["code"] == code and detail["error_type"] == kind
    assert detail["task_id"] == tid and detail["at"] and detail["context"]["evidence_groups"] == 1
    assert len(calls) == 2 and task["usage"]["model_calls"] == 2 and task["usage"]["input_tokens"] == 20
    assert calls[1]["correction"]["code"] == code
    safe = json.dumps({"diagnostic": detail, "usage": task["usage"], "events": task["events"]}) + caplog.text
    for secret in ("secret-bad-json", "secret-extra-field", "private-source-value", "usage-secret-DO-NOT-LOG"):
        assert secret not in safe
    assert all(call["response_summary"]["finish_reason"] == "stop" for call in task["usage"]["calls"])
    assert get(c, h, tid)["error_details"] == detail
    assert any(e.get("diagnostic", {}).get("code") == code and e["phase"] == "synthesizing" for e in task["events"])


def test_corrected_mock_preserves_diagnostic_and_publishes_only_valid_result(manual_client, monkeypatch):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    calls = mock_replies(monkeypatch, [synthesis("伪造原文不允许发布。"), synthesis()])
    execute(c)
    task = get(c, h, tid)
    assert len(calls) == 2 and task["status"] == "completed" and task["error_details"] is None
    assert task["result"]["findings"][0]["text"] == synthesis()["findings"][0]["text"]
    assert any(e.get("diagnostic", {}).get("code") == "QUOTE_NOT_VERBATIM" for e in task["events"])
    assert calls[1]["correction"]["checks"][0]["path"] == ["findings", 0, "text"]


@pytest.mark.parametrize("error,code", [(TimeoutError("provider-secret"), "MODEL_TIMEOUT"),
                                       (APITimeoutError(request=httpx.Request("POST", "https://api.deepseek.com")), "MODEL_TIMEOUT"),
                                       (RuntimeError("api-key-secret"), "MODEL_REQUEST_FAILED")])
def test_provider_failures_are_not_retried_and_do_not_leak(manual_client, monkeypatch, caplog, error, code):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    calls = mock_replies(monkeypatch, [error])
    execute(c)
    task = get(c, h, tid)
    assert len(calls) == 1 and task["error_details"]["code"] == code
    assert task["result"] is None and task["usage"]["calls"][0]["status"] == "attempted"
    assert str(error) not in json.dumps(task) + caplog.text


@pytest.mark.parametrize("stage,exc,code", [("retrieving", RuntimeError("secret"), "RETRIEVAL_FAILED"),
    ("publishing", SQLAlchemyError("secret"), "PERSISTENCE_FAILED"), ("verifying", RuntimeError("secret"), "EXECUTION_FAILED")])
def test_unexpected_failure_stage_and_redaction(manual_client, monkeypatch, stage, exc, code):
    c, h = manual_client, register(manual_client)
    tid = create(c, h).json()["id"]
    def fail(self):
        self.stage = stage
        raise exc
    monkeypatch.setattr(research.Runner, "run", fail)
    execute(c)
    task = get(c, h, tid)
    assert task["error_details"]["stage"] == stage and task["error_details"]["code"] == code
    assert "secret" not in json.dumps(task)


def test_diagnostic_is_hidden_after_source_access_revocation(manual_client, monkeypatch):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    mock_replies(monkeypatch, ["bad", "bad"])
    execute(c)
    task = get(c, h, tid)
    assert task["error_details"]
    assert c.delete('/api/knowledge-bases/' + task['knowledge_base_ids'][0], headers=h).status_code == 204
    restricted = get(c, h, tid)
    assert restricted["restricted"] and restricted["error_details"] is None and restricted["events"] == []
