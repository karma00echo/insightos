"""Synthetic public regression fixtures; legacy test names retain development lineage."""
import json
from pathlib import Path

import pytest
from app.config import Settings, get_settings
from app.services import answering
from app.services.verification import (
    CITATION_RE,
    compact_citations,
    factual_claims,
    normalize_answer,
    normalize_citations,
    verify_answer,
)
from langchain_core.messages import AIMessage

FIXTURES = Path(__file__).parent / "fixtures"
LATEST = json.loads((FIXTURES / "citation_boundary_real_failure.json").read_text(encoding="utf-8"))
PREVIOUS = json.loads((FIXTURES / "rag_chat_numeric_failure.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("saved", [LATEST, PREVIOUS], ids=["latest", "previous"])
@pytest.mark.parametrize("index", [0, 1], ids=["initial", "repair"])
@pytest.mark.parametrize("field", ["raw_answer", "checked_answer"])
def test_all_saved_texts_pass_without_changing_their_facts(saved, index, field):
    text = saved["attempts"][index][field]
    fixed = normalize_citations(text)
    assert normalize_citations(fixed) == fixed
    # Normalization changes spacing/reference placement, never the body or IDs.
    assert fixed.replace(" ", "") == text.replace(" ", "")
    assert verify_answer(text, saved["sources"], Settings(_env_file=None))["passed"]
    normalized = normalize_answer(text)
    assert normalize_answer(normalized) == normalized


@pytest.mark.parametrize("word", ["reranking", "embeddings", "late interaction embeddings", "retrieval", "precision"])
@pytest.mark.parametrize("suffix", ["[2]。", "[2][1]。", "[2] [1]！"])
def test_attached_words_and_phrases_in_prose_use_the_same_reference_boundary(word, suffix):
    text = "查询使用 " + word + suffix
    assert normalize_citations(text) == "查询使用 " + word + " " + suffix
    assert CITATION_RE.findall(text) == (["2"] if suffix == "[2]。" else ["2", "1"])
    assert CITATION_RE.sub("", text).strip().endswith(("。", "！"))
    assert "2" not in CITATION_RE.sub("", text)
    assert verify_answer(text, [{"text": word}] * 2, Settings(_env_file=None))["passed"]


def test_english_sentence_context_and_chinese_references():
    assert normalize_citations("The query uses embeddings[2].") == "The query uses embeddings [2]."
    assert normalize_citations("候选文档输入重排[2]。") == "候选文档输入重排[2]。"
    assert CITATION_RE.findall("The query uses embeddings[2].") == ["2"]
    assert normalize_citations("Reranking improves precision[2].") == "Reranking improves precision [2]."


@pytest.mark.parametrize("expression", [
    "arr[2]", "ht[0]", "arr2[1]", "matrix[1][2]", "arr[i][2]", "obj.values[2]",
    "result = arr[2]", "return arr[2]", "print(arr[2])", "f(arr[2])", "arr[2] + 1",
    "result = [1][2]", "return [1][2]", "Use array arr [2].",
    "func()[2]", "(arr)[2]", "{'a': 1}[2]", '"abc"[2]',
    "读取 arr[2]。", "访问 arr[2]。", "数组元素 arr[2]。", "变量 embeddings[2]。",
    "Use array embeddings[2].", "return arr [2]", "数组 arr [2]。",
    "reranking[2]", "late interaction embeddings[2]",  # No prose context: ambiguous.
    "arr [2]", "arr [1][2]",
])
def test_unmarked_code_or_ambiguous_bare_expressions_are_not_citations(expression):
    assert not CITATION_RE.findall(expression)
    assert normalize_citations(expression) == expression
    assert not verify_answer(expression, [{"text": "2"}] * 2, Settings(_env_file=None))["passed"]


@pytest.mark.parametrize("code", [
    "`查询使用 reranking[2]。`", "``查询使用 embeddings[2]。``",
    "```python\n[2]\n查询使用 reranking[2]。\n```",
    "~~~text\n[2]\n查询使用 reranking[2]。\n~~~",
    "```python\n[2]\n查询使用 reranking[2]。",  # Unfinished fence remains code.
    "    [2]\n    查询使用 embeddings[2]。",
    "查询使用 `reranking[2]。",
])
def test_code_regions_are_preserved_by_every_reference_consumer(code):
    assert normalize_citations(code) == code
    assert not CITATION_RE.findall(code)
    assert CITATION_RE.sub("", code) == code
    assert not verify_answer(code, [{"text": "2"}] * 2, Settings(_env_file=None))["passed"]


def test_code_indices_do_not_supply_evidence_or_get_renumbered():
    text = "查看 `arr[99]`，查询使用 embeddings[2]。"
    sources = [{"text": "unrelated"}, {"text": "arr[99] embeddings"}]
    assert verify_answer(text, sources, Settings(_env_file=None))["passed"]
    compact, used = compact_citations(text, sources)
    assert compact == "查看 `arr[99]`，查询使用 embeddings [1]。" and used == [sources[1]]
    assert CITATION_RE.findall(compact) == ["1"]
    assert "numeric_mismatch" in verify_answer(text, [{"text": "99"}, {"text": "embeddings"}], Settings(_env_file=None))["issues"]


def test_explicit_prose_citations_remain_valid_when_discussing_indexes_and_code():
    for text in ["The full text index [1].", "The index uses embeddings [1].",
                 "索引使用 embeddings [1]。", "调用 `return arr[1]`，说明见 [1]。"]:
        assert CITATION_RE.findall(text) == ["1"]
        assert verify_answer(text, [{"text": "The full text index uses embeddings; return arr[1]."}],
                             Settings(_env_file=None))["passed"]


def test_real_raw_sentences_keep_only_their_explicit_sources_after_repeated_normalization():
    fixed = normalize_answer(LATEST["attempts"][0]["raw_answer"])
    for _ in range(3):
        assert normalize_answer(fixed) == fixed
    for sentence in factual_claims(fixed)[2:4]:
        assert CITATION_RE.findall(sentence) == ["2"]
    assert len(factual_claims(fixed)) == 6
    assert normalize_citations("无引用事实。\n另一个事实[2]。") == "无引用事实。\n另一个事实[2]。"
    assert "uncited_claim" in verify_answer("无引用事实。\n另一个事实[2]。", [{"text": "事实"}] * 2, Settings(_env_file=None))["issues"]


@pytest.mark.parametrize("number", ["0", "3", "99"])
def test_invalid_attached_reference_is_identified_but_never_authorized(number):
    verdict = verify_answer("查询使用 reranking[" + number + "]。", [{"text": "reranking"}] * 2, Settings(_env_file=None))
    assert not verdict["passed"] and "invalid_source" in verdict["issues"]


@pytest.mark.parametrize("claim,source", [
    ("返回2个结果，查询使用 reranking[2]。", "reranking"),
    ("性能提高95%，查询使用 embeddings[2]。", "embeddings"),
    ("延迟1.5秒，查询使用 embeddings[2]。", "embeddings"),
    ("使用 GPT-7，查询使用 embeddings[2]。", "GPT-6 embeddings"),
    ("使用 v1.2.4，查询使用 embeddings[2]。", "v1.2.3 embeddings"),
])
def test_true_numbers_are_not_removed_with_reference_numbers(claim, source):
    assert "numeric_mismatch" in verify_answer(claim, [{"text": source}] * 2, Settings(_env_file=None))["issues"]


def test_internal_validated_bindings_do_not_turn_code_indices_into_prose_references():
    settings = Settings(_env_file=None)
    body = "```python\nreturn arr[2]\n```"
    sources = [{"text": body}]
    assert not verify_answer(body, sources, settings)["passed"]
    assert verify_answer(body, sources, settings, source_binding=[1])["passed"]
    wrong = verify_answer(body, [{"text": "return arr[1]"}], settings, source_binding=[1])
    assert wrong["issues"] == ["numeric_mismatch"]
    assert "invalid_source" in verify_answer(body, sources, settings, source_binding=[2])["issues"]
    assert "invalid_source" in verify_answer(body, sources, settings, source_binding=[True])["issues"]
    assert "empty_answer" in verify_answer("", sources, settings, source_binding=[1])["issues"]
    with pytest.raises(ValueError):
        verify_answer(body, sources, settings.model_copy(update={"citation_verifier": "llm"}), source_binding=[1])


def test_invalid_generation_reference_is_not_silently_replaced_by_paragraph_attribution(client, monkeypatch):
    settings = get_settings()
    for key, value in {"llm_provider": "ollama", "llm_model": "offline-invalid-citation", "cache_ttl": 0}.items():
        monkeypatch.setattr(settings, key, value)
    candidates = [{**c, "id": c["chunk_id"]} for c in LATEST["sources"]]
    monkeypatch.setattr(answering, "retrieve", lambda *a, **k: candidates)
    monkeypatch.setattr(answering, "rerank_candidates", lambda *a, **k: candidates)

    class InvalidProvider:
        def invoke(self, _):
            return AIMessage(content="查询使用 reranking[99]。其他信息 [1]。")

    monkeypatch.setattr(answering, "configured_llm", lambda: InvalidProvider())
    result = answering.answer_question(LATEST["question"], candidates)
    assert result["abstained"] and "invalid_source" in result["verification"]["issues"]
