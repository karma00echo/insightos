"""Deterministic regressions for cross-language gates and multi-turn failures."""
import json

import pytest
from app.config import Settings, get_settings
from app.services import answering
from app.services.evidence import coverage, retrieval_queries, search_terms
from app.services.retrieval import index_chunks, rerank_candidates, retrieve
from app.services.verification import verify_answer
from fastapi import HTTPException
from langchain_core.messages import AIMessage


def candidate(cid="a", name="LangGraph_overview.md", text=None):
    return {"id": cid, "chunk_id": cid, "document_id": cid, "knowledge_base_id": "k", "page": 1,
            "document_name": name, "score": .03, "retrieval_score": .03,
            "text": text or "LangGraph provides orchestration, durable execution, streaming and human-in-the-loop. It can be used standalone without LangChain."}


def test_bilingual_gate_keeps_named_english_evidence_without_lowering_threshold():
    question = "LangGraph 的核心能力包括哪些？请根据资料列出。"
    assert coverage(question, "LangGraph core benefits: durable execution and streaming.") >= .75
    assert coverage(question, "Qdrant dense semantic search.") == 0
    assert coverage("如何实现持久化？", "persistence through failures") == 0
    assert "persistence" in search_terms("LangGraph 持久化")


def test_synthesis_subqueries_keep_both_explicit_names():
    assert {"LangGraph", "Qdrant"} <= set(retrieval_queries("结合 LangGraph 和 Qdrant 教程说明职责"))


@pytest.mark.parametrize("question", ["那它必须搭配 LangChain 吗？", "是否支持人工介入？", "Does it require LangChain?"])
def test_followup_resolution_uses_the_prior_subject(question):
    history = [{"role": "user", "content": "LangGraph 的核心能力是什么？"}]
    resolved, kept = answering.resolve_question(question, history)
    assert resolved.startswith("LangGraph\n") and kept == history


def test_explicit_new_topic_drops_history():
    resolved, history = answering.resolve_question("Qdrant 如何检索？", [{"role": "user", "content": "LangGraph 核心能力？"}])
    assert resolved == "Qdrant 如何检索？" and history == []


def test_third_turn_keeps_original_subject_instead_of_dependency():
    history = [{"role": "user", "content": "LangGraph 核心能力？"},
               {"role": "assistant", "content": "支持编排。"},
               {"role": "user", "content": "那它必须搭配 LangChain 吗？"},
               {"role": "assistant", "content": "当前文档说明可以独立使用。"}]
    resolved, _ = answering.resolve_question("是否支持人工介入？", history)
    assert resolved.startswith("LangGraph\n")


def test_raw_hybrid_pool_does_not_apply_lexical_pruning(client, monkeypatch):
    from app.services import retrieval
    item = candidate(text="LangGraph durable execution and streaming.")
    index_chunks([item])
    monkeypatch.setattr(retrieval, "rerank_candidates", lambda *a, **k: pytest.fail("raw pool was pruned"))
    assert retrieve("LangGraph 核心能力", [item], rerank_results=False)


def test_two_requested_documents_survive_single_document_crowding(client):
    items = [candidate(str(n)) for n in range(8)]
    items.append(candidate("q", "Qdrant_guide.md", "Qdrant uses dense semantic search and BM25 keyword retrieval, followed by late interaction reranking."))
    selected = rerank_candidates("结合 LangGraph 和 Qdrant 说明职责", items, 2)
    assert len({c["document_name"] for c in selected}) == 2


@pytest.mark.parametrize("marker", ["1. ", "1、", "1）", "1)"])
def test_numeric_verifier_ignores_list_markers_but_checks_values(marker):
    settings = Settings(_env_file=None)
    assert verify_answer(marker + "LangGraph 支持持久化 [1]。", [{"text": "LangGraph supports persistence."}], settings)["passed"]
    assert not verify_answer("延迟为1.5秒 [1]。", [{"text": "延迟为2秒。"}], settings)["passed"]


