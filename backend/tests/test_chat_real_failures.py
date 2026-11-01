"""Synthetic public regression fixtures; legacy test names retain development lineage."""
import json
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.models import ChatRequestRun, Document
from app.services import answering
from app.services.chat_evidence import retrieval_query
from app.services.evidence import coverage, named_topics, terms
from app.services.verification import verify_answer
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import select
from test_api import create_kb, register, upload, wait_job

SAVED = json.loads((Path(__file__).parent / "fixtures/rag_chat_real_failures.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("question", SAVED["questions"])
def test_real_questions_deliver_english_method_evidence_to_final_context(client, monkeypatch, question):
    items = SAVED["chunks"]
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: items)
    contexts = []
    answering.answer_question(question, items, emit=lambda event, value:
        contexts.extend(value) if event == "sources" else None)
    assert contexts and all("Qdrant" in c["document_name"] for c in contexts)
    assert any("Dense embeddings and BM25 feed hybrid search" in c["text"] for c in contexts)
    assert not ({"分析", "场景", "问题", "支持"} & terms(retrieval_query(question)))
    assert not any("GPT-6" in c["document_name"] for c in contexts)


def test_saved_first_failure_reproduces_old_coverage_and_new_topic_coverage():
    question = SAVED["questions"][0]
    evidence = next(c["text"] for c in SAVED["chunks"] if "Dense embeddings and BM25 feed" in c["text"])
    assert coverage(question, evidence) < .25
    assert coverage(retrieval_query(question), evidence) >= .75
    assert "量子" in retrieval_query("请分析量子纠错与混合检索的协作流程")


@pytest.mark.parametrize("question", ["GPT-6 应如何选择模型？", "如何实现持久化？", "住宿报销上限是多少？"])
def test_simple_and_nontechnical_questions_keep_existing_focus(question):
    assert retrieval_query(question) == question


def test_query_preparation_preserves_explicit_names_for_document_coverage_guard():
    query = retrieval_query("请分析 LangGraph 编排和 Qdrant 混合检索的协作流程")
    assert {"LangGraph", "Qdrant"} <= set(named_topics(query))


@pytest.mark.parametrize("answer,source", [
    ("BM25 用于关键词检索 [1]。", "BM25 is used for keyword search."),
    ("1. **BM25** 用于关键词检索 [1]。", "BM25 is used for keyword search."),
    ("GPT-6.1 可用于编程 [1]。", "GPT-6.1 supports programming."),
    ("使用 v1.2.3 [1]。", "Use v1.2.3."),
    ("BM25 用于检索 [2][1]。", "BM25 search."),
])
def test_legal_identifiers_and_citations_already_pass_without_relaxation(answer, source):
    assert verify_answer(answer, [{"text": source}] * 2, Settings(_env_file=None))["passed"]


@pytest.mark.parametrize("answer", [
    "返回25个结果 [1]。", "返回1个结果 [1]。", "性能提高95% [1]。", "延迟1.5秒 [1]。",
    "支持9999个用户 [1]。", "性能提高25% [1]。",
])
def test_identifiers_and_source_numbers_do_not_authorize_quantities(answer):
    diagnostics = []
    settings = Settings(_env_file=None)
    sources = [{"text": "BM25 keyword search."}]
    verdict = verify_answer(answer, sources, settings, numeric_diagnostics=diagnostics)
    assert verdict == verify_answer(answer, sources, settings)
    assert not verdict["passed"] and verdict["issues"] == ["numeric_mismatch"]
    assert diagnostics[0]["values"] and diagnostics[0]["expected_values"] == []
    assert diagnostics[0]["source_ids"] == [1]


@pytest.mark.parametrize("answer,source", [
    ("提高30% [1]。", "提高20%。"),
    ("提高20% [1]。", "返回20个结果。"),
    ("使用 GPT-7 [1]。", "使用 GPT-6。"),
    ("使用 v1.2.4 [1]。", "使用 v1.2.3。"),
])
def test_wrong_percentages_models_and_versions_still_fail(answer, source):
    assert "numeric_mismatch" in verify_answer(answer, [{"text": source}], Settings(_env_file=None))["issues"]


def test_stream_failure_saves_raw_and_checked_attempts_privately_and_replay_stays_safe(client, monkeypatch):
    headers = register(client)
    kb = create_kb(client, headers)
    task = upload(client, headers, kb, "住宿报销上限500元。".encode())
    assert wait_job(client, headers, task["job_id"])["status"] == "completed"
    monkeypatch.setattr(get_settings(), "llm_model", "offline-fixture")
    monkeypatch.setattr(get_settings(), "llm_provider", "ollama")

    class Provider:
        def stream(self, _):
            yield AIMessageChunk(content="1. 住宿报销上限")
            yield AIMessageChunk(content="5000元 [1]。")

        def invoke(self, _):
            return AIMessage(content="住宿报销上限6000元 [1]。")

    monkeypatch.setattr(answering, "configured_llm", lambda: Provider())
    body = {"question": "住宿报销上限是多少？", "knowledge_base_ids": [kb], "request_id": "diagnostic-offline-1"}
    response = client.post("/api/chat/stream", headers=headers, json=body)
    final = json.loads(response.text.split("event: done\ndata: ")[1].split("\n\n")[0])
    assert final["abstained"] and "numeric_mismatch" in final["answer"]
    assert "_failure_diagnostic" not in response.text and "6000元" not in response.text
    with client.app.state.database.session() as db:
        run = db.scalar(select(ChatRequestRun).where(ChatRequestRun.request_key == body["request_id"]))
        diagnostic = run.result["_failure_diagnostic"]
    initial, repair = diagnostic["attempts"]
    draft = "".join(json.loads(block.split("data: ")[1])["text"] for block in response.text.split("\n\n")
                    if block.startswith("event: draft"))
    assert initial["raw_answer"] == draft == "1. 住宿报销上限5000元 [1]。"
    assert initial["numeric_mismatches"][0]["values"] == ["5000"]
    assert repair["numeric_mismatches"][0]["values"] == ["6000"]
    assert "500元" in diagnostic["sources"][0]["text"]
    assert initial["checked_answer"] and not initial["verification"]["passed"]
    replay = client.post("/api/chat", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["idempotent_replay"]
    assert "_failure_diagnostic" not in replay.text and "6000元" not in replay.text
    history = client.get("/api/conversations/" + final["conversation_id"], headers=headers)
    assert "_failure_diagnostic" not in history.text and "6000元" not in history.text


def test_failed_answer_diagnostics_revalidate_source_before_persistence(client, monkeypatch):
    headers = register(client)
    kb = create_kb(client, headers)
    task = upload(client, headers, kb, "住宿报销上限500元。".encode())
    assert wait_job(client, headers, task["job_id"])["status"] == "completed"
    monkeypatch.setattr(get_settings(), "llm_model", "offline-fixture")
    monkeypatch.setattr(get_settings(), "llm_provider", "ollama")

    class Provider:
        def invoke(self, _):
            with client.app.state.database.session() as db:
                db.get(Document, task["document"]["id"]).status = "deleting"
                db.commit()
            return AIMessage(content="住宿报销上限6000元 [1]。")

    monkeypatch.setattr(answering, "configured_llm", lambda: Provider())
    response = client.post("/api/chat", headers=headers, json={"question": "住宿报销上限是多少？",
        "knowledge_base_ids": [kb], "request_id": "source-changed-diagnostic"})
    assert response.status_code == 409 and "6000" not in response.text
    with client.app.state.database.session() as db:
        run = db.scalar(select(ChatRequestRun).where(ChatRequestRun.request_key == "source-changed-diagnostic"))
        assert run.status == "failed" and not run.result
