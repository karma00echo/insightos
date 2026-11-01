"""No paid calls: contracts, real retrieval, authorization, fencing and failure cases."""
import copy
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.models import ResearchTask, new_id, utcnow
from app.research_schemas import ResearchPlan
from app.services import research
from pydantic import ValidationError
from sqlalchemy import select
from test_api import create_kb, register, upload, wait_job


@pytest.fixture
def manual_client(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    return request.getfixturevalue("client")


def create(client, headers, kb=None, question="员工年假有几天？请假需要提前几天申请？", key=None):
    return client.post("/api/research", headers=headers, json={"question": question,
        "knowledge_base_ids": [kb] if kb else [], "request_id": key or new_id(), "generate_report": False})


def execute(client):
    research.tick(client.app.state.database, client.app.state.settings)


def get(client, headers, task_id):
    response = client.get("/api/research/" + task_id, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def candidate(text, cid="c1", doc="d1"):
    return {"id": cid, "text": text, "document_id": doc, "document_name": doc + ".md",
            "knowledge_base_id": "kb", "revision_id": "r1", "locator": {"line_start": 2, "line_end": 3}, "page": 1}


def test_plan_contract_and_deterministic_order():
    plan = research.rule_plan("员工年假有几天？请假需要提前几天申请？")
    assert len(plan.steps) == 2
    data = plan.model_dump()
    for bad in [{**data, "extra": "bad"}, {**data, "steps": data["steps"] * 3},
                {**data, "steps": [{**data["steps"][0], "id": "s2"}]},
                {**data, "steps": [data["steps"][0], {**data["steps"][0], "id": "s2"}]}]:
        with pytest.raises(ValidationError):
            ResearchPlan.model_validate(bad)


def test_dedup_preserves_questions_origins_and_numeric_conflicts():
    evidence = []
    research.add_evidence(evidence, candidate("员工住宿报销上限为每晚500元。"), "s1")
    research.add_evidence(evidence, candidate("员工住宿报销上限为每晚500元。", "copy", "copy"), "s2")
    assert len(evidence) == 1 and evidence[0]["step_ids"] == ["s1", "s2"]
    assert len(evidence[0]["origins"]) == 2
    research.add_evidence(evidence, candidate("员工住宿报销上限为每晚800元。", "c2", "d2"), "s1")
    assert len(evidence) == 2
    assert research.conflicts(evidence)[0]["evidence_ids"] == ["e1", "e2"]


def test_synthesis_rejects_fabrication_binding_and_conflict():
    from app.config import Settings
    evidence = []
    research.add_evidence(evidence, candidate("员工住宿报销上限为每晚500元。"), "s1")
    steps = [{"id": "s1", "evidence_ids": ["e1"]}]
    data = {"findings": [{"kind": "evidence", "text": evidence[0]["text"], "evidence_ids": ["e1"], "step_ids": ["s1"]}],
            "analysis": [], "limitations": [], "follow_up_questions": []}
    assert research.validate_synthesis(data, evidence, steps, Settings(), [])["findings"]
    for changes in [{"evidence_ids": ["e9"]}, {"step_ids": ["s2"]}, {"text": "员工住宿报销上限为每晚900元。"},
                    {"kind": "synthesis", "text": "员工住宿报销上限为每晚900元。"}]:
        bad = copy.deepcopy(data)
        bad["findings"][0].update(changes)
        with pytest.raises(ValueError):
            research.validate_synthesis(bad, evidence, steps, Settings(), [])
    data["findings"][0]["kind"] = "synthesis"
    with pytest.raises(ValueError):
        research.validate_synthesis(data, evidence, steps, Settings(), [{"evidence_ids": ["e1"]}])


def test_real_retrieval_multi_document_gaps_persistence(manual_client):
    c = manual_client
    h = register(c)
    kb = create_kb(c, h)
    for text, name in [("员工每年享有十五天带薪年假。", "leave.md"), ("请假需要提前三天提交申请。", "request.md")]:
        wait_job(c, h, upload(c, h, kb, text.encode(), name)["job_id"])
    response = create(c, h, kb, "员工年假有几天？请假需要提前几天申请？月球采矿许可证如何办理？")
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "completed", task
    assert len(task["steps"]) == 3 and len(task["result"]["gaps"]) == 1
    assert task["result"]["source_documents"] == 2
    assert task["result"]["provider"] == "extractive" and task["usage"]["model_calls"] == 0
    assert all(s["status"] == "completed" for s in task["steps"])
    assert sum(len(s["rounds"]) for s in task["steps"]) <= 6
    assert get(c, h, task_id)["result"] == task["result"]
    assert c.get("/api/research", headers=h).json()[0]["id"] == task_id


def test_idempotency_concurrency_owner_cancel_and_scope(manual_client):
    c = manual_client
    owner, other = register(c), register(c, "other-research@example.com")
    kb = create_kb(c, owner)
    key = new_id()
    first = create(c, owner, kb, key=key)
    task_id = first.json()["id"]
    assert create(c, owner, kb, key=key).json()["id"] == task_id
    assert create(c, owner, kb).status_code == 409
    assert create(c, owner, kb, question="不同研究问题要单独研究", key=key).status_code == 409
    assert c.get("/api/research/" + task_id, headers=other).status_code == 404
    assert c.post("/api/research/" + task_id + "/cancel", headers=other).status_code == 404
    assert create(c, other, kb).status_code == 404
    cancelled = c.post("/api/research/" + task_id + "/cancel", headers=owner).json()
    assert cancelled["status"] == "cancelled"
    execute(c)
    assert get(c, owner, task_id)["status"] == "cancelled"
    assert create(c, owner, kb).status_code == 202


def test_expired_run_keeps_partial_evidence_and_releases_slot(manual_client):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, task_id)
        task.status, task.lease_token = "running", "old-lease"
        task.deadline_at = utcnow() - timedelta(seconds=1)
        task.evidence = [{"id": "e1", "text": "已保存的部分证据"}]
        task.steps = [{"id": "s1", "status": "running"}]
        db.commit()
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "failed" and task["evidence"][0]["id"] == "e1"
    assert task["steps"][0]["status"] == "failed"
    assert create(c, h).status_code == 202


def test_cancel_during_retrieval_fences_late_writes(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    def delayed(*args, **kwargs):
        assert c.post(f"/api/research/{task_id}/cancel", headers=h).json()["status"] == "cancelled"
        return [candidate("员工每年享有十五天带薪年假。")]
    monkeypatch.setattr(research, "retrieve", delayed)
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "cancelled" and task["result"] is None and not task["evidence"]
    assert task["steps"][0]["status"] == "cancelled"


def test_failure_is_sanitized_and_releases_slot(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    monkeypatch.setattr(research, "retrieve", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("secret-key-do-not-log")))
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "failed" and "secret-key" not in str(task)
    assert create(c, h).status_code == 202


def test_permission_revocation_hides_saved_research(manual_client):
    c = manual_client
    h = register(c)
    kb = create_kb(c, h)
    task_id = create(c, h, kb).json()["id"]
    execute(c)
    assert c.delete("/api/knowledge-bases/" + kb, headers=h).status_code == 204
    task = get(c, h, task_id)
    assert task["restricted"] and not task["evidence"] and task["result"] is None


def test_mock_model_plan_synthesis_usage_and_bounded_repair(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    kb = create_kb(c, h)
    wait_job(c, h, upload(c, h, kb, "员工每年享有十五天带薪年假。".encode(), "leave.md")["job_id"])
    monkeypatch.setattr(research, "llm_ready", lambda: True)
    import json
    replies = [research.rule_plan("员工年假有几天？").model_dump(),
        {"findings": [{"kind": "evidence", "text": "编造的连续原文", "evidence_ids": ["e1"], "step_ids": ["s1"]}],
         "analysis": [], "limitations": [], "follow_up_questions": []},
        {"findings": [{"kind": "evidence", "text": "员工每年享有十五天带薪年假。", "evidence_ids": ["e1"], "step_ids": ["s1"]}],
         "analysis": [], "limitations": [], "follow_up_questions": []}]
    class Fake:
        def model_copy(self, **kwargs):
            assert kwargs["update"]["max_retries"] == 0
            return self
        def invoke(self, messages):
            return SimpleNamespace(content=json.dumps(replies.pop(0), ensure_ascii=False),
                                   usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    monkeypatch.setattr(research, "configured_llm", lambda: Fake())
    task_id = create(c, h, kb, "员工年假有几天？").json()["id"]
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "completed", task
    assert task["usage"]["model_calls"] == 3 and task["usage"]["total_tokens"] == 450
    assert not replies


def test_no_evidence_never_calls_synthesis(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    monkeypatch.setattr(research, "configured_llm", lambda: pytest.fail("No model configured"))
    task_id = create(c, h).json()["id"]
    execute(c)
    task = get(c, h, task_id)
    assert task["result"]["provider"] == "abstention" and not task["result"]["findings"]


def test_two_workers_only_claim_once(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    entered, release = threading.Event(), threading.Event()
    calls = []
    def run(self):
        calls.append(self.id)
        entered.set()
        release.wait(5)
    monkeypatch.setattr(research.Runner, "run", run)
    worker = threading.Thread(target=execute, args=(c,))
    worker.start()
    assert entered.wait(5)
    execute(c)
    release.set()
    worker.join(5)
    assert calls == [task_id]
    with c.app.state.database.session() as db:
        assert len(db.scalars(select(ResearchTask)).all()) == 1


def test_retry_reuses_plan_and_has_own_idempotency(manual_client, monkeypatch):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    execute(c)
    original = get(c, h, task_id)
    key = new_id()
    retry = c.post(f"/api/research/{task_id}/retry", headers=h, json={"request_id": key})
    assert retry.status_code == 202
    new_task = retry.json()
    assert new_task["id"] != task_id and new_task["plan"] == original["plan"]
    assert c.post(f"/api/research/{task_id}/retry", headers=h, json={"request_id": key}).json()["id"] == new_task["id"]
    monkeypatch.setattr(research, "llm_ready", lambda: True)
    monkeypatch.setattr(research, "configured_llm", lambda: pytest.fail("Saved plan must be reused; empty corpus needs no synthesis"))
    execute(c)
    assert get(c, h, new_task["id"])["status"] == "completed"


@pytest.mark.parametrize("budget", [{"model_calls": 3}, {"reserved_tokens": 48000}])
def test_model_limits_stop_before_provider_call(manual_client, monkeypatch, budget):
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, task_id)
        task.status, task.lease_token = "running", "lease"
        task.deadline_at = utcnow() + timedelta(seconds=60)
        task.usage = budget
        db.commit()
    monkeypatch.setattr(research, "configured_llm", lambda: pytest.fail("Budget exceeded before call"))
    runner = research.Runner(c.app.state.database, c.app.state.settings, task_id, "lease")
    with pytest.raises(ValueError, match="budget"):
        runner.call("test", {}, {})


def test_watchdog_fails_running_operation_and_fences_late_result(manual_client, monkeypatch):
    import time
    c = manual_client
    h = register(c)
    task_id = create(c, h).json()["id"]
    monkeypatch.setitem(research.LIMITS, "seconds", .05)
    def slow(self):
        time.sleep(.2)
        self.record("completed", "Late", status="completed")
    monkeypatch.setattr(research.Runner, "run", slow)
    execute(c)
    task = get(c, h, task_id)
    assert task["status"] == "failed" and task["result"] is None