def test_empty_model_answer_is_not_a_success():
    assert not verify_answer("", [{"text": "evidence"}], Settings(_env_file=None))["passed"]


def configure(monkeypatch):
    for key, value in {"cache_ttl": 0, "llm_provider": "ollama", "llm_model": "fixture", "citation_verifier": "numeric"}.items():
        monkeypatch.setattr(get_settings(), key, value)


def test_failed_generation_and_repair_report_failure_without_excerpts(client, monkeypatch):
    configure(monkeypatch)
    item = candidate(text="LangGraph 支持持久化和人工介入，能够编排有状态 Agent。")
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: [item])
    class Model:
        def invoke(self, messages):
            return AIMessage(content="LangGraph 支持9999个用户 [1]。")
    monkeypatch.setattr(answering, "configured_llm", lambda: Model())
    result = answering.answer_question("LangGraph 支持什么？", [item])
    assert result["abstained"] and not result["verification"]["passed"]
    assert "numeric_mismatch" in result["answer"] and "原文摘录" not in result["answer"]
    assert result["usage"]["model_calls"] == 2 and not result["citations"]


def test_repair_receives_same_resolved_question_and_history(client, monkeypatch):
    configure(monkeypatch)
    item = candidate()
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: [item])
    seen = []
    class Model:
        def invoke(self, messages):
            seen.append(json.loads(messages[-1].content))
            return AIMessage(content="LangGraph 有9999种用法 [1]。" if len(seen) == 1 else "LangGraph 可独立使用 [1]。")
    monkeypatch.setattr(answering, "configured_llm", lambda: Model())
    result = answering.answer_question("那它必须搭配 LangChain 吗？", [item], history=[{"role": "user", "content": "LangGraph 核心能力？"}])
    assert len(seen) == 2 and seen[0]["history"] == seen[1]["history"]
    assert seen[0]["resolved_question"] == seen[1]["resolved_question"]
    assert result["provider"] == "fixture" and result["verification"]["passed"]


def test_answer_cannot_omit_one_requested_document(client, monkeypatch):
    configure(monkeypatch)
    items = [candidate(), candidate("b", "Qdrant_guide.md", "Qdrant provides dense semantic search and BM25 keyword retrieval; late interaction embeddings rerank candidates.")]
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: items)
    class Model:
        def invoke(self, messages):
            return AIMessage(content="LangGraph 支持编排 [1]。")
    monkeypatch.setattr(answering, "configured_llm", lambda: Model())
    result = answering.answer_question("结合 LangGraph 和 Qdrant 说明职责", items)
    assert result["abstained"] and "document_coverage_missing" in result["verification"]["issues"]


def test_missing_retrieved_side_abstains_before_model(client, monkeypatch):
    configure(monkeypatch)
    items = [candidate(), candidate("b", "Qdrant_guide.md", "Qdrant provides dense semantic search and BM25 keyword retrieval; late interaction reranks candidates.")]
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: [items[0]])
    monkeypatch.setattr(answering, "configured_llm", lambda: pytest.fail("incomplete document coverage reached the model"))
    result = answering.answer_question("结合 LangGraph 和 Qdrant 说明职责", items)
    assert result["abstained"] and "Qdrant" in result["answer"]
    assert result["usage"]["model_calls"] == 0


def test_model_exception_is_explicit_and_does_not_expose_provider_data(client, monkeypatch):
    configure(monkeypatch)
    item = candidate()
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: [item])
    class Model:
        def invoke(self, messages):
            raise RuntimeError("secret provider request content")
    monkeypatch.setattr(answering, "configured_llm", lambda: Model())
    with pytest.raises(HTTPException) as error:
        answering.answer_question("LangGraph 支持什么？", [item])
    assert error.value.status_code == 503 and error.value.detail == "模型生成异常，请稍后重试"
