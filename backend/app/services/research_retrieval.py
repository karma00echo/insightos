"""Research-only source reservation and body relevance. Ordinary RAG is unchanged."""
import copy
import re

from sqlalchemy import func, or_

from app.models import Document
from app.services.embeddings import tokenize
from app.services.evidence import TECH_ALIASES, clean_text, coverage, terms

# Query wrapper words are not factual research targets. This is local to research.
CONTEXT_WORDS = {"ai", "agent", "research", "知识库", "当前", "基于", "关于", "描述", "资料", "在", "中"}
ROLE_QUESTION = re.compile(r"职责|负责|作用|定位|\brole\b|\bresponsib", re.I)
FUNCTION_WORDS = re.compile(r"\b(?:provid\w*|support\w*|build\w*|manag\w*|deploy\w*|stor\w*|retriev\w*|search\w*|index\w*|rerank\w*|orchestrat\w*|runtime|framework)\b|负责|提供|支持|构建|管理|部署|存储|检索|查询|编排|重排", re.I)
RESEARCH_ALIASES = {"存储": ("storage", "store", "storing"), "向量": ("vector", "vectors", "embeddings"),
    "服务": ("service", "serving"), "过滤": ("filter", "filtering"), "元数据": ("metadata", "payload"),
    "工具": ("tool", "tools"), "调用": ("calling", "invoke"), "流程": ("workflow", "workflows"),
    "控制": ("control",), "管理": ("management", "manage"), "状态管理": ("state", "stateful")}


def research_focus(question):
    """Remove fixed request wrappers, never factual negation or integration constraints."""
    value = re.sub(r"^\s*(?:请)?(?:查阅|根据|基于)?(?:当前)?知识库(?:中)?(?:关于|的)?\s*", "", question)
    value = re.sub(r"\s*在\s*AI\s+Research\s+Agent\s*中(?:的)?", " ", value, flags=re.I)
    value = re.sub(r"(?:的)?原文证据(?:是什么|有哪些)?[？?。]*$", "", value)
    return value.strip(" ，,。？?") or question


def research_queries(question):
    from app.services.evidence import search_terms
    focus = research_focus(question)
    words = search_terms(focus) - CONTEXT_WORDS
    words |= {alias for term, aliases in RESEARCH_ALIASES.items() if term in focus for alias in aliases}
    return list(dict.fromkeys([focus, " ".join(sorted(words))[:500]]))


def scoped_sources(corpus, topics):
    """Filter authorized SQL hydration/BM25 before shared ranking truncates the pool.

    The vector filter remains the existing KB ACL. Hydration applies these extra
    document conditions, so foreign-source dense hits cannot occupy result slots.
    No additional retrieval rounds, model calls or index operations are introduced.
    """
    if not topics:
        return corpus
    narrowed = copy.copy(corpus)
    narrowed.conditions = [*corpus.conditions, or_(*[
        func.lower(Document.name).contains(topic.lower(), autoescape=True) for topic in topics])]
    return narrowed


def prose_body(text):
    # Titles, provenance metadata and code alone do not establish a product role.
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", clean_text(text))
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or re.match(
                r"(?:pip |uv |from [\w.]+ import |import |def |class |return |if |for |while |with |"
                r"[\w.]+\s*[=(]|[{}()[\]]|[\"']\w+[\"']\s*:)", line):
            continue
        if re.match(r"(?:\*\*)?(?:来源机构|原始页面|资料类型|整理日期|说明)[：:]|(?:Time|Level)\s*:", line):
            continue
        if len(re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", line)) < 5:
            continue
        lines.append(line)
    return "\n".join(lines)


def relevant_body(question, candidate, topics, minimum):
    question = research_focus(question)
    body = prose_body(candidate["text"])
    if not body:
        return False
    if not topics:
        return coverage(question, body) >= minimum
    matched = [t for t in topics if t.casefold() in candidate["document_name"].casefold()]
    if not matched:
        return False
    intents = terms(question) - CONTEXT_WORDS - {t.lower() for t in topics}
    if intents:
        # Product title matches alone cannot satisfy a factual question.
        found, lower = set(tokenize(body)), body.lower()
        matches = sum(word in found or word in lower and bool(re.search(r"[\u4e00-\u9fff]", word))
            or any(alias in found for alias in (*TECH_ALIASES.get(word, ()), *RESEARCH_ALIASES.get(word, ())))
            or len(word) >= 3 and bool(re.search(r"\b" + re.escape(word), lower)) for word in intents)
        return matches / len(intents) >= minimum
    # A broad role question needs a product mention in prose plus functional content.
    return bool(ROLE_QUESTION.search(question) and FUNCTION_WORDS.search(body)
                and any(re.search(r"\b" + re.escape(t) + r"\b", body, re.I) for t in matched))


def accepted_candidates(question, candidates, topics, minimum):
    relevant = [c for c in candidates if relevant_body(question, c, topics, minimum)]
    kept = []
    for topic in topics:
        item = next((c for c in relevant if topic.casefold() in c["document_name"].casefold()), None)
        if item and item not in kept:
            kept.append(item)
    return [*kept, *(c for c in relevant if c not in kept)]
