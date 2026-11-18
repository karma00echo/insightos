"""Transient candidate workflow with mocked adapter and real isolated API/SQL."""

import json
from types import SimpleNamespace

import pytest
from app.decision_models import Decision
from app.memory_models import PersonalMemory
from app.models import Conversation, Document, EvaluationRun, Message, new_id, utcnow
from app.services import decision_candidates as service
from langsmith.run_helpers import get_tracing_context
from sqlalchemy import func, select
from test_api import register
from test_memories import seed_source

TEXT = "我考虑先做个人记忆或多Agent，决定先做个人记忆，因为需要可靠基础。"


def output(**changes):
    return {
        "classification": "decision",
        "reply": "这是未确认草稿，请检查并明确确认。",
        "candidate": {
            "question": "先开发什么？",
            "options": [{"id": "a", "label": "个人记忆"}, {"id": "b", "label": "多Agent"}],
            "selected_option": "a",
            "reasons": ["需要可靠基础"],
            "assumptions": [],
            "review_conditions": [],
        },
        **changes,
    }


@pytest.fixture
def cc(client, monkeypatch):
    calls = []
    state = {"output": output()}

    class Model:
        def model_copy(self, update):
            assert update["cache"] is False and update["callbacks"] == []
            assert update["max_retries"] == 0
            return self

        def invoke(self, messages, config):
            assert config["callbacks"] == [] and get_tracing_context()["enabled"] is False
            calls.append(messages)
            if state.get("error"):
                raise RuntimeError("PRIVATE-CANDIDATE provider body")
            if state.get("during"):
                state["during"]()
            return SimpleNamespace(content=state.get("raw", json.dumps(state["output"], ensure_ascii=False)))

    monkeypatch.setattr(service, "llm_ready", lambda: True)
    monkeypatch.setattr(service, "configured_llm", lambda: Model())
    return client, register(client), state, calls


def request(c, h, **changes):
    return c.post(
        "/api/decision-candidates", headers=h, json={"messages": [{"role": "user", "content": TEXT}], **changes}
    )


def assert_no_durable_content(c):
    with c.app.state.database.session() as db:
        for model in [Decision, PersonalMemory, Conversation, Message, EvaluationRun]:
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_candidate_no_write_then_final_edited_confirmation(cc, caplog):
    c, h, _, calls = cc
    r = request(c, h)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["readiness"] == "ready" and result["confirmed"] is False
    assert result["evidence_status"] == "unverified"
    assert r.headers["cache-control"] == "no-store" and len(calls) == 1
    assert_no_durable_content(c)
    final = {
        **result["candidate"],
        "question": "用户最终编辑的问题",
        "confirmed": True,
        "basis": result["basis"],
        "request_id": new_id(),
    }
    saved = c.post("/api/decisions", headers=h, json=final)
    assert saved.status_code == 201
    assert saved.json()["revision"]["content"]["question"] == final["question"]
    assert c.post("/api/decisions", headers=h, json=final).json()["id"] == saved.json()["id"]
    assert TEXT not in caplog.text


@pytest.mark.parametrize("classification", ["plan", "consideration", "ai_suggestion", "negation", "unrelated"])
def test_classification_does_not_confirm_decision(cc, classification):
    c, h, state, _ = cc
    state["output"] = output(classification=classification)
    if classification in {"negation", "unrelated"}:
        state["output"]["candidate"] = None
    result = request(c, h).json()
    assert result["classification"] == classification and result["readiness"] != "ready"
    assert result["confirmed"] is False
    assert_no_durable_content(c)


def test_insufficient_information_preserved_without_invention(cc):
    c, h, state, _ = cc
    state["output"]["candidate"].update(options=[{"id": "a", "label": "个人记忆"}], reasons=[])
    result = request(c, h).json()
    assert result["readiness"] == "incomplete"
    assert len(result["candidate"]["options"]) == 1 and result["candidate"]["reasons"] == []
    assert len(result["missing_fields"]) == 2
    assert_no_durable_content(c)


@pytest.mark.parametrize(
    "failure",
    [
        "malformed",
        "invent_reason",
        "invent_option",
        "fake_source",
        "confirmed",
        "mismatched",
        "oversize",
        "denial_with_candidate",
        "assistant_attribution",
    ],
)
def test_bad_model_outputs_fail_without_persistence_or_log_body(cc, failure, caplog):
    c, h, state, _ = cc
    if failure == "malformed":
        state["raw"] = "PRIVATE-CANDIDATE invalid JSON"
    elif failure == "invent_reason":
        state["output"]["candidate"]["reasons"] = ["PRIVATE-CANDIDATE invented"]
    elif failure == "invent_option":
        state["output"]["candidate"]["options"][1]["label"] = "invented"
    elif failure == "fake_source":
        state["output"]["basis"] = [{"type": "memory", "id": new_id()}]
    elif failure == "confirmed":
        state["output"]["confirmed"] = True
    elif failure == "mismatched":
        state["output"]["candidate"]["selected_option"] = "missing"
    elif failure == "oversize":
        state["raw"] = "X" * 24001
    elif failure == "denial_with_candidate":
        state["output"]["classification"] = "negation"
    else:
        state["output"]["candidate"]["reasons"] = ["assistant-only-reason"]
    r = request(
        c, h, messages=[{"role": "assistant", "content": "assistant-only-reason"}, {"role": "user", "content": TEXT}]
    )
    assert r.status_code == 502
    assert "PRIVATE-CANDIDATE" not in r.text + caplog.text
    assert_no_durable_content(c)


