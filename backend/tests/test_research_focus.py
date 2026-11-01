"""Only request wrappers change; factual boundaries and body admission remain strict."""
from app.services.research_retrieval import accepted_candidates, research_focus, research_queries


def test_request_wrappers_do_not_create_definition_gate_or_hide_constraints():
    q = "知识库中关于 LangGraph 在 AI Research Agent 中的职责（编排、状态管理、工具调用、流程控制）的原文证据是什么？"
    focus = research_focus(q)
    assert "是什么" not in focus and "知识库" not in focus
    assert "编排" in focus and "工具调用" in focus and "流程控制" in focus
    queries = research_queries(q)
    assert len(queries) == 2 and "orchestration" in queries[1] and "workflow" in queries[1]
    boundary = "知识库中是否有 LangGraph 与 Qdrant 集成的直接说明？不能推断必须组合。"
    assert "是否" in research_focus(boundary) and "不能" in research_focus(boundary)
    assert "必须" in research_focus(boundary)


def test_named_role_body_stays_distinct_from_metadata_code_and_other_intents():
    q = "知识库中关于 Qdrant 在 AI Research Agent 中的职责（向量存储、检索服务、过滤或元数据）的原文证据是什么？"
    good = {"document_name": "Qdrant.md", "text": "Qdrant stores vectors and payload metadata for search and filtering."}
    bad = [{"document_name": "LangGraph.md", "text": good["text"]},
           {"document_name": "Qdrant.md", "text": "# Qdrant\n```python\nclient = QdrantClient()\n```"},
           {"document_name": "Qdrant.md", "text": "**来源机构：** Qdrant\nTime: 40 min Level: Intermediate"},
           {"document_name": "Qdrant.md", "text": "Qdrant restaurant offers noodles and delicious food."}]
    assert accepted_candidates(q, [good, *bad], ["Qdrant"], .25) == [good]
    assert not accepted_candidates("Qdrant是否自动处理员工薪资和考勤？", [good], ["Qdrant"], .25)


def test_unfenced_code_and_neighbor_product_prose_cannot_prove_a_role():
    q = "知识库中关于 LangGraph 在 AI Research Agent 中职责（编排、状态管理、工具调用、流程控制）的原文证据是什么？"
    code = {"document_name": "LangGraph.md", "text": "# LangGraph\nfrom langgraph.graph import StateGraph, MessagesState\n"
        "def mock_llm(state: MessagesState):\n    return {'messages': state}\n"
        "graph = StateGraph(MessagesState)\ngraph.add_node(mock_llm)\ngraph.invoke({'messages': []})\n"
        "Use LangSmith to trace requests and debug agent behavior. Follow the quickstart to get set up."}
    assert not accepted_candidates(q, [code], ["LangGraph"], .25)
