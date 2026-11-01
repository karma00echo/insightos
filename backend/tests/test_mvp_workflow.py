"""Mock end-to-end workflow, safe writing rejection and durable publication fences."""
import copy
import json
from types import SimpleNamespace

import pytest
from app.models import ResearchReport, ResearchTask, new_id
from app.report_schemas import WrittenReport
from app.services import answering, reports, research
from sqlalchemy import select
from test_api import create_kb, register, upload, wait_job
from test_research import execute, get


@pytest.fixture
def workflow_client(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    return request.getfixturevalue("client")


def written(eid="e1", sid="s1"):
    headings = ["研究摘要", "问题背景", "主要发现", "机制分析", "适用条件", "研究结论"]
    texts = [f"{h}：现有资料描述员工休假政策，并将休假权益与申请要求列为需要分别理解的内容。"
             "这些信息为理解制度提供了直接依据，但只能用于当前资料的适用范围；"
             "不能据此推断所有组织采用相同规定，具体执行仍应核对来源和审批条件。" for h in headings]
    def p(i):
        return {"text": texts[i], "evidence_ids": [eid], "step_ids": [sid]}
    return {"sections": [{"id": key, "paragraphs": [p(i)] if key != "discussion" else [p(3), p(4)]}
        for key, i in zip(["abstract", "background", "findings", "discussion", "conclusion"], [0, 1, 2, 3, 5], strict=True)]}


def setup(c, h):
    kb = create_kb(c, h)
    wait_job(c, h, upload(c, h, kb, "员工每年享有十五天带薪年假。请假需提前三天提交申请。".encode(), "leave.md")["job_id"])
    body = {"question": "员工年假制度有哪些要求？", "knowledge_base_ids": [kb], "request_id": new_id()}
    response = c.post("/api/research", headers=h, json=body)
    assert response.status_code == 202, response.text
    return response.json()["id"], body


def mock_model(monkeypatch, transform=None, on_writing=None):
    calls = []
    class Model:
        def model_copy(self, update):
            self.options = update
            return self
        def invoke(self, messages):
            payload = json.loads(messages[1].content)
            calls.append((messages[0].content, payload, self.options))
            if "研究规划：" in messages[0].content:
                data = {"objective": payload["question"], "steps": [{"id": "s1", "question": payload["question"],
                    "expected_output": "休假规定和证据适用条件"}]}
            elif "报告写作：" in messages[0].content:
                if on_writing:
                    on_writing()
                data = written(payload["sources"][0]["id"])
                if transform:
                    data = transform(data)
            else:
                e = payload["sources"][0]
                data = {"findings": [{"kind": "evidence", "text": e["text"], "evidence_ids": [e["id"]],
                    "step_ids": ["s1"]}], "analysis": [], "limitations": ["仅限当前资料范围"], "follow_up_questions": []}
            return SimpleNamespace(content=json.dumps(data, ensure_ascii=False), usage_metadata={
                "input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
    monkeypatch.setattr(research, "configured_llm", lambda: Model())
    monkeypatch.setattr(research, "llm_ready", lambda: True)
    monkeypatch.setattr(answering, "llm_ready", lambda: True)
    return calls


def test_one_request_research_writing_version_read_export_and_evaluation_compatibility(workflow_client, monkeypatch):
    c, h = workflow_client, register(workflow_client)
    calls = mock_model(monkeypatch)
    tid, body = setup(c, h)
    assert c.post("/api/research", headers=h, json=body).json()["id"] == tid
    execute(c)
    task = get(c, h, tid)
    assert task["status"] == "completed" and task["report_workflow"]["status"] == "saved", task
    assert len(calls) == task["usage"]["model_calls"] == 3
    assert task["usage"]["input_tokens"] == 30 and task["usage"]["output_tokens"] == 60
    assert calls[-1][2]["max_retries"] == 0
    report_id = task["report_workflow"]["report_id"]
    saved = c.get("/api/research-reports/" + report_id, headers=h).json()
    assert saved["content"]["generation"]["method"] == "llm_evidence_writing"
    assert saved["content"]["generation"]["model_calls"] == 1
    assert saved["current_validation"]["status"] == "VALID"
    assert saved["content"]["quality_status"] == "QUALITY_PENDING_HUMAN_REVIEW"
    generated = [p for p in saved["content"]["claims"] if p["id"].startswith("w")]
    assert len(generated) == 6 and all(p["quality_status"] == "NEEDS_REVIEW" for p in generated)
    assert len({p["text"] for p in generated}) == 6
    path = "/api/research-reports/" + report_id
    exported = c.get(path + "/export", headers=h)
    assert exported.status_code == 200 and "llm_evidence_writing" in exported.text
    assert "机制分析" in exported.text and "适用条件" in exported.text
    assert c.get(path, headers=h).json()["content_sha256"] == saved["content_sha256"]
    execute(c)  # Completed jobs are never executed again.
    assert len(c.get("/api/research-reports?task_id=" + tid, headers=h).json()) == 1 and len(calls) == 3
    from app.research_evaluation_schemas import ResearchCase
    from app.services.research_evaluation import evaluate_research
    case = ResearchCase(id="mvp-mock", split="development", scenario="synthetic workflow",
        question=body["question"], material_scope=["leave.md"], provenance="synthetic_fixture",
        annotation_version="test", annotated_by="codex simulated fixture", expected_points=["说明制度"],
        anchors=[], rubric=["Mock工程测试，非人工正确答案"], failure_conditions=["不能编造来源"])
    result = evaluate_research(case.model_dump(), task, {}, benchmark_version="mock-only")
    assert result["metrics"]["task_completion"]["score"] is None
    assert result["quality_status"] == "尚未完成质量验证"


@pytest.mark.parametrize("mutation", ["unknown", "number", "duplicate", "gap", "structure"])
def test_invalid_report_never_publishes_and_successful_research_survives(workflow_client, monkeypatch, mutation):
    c, h = workflow_client, register(workflow_client)
    def transform(data):
        p = data["sections"][0]["paragraphs"][0]
        if mutation == "unknown":
            p["evidence_ids"] = ["e999"]
        elif mutation == "number":
            p["text"] += "每位员工可休假999天。"
        elif mutation == "gap":
            p["step_ids"] = ["s4"]
        elif mutation == "duplicate":
            data["sections"][1]["paragraphs"][0] = copy.deepcopy(p)
        else:
            data["sections"].pop()
        return data
    calls = mock_model(monkeypatch, transform)
    tid, _ = setup(c, h)
    execute(c)
    task = get(c, h, tid)
    assert task["status"] == "completed" and task["result"]["findings"]
    assert task["report_workflow"]["status"] == "failed"
    assert not c.get("/api/research-reports?task_id=" + tid, headers=h).json()
    assert len(calls) == 3  # No retry or silent extractive fallback.


def test_cancellation_during_writing_fences_report_and_preserves_usage(workflow_client, monkeypatch):
    c, h = workflow_client, register(workflow_client)
    tid, _ = setup(c, h)
    calls = mock_model(monkeypatch, on_writing=lambda: c.post("/api/research/" + tid + "/cancel", headers=h))
    execute(c)
    task = get(c, h, tid)
    assert task["status"] == "cancelled" and task["result"]["findings"]
    assert task["usage"]["model_calls"] == len(calls) == 3
    assert not c.get("/api/research-reports?task_id=" + tid, headers=h).json()


def test_offline_mode_auto_saves_honest_deterministic_report(workflow_client):
    c, h = workflow_client, register(workflow_client)
    tid, _ = setup(c, h)
    execute(c)
    task = get(c, h, tid)
    assert task["report_workflow"]["status"] == "saved"
    report = c.get("/api/research-reports/" + task["report_workflow"]["report_id"], headers=h).json()
    assert report["content"]["generation"]["method"] == "deterministic"
    assert report["content"]["generation"]["model_calls"] == task["usage"]["model_calls"] == 0
    assert "模型未配置" in report["content"]["generation"]["reason"]


def test_report_publication_failure_is_atomic(workflow_client, monkeypatch):
    c, h = workflow_client, register(workflow_client)
    tid, _ = setup(c, h)
    original = reports.save_version
    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("private-token-not-to-leak")
    monkeypatch.setattr(reports, "save_version", fail_after_insert)
    execute(c)
    task = get(c, h, tid)
    assert task["report_workflow"]["status"] == "failed" and task["status"] == "completed"
    assert "private-token" not in str(task)
    with c.app.state.database.session() as db:
        assert not list(db.scalars(select(ResearchReport).where(ResearchReport.task_id == tid)))
        assert db.get(ResearchTask, tid).result


def test_written_outline_cannot_be_only_copied_findings():
    assert WrittenReport.model_validate(written())
    bad = written()
    bad["sections"][3]["paragraphs"] = [bad["sections"][0]["paragraphs"][0]]
    with pytest.raises(ValueError):
        WrittenReport.model_validate(bad)


def test_source_revocation_during_writing_prevents_publication(workflow_client, monkeypatch):
    from app.models import KnowledgeBase, User
    c, h = workflow_client, register(workflow_client)
    other = register(c, "other-workflow@example.com")
    tid, _ = setup(c, h)
    def revoke():
        with c.app.state.database.session() as db:
            task = db.get(ResearchTask, tid)
            kb = db.get(KnowledgeBase, task.knowledge_base_ids[0])
            kb.visibility = "restricted"
            kb.user_id = db.scalar(select(User.id).where(User.email == "other-workflow@example.com"))
            db.commit()
    mock_model(monkeypatch, on_writing=revoke)
    execute(c)
    task = get(c, h, tid)
    assert task["restricted"] and task["result"] is None
    assert not c.get("/api/research-reports", headers=h).json()
    assert not c.get("/api/research-reports", headers=other).json()
    with c.app.state.database.session() as db:
        assert not list(db.scalars(select(ResearchReport).where(ResearchReport.task_id == tid)))


@pytest.mark.parametrize("boundary", ["gap", "conflict"])
def test_gap_or_conflict_evidence_cannot_be_used_for_llm_writing(workflow_client, monkeypatch, boundary):
    from app.services import report_writing
    from test_reports import seed
    c, h = workflow_client, register(workflow_client)
    tid = seed(c, h, ["员工住宿报销上限为每晚500元。", "员工住宿报销上限为每晚800元。"] if boundary == "conflict" else ["员工享有年假。"])
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        if boundary == "gap":
            steps = copy.deepcopy(task.steps)
            steps[0]["gap"] = "已确认资料范围不全，不允许综合"
            task.steps = steps
            db.commit()
        task = copy.deepcopy(task)
    monkeypatch.setattr(answering, "llm_ready", lambda: True)
    runner = SimpleNamespace(database=c.app.state.database, settings=c.app.state.settings, check=lambda: task,
        call=lambda *a, **kw: pytest.fail("Unsafe evidence must not be sent to report writing"))
    content = report_writing.generate(runner)
    assert content["generation"]["method"] == "deterministic"
    assert content["generation"]["model_calls"] == 0


def test_report_timeout_does_not_retry_or_publish_fake_success(workflow_client, monkeypatch):
    c, h = workflow_client, register(workflow_client)
    tid, _ = setup(c, h)
    calls = mock_model(monkeypatch, on_writing=lambda: (_ for _ in ()).throw(TimeoutError("private timeout body")))
    execute(c)
    task = get(c, h, tid)
    assert task["status"] == "completed" and task["report_workflow"]["status"] == "failed"
    assert task["report_workflow"]["code"] == "MODEL_TIMEOUT" and len(calls) == 3
    assert "private timeout" not in str(task)
    assert not c.get("/api/research-reports?task_id=" + tid, headers=h).json()