def test_provider_exception_sanitized(cc, caplog):
    c, h, state, _ = cc
    state["error"] = True
    r = request(c, h)
    assert r.status_code == 503 and "PRIVATE-CANDIDATE" not in r.text + caplog.text
    assert_no_durable_content(c)


@pytest.mark.parametrize(
    "bad",
    [
        {"owner_id": new_id()},
        {"confirmed": True},
        {"messages": [{"role": "system", "content": "secret"}]},
        {"messages": [{"role": "user", "content": "a" * 2001}]},
        {"messages": [{"role": "user", "content": "a" * 2000}] * 5},
        {"sources": [{"type": "report", "id": new_id(), "version": "1"}]},
    ],
)
def test_request_limits_privacy_and_unsupported_sources(cc, bad):
    c, h, _, calls = cc
    assert request(c, h, **bad).status_code == 422
    assert calls == []
    assert_no_durable_content(c)


@pytest.mark.parametrize("kind", ["user_message", "document_chunk"])
def test_source_authorization_before_and_on_final_save(cc, kind):
    c, h, _, calls = cc
    ref = seed_source(c, kind)
    other = register(c, "other@example.com")
    for headers, source in [(other, ref), (h, {**ref, "version": "forged"}), (h, {**ref, "id": new_id()})]:
        assert request(c, headers, sources=[source]).status_code == 422
    assert calls == []
    result = request(c, h, sources=[ref]).json()
    assert result["basis"][0]["id"] == ref["id"]
    if kind == "document_chunk":
        from app.models import Chunk

        with c.app.state.database.session() as db:
            db.delete(db.get(Document, db.get(Chunk, ref["id"]).document_id))
            db.commit()
        final = {**result["candidate"], "confirmed": True, "basis": result["basis"], "request_id": new_id()}
        assert c.post("/api/decisions", headers=h, json=final).status_code == 422


def test_memory_revoked_and_source_revocation_during_call(cc):
    c, h, state, _ = cc
    m = c.post(
        "/api/memories",
        headers=h,
        json={"request_id": new_id(), "confirmed": True, "kind": "preference", "content": "PRIVATE-CANDIDATE"},
    ).json()
    ref = {"type": "memory", "id": m["id"], "revision": 1}
    assert request(c, h, sources=[ref]).status_code == 200
    c.post("/api/memories/" + m["id"] + "/revoke", headers=h, json={"request_id": new_id(), "expected_version": 1})
    assert request(c, h, sources=[ref]).status_code == 422
    source = seed_source(c, "document_chunk")

    def expire():
        from app.models import Chunk

        with c.app.state.database.session() as db:
            db.get(Document, db.get(Chunk, source["id"]).document_id).expires_at = utcnow()
            db.commit()

    state["during"] = expire
    assert request(c, h, sources=[source]).status_code == 422


def test_auth_csrf_quota_and_no_model_configuration(cc, monkeypatch):
    c, h, _, calls = cc
    assert request(c, {}).status_code == 403
    c.cookies.clear()
    assert request(c, {}).status_code == 401
    monkeypatch.setattr(service, "llm_ready", lambda: False)
    assert request(c, h).status_code == 503 and not calls
    monkeypatch.setattr(service, "llm_ready", lambda: True)
    from fastapi import HTTPException

    monkeypatch.setattr(service, "consume", lambda *a, **k: (_ for _ in ()).throw(HTTPException(429, "budget")))
    assert request(c, h).status_code == 429 and not calls


def test_candidate_uses_shared_chat_capacity(cc):
    from app.models import User
    from app.services.admission import capacity

    c, h, _, calls = cc
    with c.app.state.database.session() as db:
        user_id = db.scalar(select(User.id))
    settings = c.app.state.settings
    with capacity(c.app.state.database, user_id, settings.max_active_chats, 60):
        # Occupy every configured slot, including the first one above.
        from contextlib import ExitStack

        with ExitStack() as stack:
            for _ in range(settings.max_active_chats - 1):
                stack.enter_context(capacity(c.app.state.database, user_id, settings.max_active_chats, 60))
            assert request(c, h).status_code == 429 and not calls
    assert request(c, h).status_code == 200
