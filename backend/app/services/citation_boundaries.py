"""One contextual boundary policy for normalization, checking and renumbering.

An isolated ``name[n]`` remains ambiguous. Attached references require prose
context; code regions, indexing language and expression syntax take precedence.
Use a complete prose sentence and a separated reference for ambiguous labels.
"""
import re

_BRACKET = re.compile(r"\[(\d+)\]")
_INDEX_CONTEXT = re.compile(
    r"数组|下标|索引|元素|变量|赋值|读取|访问|\b(?:array|subscript|index|element|variable|code|expression|return|print)\b", re.I)
_PROSE_CONTEXT = re.compile(r"是|为|用于|使用|采用|输入|输出|生成|通过|描述|表示|支持|说明|提升|进行|结合|包含|得到|提供|属于|称为")
_ENGLISH_PROSE = re.compile(r"\b(?:is|are|was|were|uses|supports|produces|provides|improves|feeds|requires|describes)\b", re.I)
_EXPLICIT_INDEX_CONTEXT = re.compile(r"(?:数组|下标|索引|元素|变量)\s*$|\b(?:array|subscript|index|element|variable)\s*$", re.I)
_CODE_STATEMENT = re.compile(r"(?:^|[:：])\s*(?:[A-Za-z_][\w.]*\s*(?:=|:=)|return\b|print\s*\()")
_VALUE_END = re.compile(
    r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?:\s*(?:ms|mm|cm|km|kWh|Wh|mAh|Ah|kVA|kW|MPa|kPa|Hz|kg|h|min|s|m|V|A|W))?$")
_STANDARD_END = re.compile(r"(?<![A-Za-z0-9_])[A-Z]{1,4}/[A-Z]{1,4}\s*\d+(?:[.-]\d+)*$")


def code_regions(text):
    """Protect inline, fenced (including unfinished) and indented code."""
    regions, fence, start, offset = [], None, 0, 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                regions.append((start, offset + len(line)))
                fence = None
        elif marker:
            fence, start = marker[1], offset
        elif line.startswith(("    ", "\t")):
            regions.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        regions.append((start, len(text)))
    for match in re.finditer(r"(?<!`)(`+)(?!`)[^\n]*?\1(?!`)", text):
        if not any(a <= match.start() < b for a, b in regions):
            regions.append(match.span())
    for match in re.finditer(r"`+", text):
        if not any(a <= match.start() < b for a, b in regions):
            end = text.find("\n", match.end())
            regions.append((match.start(), len(text) if end < 0 else end))
    return sorted(regions)


def citation_matches(text):
    regions = code_regions(text)
    context = list(text)
    for a, b in regions:
        context[a:b] = ["\n" if char == "\n" else " " for char in text[a:b]]
    context = "".join(context)
    previous = None
    for match in _BRACKET.finditer(text):
        start, end = match.span()
        if any(a <= start < b for a, b in regions) or text[end:end + 1] == "(":
            previous = (end, False)
            continue
        prefix = context[:start]
        clause = re.split(r"[。！？；;\n，,]", prefix)[-1]
        token = re.search(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*$", clause)
        attached = bool(start and re.match(r"[A-Za-z0-9_]", text[start - 1]))
        if start and text[start - 1] == "]":
            accepted = previous == (start, True)
        elif start and text[start - 1] in ")}'\"":
            accepted = False  # Calls, grouped expressions and literals indexed directly.
        elif _CODE_STATEMENT.search(clause):
            accepted = False
        elif attached and (_VALUE_END.search(prefix) or _STANDARD_END.search(prefix)):
            accepted = True
        elif token and (attached and (_INDEX_CONTEXT.search(clause) or re.search(r"[=+*/()]|:=|->", clause))
                        or not attached and _EXPLICIT_INDEX_CONTEXT.search(clause[:token.start()])):
            # Whitespace does not make ``return arr [1]`` a reference either.
            accepted = False
        elif token and re.fullmatch(r"\s*[A-Za-z_][A-Za-z0-9_.-]*\s*", clause):
            accepted = False  # Even ``arr [2]`` is valid indexing syntax.
        elif not attached:
            accepted = True
        else:
            tail = re.sub(r"^[ \t]*(?:\[\d+\][ \t]*)*", "", text[end:])
            sentence_boundary = not tail or tail[0] in "。！？；;,.!?，：:\n"
            plain_word = token and re.fullmatch(r"[A-Za-z]+", token[1])
            prose = (len(re.findall(r"[\u4e00-\u9fff]", clause)) >= 2 and _PROSE_CONTEXT.search(clause)
                     or len(re.findall(r"\b[A-Za-z]+\b", clause)) >= 3 and _ENGLISH_PROSE.search(clause))
            accepted = bool(plain_word and sentence_boundary and prose)
        previous = (end, accepted)
        if accepted:
            yield match


class _CitationPattern:
    """The existing small regex-style API, backed by the same parsed spans."""

    def finditer(self, text):
        return citation_matches(text)

    def findall(self, text):
        return [match[1] for match in self.finditer(text)]

    def search(self, text):
        return next(self.finditer(text), None)

    def fullmatch(self, text):
        match = self.search(text)
        return match if match and match.span() == (0, len(text)) else None

    def sub(self, replacement, text):
        pieces, last = [], 0
        for match in self.finditer(text):
            pieces.extend((text[last:match.start()], replacement(match) if callable(replacement) else match.expand(replacement)))
            last = match.end()
        return "".join([*pieces, text[last:]])


CITATION_RE = _CitationPattern()


def normalize_citations(text):
    # First canonicalize exactly the spans the checker and compactor recognize.
    text = CITATION_RE.sub(lambda m: (" " if m.start() and re.match(r"[A-Za-z0-9_]", text[m.start() - 1]) else "")
                           + m.group(), text)
    # Existing post-punctuation references move before their punctuation. Do not
    # move code text or references on a different paragraph/list item.
    spans = {match.span() for match in CITATION_RE.finditer(text)}
    return re.sub(r"([。！？；;])[ \t]*((?:\[\d+\][ \t]*)+)",
                  lambda m: " " + m[2].strip() + m[1]
                  if all((m.start(2) + n.start(), m.start(2) + n.end()) in spans for n in _BRACKET.finditer(m[2]))
                  else m.group(), text)


def claim_lines(text, sentences=False):
    """Split lines/sentences only outside code, retaining code verbatim."""
    regions = code_regions(text)
    start = 0
    for match in re.finditer(r"(?<=[。！？；;])" if sentences else r"\n", text):
        if not any(a < match.start() < b for a, b in regions):
            yield text[start:match.start()]
            start = match.end()
    yield text[start:]
