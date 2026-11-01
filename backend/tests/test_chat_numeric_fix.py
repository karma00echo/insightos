"""Synthetic public regression fixtures; legacy test names retain development lineage."""
import json
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.services import answering
from app.services.verification import factual_claims, normalize_answer, normalize_citations, verify_answer
from langchain_core.messages import AIMessageChunk

REAL = json.loads((Path(__file__).parent / "fixtures/rag_chat_numeric_failure.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("attempt", REAL["attempts"], ids=lambda a: a["stage"])
@pytest.mark.parametrize("field", ["raw_answer", "checked_answer"])
def test_exact_saved_numeric_failure_passes_after_citation_boundary_repair(attempt, field):
    diagnostic = []
    verdict = verify_answer(attempt[field], REAL["sources"], Settings(_env_file=None), numeric_diagnostics=diagnostic)
    assert attempt["verification"]["issues"] == ["numeric_mismatch"]
    assert attempt["numeric_mismatches"][0]["values"] == ["2"]
    assert verdict["passed"] and verdict["model_calls"] == 0 and not diagnostic


def test_real_sentence_keeps_its_explicit_source_without_inheriting_other_sources():
    sentence = factual_claims(REAL["attempts"][0]["raw_answer"])[2]
    assert "late interaction embeddings [2]。" in sentence
    assert "[1]" not in sentence
    assert normalize_answer(sentence) == sentence


@pytest.mark.parametrize("phrase", ["dense embeddings", "late interaction embeddings", "hybrid search", "semantic retrieval"])
@pytest.mark.parametrize("tail", ["[1]。", "[1].", "[1] [2]。", "[1][2]", "[1]！"])
def test_sentence_final_english_phrases_accept_attached_references(phrase, tail):
    value = "查询使用 " + phrase + tail
    fixed = normalize_citations(value)
    assert fixed == "查询使用 " + phrase + " " + tail
    assert normalize_citations(fixed) == fixed
    assert verify_answer(value, [{"text": phrase}] * 2, Settings(_env_file=None))["passed"]


@pytest.mark.parametrize("value", [
    "查看 ht[0] 和 arr2[1] [2]。",
    "查看 ht[1]。",
    "使用 `dense embeddings[2]` [1]。",
    "使用 ``late interaction embeddings[2]`` [1]。",
    "```python\nlate interaction embeddings[2]\n```\n解释 [1]。",
    "~~~text\nlate interaction embeddings[2]\n~~~\n解释 [1]。",
])
def test_code_and_single_identifiers_are_not_rewritten_as_prose_references(value):
    assert normalize_citations(value) == value


@pytest.mark.parametrize("claim,source", [
    ("返回2个结果，采用 dense embeddings[1]。", "dense embeddings."),
    ("返回25个结果，采用 dense embeddings[1]。", "BM25 and dense embeddings."),
    ("性能提高95%，采用 dense embeddings[1]。", "dense embeddings."),
    ("性能提高20%，采用 dense embeddings[1]。", "dense embeddings returns 20 results."),
    ("延迟1.5秒，采用 dense embeddings[1]。", "dense embeddings."),
    ("使用 GPT-7 和 dense embeddings[1]。", "GPT-6 and dense embeddings."),
    ("使用 v1.2.4 和 dense embeddings[1]。", "v1.2.3 and dense embeddings."),
    ("使用384维 dense embeddings[1]。", "96-dimensional dense embeddings."),
])
def test_real_quantities_percentages_versions_and_metrics_remain_strict(claim, source):
    verdict = verify_answer(claim, [{"text": source}], Settings(_env_file=None))
    assert not verdict["passed"] and "numeric_mismatch" in verdict["issues"]


def test_attached_invalid_citation_is_rejected_and_other_sources_do_not_authorize_numbers():
    assert "invalid_source" in verify_answer("采用 dense embeddings[99]。", [{"text": "dense embeddings"}],
                                             Settings(_env_file=None))["issues"]
    verdict = verify_answer("返回40个结果，采用 dense embeddings[2]。",
                            [{"text": "40 results"}, {"text": "dense embeddings"}], Settings(_env_file=None))
    assert verdict["issues"] == ["numeric_mismatch"]
    assert verify_answer("采用 BM25 [1]。", [{"text": "BM25"}], Settings(_env_file=None))["passed"]


def test_saved_initial_stream_passes_without_repair_call(client, monkeypatch):
    settings = get_settings()
    for key, value in {"llm_provider": "ollama", "llm_model": "saved-output-offline", "cache_ttl": 0}.items():
        monkeypatch.setattr(settings, key, value)
    candidates = [{**c, "id": c["chunk_id"]} for c in REAL["sources"]]
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: candidates)
    monkeypatch.setattr(answering, "rerank_candidates", lambda *a, **k: candidates)
    raw = REAL["attempts"][0]["raw_answer"]

    class SavedProvider:
        def stream(self, _):
            for start in range(0, len(raw), 31):
                yield AIMessageChunk(content=raw[start:start + 31])

        def invoke(self, _):
            pytest.fail("Saved initial output must pass without generation/repair calls")

    monkeypatch.setattr(answering, "configured_llm", lambda: SavedProvider())
    events = []
    result = answering.answer_question(REAL["question"], candidates, emit=lambda *e: events.append(e))
    assert "".join(value["text"] for event, value in events if event == "draft") == raw
    assert result["verification"]["passed"] and not result["abstained"]
    assert result["usage"]["model_calls"] == 1  # One Mock stream, zero real provider calls.
    assert not any(t["step"] == "answer_repair" for t in result["trace"])
