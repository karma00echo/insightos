"""Offline failure classification, gap boundaries and independent saved-report writing."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from app.config import Settings
from app.models import ResearchReport, ResearchTask, User, new_id
from app.services import report_writing, reports, research
from app.services.research_errors import ResearchFailure, call_observations, prior_verification_error
from openai import APIConnectionError, APIStatusError
from sqlalchemy import select
from test_api import register
from test_mvp_workflow import written
from test_reports import create as create_report
from test_reports import seed
from test_research import execute, get
from test_research_stability import mock_replies, prepared, synthesis

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def manual_client(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    return request.getfixturevalue("client")


def test_archived_sequence_preserves_two_distinct_failures_and_unknown_raw_draft():
    if not (ROOT / "evaluation/mvp-v1-acceptance/task-final.json").is_file():
        pytest.skip("Private archived acceptance task is not distributed")
    task = json.loads((ROOT / "evaluation/mvp-v1-acceptance/task-final.json").read_text(encoding="utf8"))
    ledger = json.loads((ROOT / "evaluation/mvp-v1-acceptance/provider-usage.json").read_text(encoding="utf8"))
    assert task["error_details"]["code"] == "MODEL_REQUEST_FAILED"
    assert prior_verification_error(task["events"])["code"] == "EVIDENCE_GAP"
    assert len(ledger["requests"]) == 2 and all(r["http_status"] == 200 for r in ledger["requests"])
    counts = call_observations(task["usage"])
    assert counts["attempts"] == 3 and counts["calls_with_usage"] == 2 and counts["network_send_count"] is None
    assert task["result"] is None and task["usage"]["calls"][-1]["status"] == "attempted"
    assert call_observations({"model_calls": 2})["calls_with_usage"] is None
    # A recovered validation followed by later work is not presented as a current cause.
    events = copy.deepcopy(task["events"])
    events.insert(-1, {"phase": "writing_report"})
    assert prior_verification_error(events) is None


def test_saved_six_evidence_projection_keeps_gap_but_allows_only_supported_analysis():
    if not (ROOT / "evaluation/mvp-v1-acceptance/task-final.json").is_file():
        pytest.skip("Private archived acceptance task is not distributed")
    task = json.loads((ROOT / "evaluation/mvp-v1-acceptance/task-final.json").read_text(encoding="utf8"))
    before = reports.digest(task)
    by_id = {e["id"]: e for e in task["evidence"]}
    data = {"findings": [{"kind": "evidence", "text": by_id[eid]["text"][:350],
                         "evidence_ids": [eid], "step_ids": sids} for eid, sids in
                         (("e1", ["s1"]), ("e4", ["s2", "s3", "s4"]))],
        "analysis": [{"kind": "synthesis", "text": "所引流程将候选召回与精细重排区分为不同环节，结论只限该资料描述。",
                      "evidence_ids": ["e4"], "step_ids": ["s3"]}],
        "limitations": [task["steps"][3]["gap"]], "follow_up_questions": []}
    scope, schema = research.synthesis_contract(task["steps"], task["evidence"], [])
    assert set(scope["allowed_step_evidence"]) == {"s1", "s2", "s3"}
    assert scope["blocked_step_ids"] == ["s4"]
    assert "s4" not in schema["properties"]["analysis"]["items"]["properties"]["step_ids"]["items"]["enum"]
    assert research.validate_synthesis(data, task["evidence"], task["steps"], Settings(), [])["analysis"]
    bad = copy.deepcopy(data)
    bad["analysis"][0]["step_ids"] = ["s4"]
    with pytest.raises(ResearchFailure) as error:
        research.validate_synthesis(bad, task["evidence"], task["steps"], Settings(), [])
    assert error.value.code == "EVIDENCE_GAP"
    assert reports.digest(task) == before  # Projection, never a successful historical rerun.
    no_scope, no_schema = research.synthesis_contract([{**s, "gap": "missing"} for s in task["steps"]], task["evidence"], [])
    assert not no_scope["allowed_step_evidence"] and no_schema["properties"]["analysis"]["maxItems"] == 0


@pytest.mark.parametrize("typed,expected", [(True, "MODEL_REQUEST_BLOCKED"), (False, "MODEL_ADAPTER_FAILED")])
def test_failed_repair_keeps_initial_validation_separate_from_local_failure(manual_client, monkeypatch, typed, expected):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    error = ResearchFailure("MODEL_REQUEST_BLOCKED", stage="synthesizing", error_type="LocalPolicyError") if typed else ValueError("private guard text")
    calls = mock_replies(monkeypatch, [synthesis("伪造原文不允许通过。"), error])
    execute(c)
    task = get(c, h, tid)
    assert len(calls) == 2 and task["result"] is None
    assert task["error_details"]["code"] == expected
    assert task["error_details"]["prior_failure"]["code"] == "QUOTE_NOT_VERBATIM"
    assert task["prior_error_details"]["code"] == "QUOTE_NOT_VERBATIM"
    assert task["call_observations"]["calls_with_usage"] == 1
    assert "private guard text" not in str(task)


@pytest.mark.parametrize("status,code", [(401, "MODEL_AUTH_FAILED"), (429, "MODEL_RATE_LIMITED"), (500, "MODEL_HTTP_FAILED")])
def test_http_errors_have_status_without_leaking_provider_body(manual_client, monkeypatch, status, code):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    exc = APIStatusError("private provider body", response=httpx.Response(status, request=httpx.Request("POST", "https://api.deepseek.com")),
                         body={"secret": "never expose"})
    calls = mock_replies(monkeypatch, [exc])
    execute(c)
    task = get(c, h, tid)
    assert len(calls) == 1 and task["error_details"]["code"] == code
    assert task["error_details"]["http_status"] == status and task["result"] is None
    assert "private provider" not in str(task) and "never expose" not in str(task)


def test_configuration_failure_is_not_a_provider_response(manual_client, monkeypatch):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    monkeypatch.setattr(research, "llm_ready", lambda: True)
    monkeypatch.setattr(research, "configured_llm", lambda: (_ for _ in ()).throw(ValueError("private config")))
    execute(c)
    task = get(c, h, tid)
    assert task["error_details"]["code"] == "MODEL_CONFIGURATION_FAILED"
    assert task["call_observations"]["responses_received"] == 0 and task["result"] is None


@pytest.mark.parametrize("exc", [httpx.ConnectError("private connection"),
    APIConnectionError(request=httpx.Request("POST", "https://api.deepseek.com"))])
def test_connection_failure_is_not_reported_as_http_or_validation(manual_client, monkeypatch, exc):
    c, h = manual_client, register(manual_client)
    tid = prepared(c, h)
    calls = mock_replies(monkeypatch, [exc])
    execute(c)
    task = get(c, h, tid)
    assert len(calls) == 1 and task["error_details"]["code"] == "MODEL_CONNECTION_FAILED"
    assert task["call_observations"]["network_send_count"] is None
    assert "private connection" not in str(task)


def writer(c, tid, invoke):
    with c.app.state.database.session() as db:
        owner = db.get(ResearchTask, tid).user_id
    return report_writing.write_saved_report(c.app.state.database, owner, tid, c.app.state.settings, invoke)


def test_independent_writer_versions_export_and_original_task_are_unchanged(manual_client):
    c, h = manual_client, register(manual_client)
    tid = seed(c, h, ["员工每年享有十五天带薪年假。"])
    old = create_report(c, h, tid).json()
    old_bytes = c.get(f"/api/research-reports/{old['id']}/export", headers=h).content
    before = get(c, h, tid)
    calls = []
    def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return written(), {"input_tokens": 17, "output_tokens": 23, "api_key": "private"}
    content = writer(c, tid, invoke)
    assert len(calls) == 1 and content["generation"]["invocation_kind"] == "mock"
    assert content["generation"]["input_tokens"] == 17 and "api_key" not in content["generation"]
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        saved = reports.save_version(db, db.get(User, task.user_id), task, content, new_id())
        db.commit()
        rid = saved.id
    latest = c.get("/api/research-reports/" + rid, headers=h).json()
    assert latest["version"] == 2 and latest["current_validation"]["status"] == "VALID"
    exported = c.get(f"/api/research-reports/{rid}/export", headers=h)
    assert "MOCK" in exported.text and "综合分析" in exported.text
    assert get(c, h, tid) == before
    assert c.get(f"/api/research-reports/{old['id']}/export", headers=h).content == old_bytes
    assert all(claim["quality_status"] == "NEEDS_REVIEW" for claim in content["claims"] if claim["id"].startswith("w"))


@pytest.mark.parametrize("failure", ["unknown_citation", "numeric", "timeout"])
def test_independent_writer_failure_preserves_task_and_versions(manual_client, failure):
    c, h = manual_client, register(manual_client)
    tid = seed(c, h)
    old = create_report(c, h, tid).json()
    before = get(c, h, tid)
    def invoke(*args, **kwargs):
        if failure == "timeout":
            raise TimeoutError("mock timeout")
        data = written()
        if failure == "unknown_citation":
            data["sections"][0]["paragraphs"][0]["evidence_ids"] = ["e999"]
        else:
            data["sections"][0]["paragraphs"][0]["text"] += "每位员工享有999天假期。"
        return data, {}
    with pytest.raises((ValueError, TimeoutError)):
        writer(c, tid, invoke)
    assert get(c, h, tid) == before
    assert [r["id"] for r in c.get("/api/research-reports?task_id=" + tid, headers=h).json()] == [old["id"]]


def test_partial_completed_report_keeps_gap_and_failed_research_is_rejected(manual_client):
    c, h = manual_client, register(manual_client)
    tid = seed(c, h)
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        task.steps = [*task.steps, {"id": "s2", "question": "资料未覆盖的问题", "status": "completed", "rounds": [],
            "evidence_ids": [], "gap": "缺少直接资料，不得作确定判断"}]
        task.result = {**task.result, "gaps": [{"step_id": "s2", "question": "资料未覆盖的问题", "reason": "缺少直接资料"}]}
        db.commit()
    content = writer(c, tid, lambda *a, **kw: (written(), {}))
    assert any("缺少直接资料" in p for s in content["sections"] if s["id"] == "limitations" for p in s["paragraphs"])
    assert all("s2" not in cl["step_ids"] for cl in content["claims"] if cl["id"].startswith("w"))
    with c.app.state.database.session() as db:
        db.get(ResearchTask, tid).status = "failed"
        db.commit()
    with pytest.raises(ValueError, match="completed"):
        writer(c, tid, lambda *a, **kw: pytest.fail("Failed research must not invoke the writer"))


def test_independent_writer_rechecks_immutable_task_after_callback(manual_client):
    c, h = manual_client, register(manual_client)
    tid = seed(c, h)
    def invoke(*args, **kwargs):
        with c.app.state.database.session() as db:
            task = db.get(ResearchTask, tid)
            task.result = {**task.result, "limitations": ["changed during callback"]}
            db.commit()
        return written(), {}
    with pytest.raises(ValueError, match="changed"):
        writer(c, tid, invoke)
    with c.app.state.database.session() as db:
        assert not list(db.scalars(select(ResearchReport).where(ResearchReport.task_id == tid)))
