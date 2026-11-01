"""Query-only preparation for ordinary chat; source text and indexes are untouched."""
import re

from app.services.evidence import TECH_ALIASES, named_topics, terms

# Instructions about the shape of an answer are not its technical subject.
RESPONSE_WORDS = {"分析", "场景", "问题", "支持", "两者", "协作", "原理", "及", "在", "基于",
                  "并", "技术", "无法", "流程", "现有", "结论", "解决", "适用", "分工", "衔接",
                  "标明", "内容", "依据", "请分析", "说明", "未说明"}


def retrieval_query(question):
    """Canonicalize technical analysis requests, retaining unknown topic words.

    Only explicit bounded glossary entries are translated. This neither infers a
    product nor establishes factual support. The original question still goes to
    the generator and verifier. Definitions, value lookups and simple requests
    continue through the existing focus rules.
    """
    if not re.search(r"分析|分工|衔接|协作|适用场景", question):
        return question
    if not (any(term in question for term in TECH_ALIASES) or any(
            re.search(r"\b" + re.escape(alias) + r"\b", question, re.I)
            for aliases in TECH_ALIASES.values() for alias in aliases)):
        return question
    # A leading application context must not outweigh the methods being compared.
    value = re.sub(r"^在基于\s+[^，,？?。]{1,100}系统中[，,]\s*", "", question)
    # Keep glossary units even when jieba would split them differently without a
    # Latin product name. No changes to tokenization of indexed documents.
    parts = re.split("(" + "|".join(sorted(TECH_ALIASES, key=len, reverse=True)) + ")", value)
    words = set()
    for part in parts:
        if part in TECH_ALIASES:
            words.add(TECH_ALIASES[part][0])
        else:
            words.update(terms(part) - RESPONSE_WORDS)
    # Preserve explicit names for the existing multi-document coverage guard.
    names = {name.lower(): name for name in named_topics(value)}
    return " ".join(names.get(word, word) for word in sorted(words)) or question
