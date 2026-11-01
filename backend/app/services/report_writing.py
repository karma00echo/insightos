"""Evidence-bound LLM writing inside the research worker's budget and lease.

Structural/numeric checks do not certify semantic entailment. All generated
paragraphs remain NEEDS_REVIEW, even when every citation is valid.
"""
import copy
import hashlib
import re
from pathlib import Path

from app.report_schemas import ReportContent, WrittenReport
from app.services import reports
from app.services.verification import verify_answer

INSTRUCTION = (
    "报告写作：围绕研究问题，基于所给证据撰写有实质分析的完整中文研究报告。"
    "按schema输出abstract摘要、background问题背景、findings主要发现、discussion分析讨论、conclusion结论。"
    "讨论至少两段，解释来源支持的机制、差异、适用条件和对研究问题的含义，避免仅复制已有结论。"
    "每段绑定evidence_ids与step_ids，不能引用未提供证据，不能把局部材料外推为普遍规律。"
    "citation_scope是允许引用的关联表：每条证据必须与本段至少一个步骤合法关联，"
    "且每个声明步骤必须由本段至少一条证据覆盖；两方向同时满足。"
    "sources.step_ids是原始绑定，allowed_step_ids才是本次可用范围，不得按内容相似自行扩大关联。"
    "跨步骤讨论可用多条证据联合覆盖，不要求每条证据覆盖全部步骤；step_ids必须对应正文实际讨论，不能装饰性补列。"
    "research_claims是既有研究记录而非已确认事实，优先核对sources原文并保留其复核状态。"
    "没有合法证据的判断不要输出为确定结论，不编造引用；已知不足与待复核状态由服务器保留。"
    "不得编造实验、数据、来源、系统已集成的能力；资料未提及不代表不存在。"
    "保留证据范围限定与不确定性；缺口/冲突由服务器保留，不能用确定结论填补。"
    "不要输出[n]或Markdown引用，服务器生成可追溯引用；不输出思维链。"
)


class ReportWritingError(ValueError):
    """Bounded diagnostics without model text, provider errors or arbitrary IDs."""

    def __init__(self, rule, path, *, evidence_ids=(), step_ids=(), allowed_step_ids=(), input_error=False):
        self.code = "REPORT_INPUT_BINDING_INVALID" if input_error else "REPORT_BINDING_INVALID"
        def safe_ids(values):
            return [v if isinstance(v, str) and re.fullmatch(r"[es][1-9]\d{0,3}", v) else "[invalid-id]"
                    for v in list(values)[:12]]
        self.details = {"code": self.code, "rule": rule, "path": path,
            "evidence_ids": safe_ids(evidence_ids), "step_ids": safe_ids(step_ids),
            "allowed_step_ids": safe_ids(allowed_step_ids), "semantic_checked": False}
        self.message = (f"报告引用绑定检查失败：{rule}；位置 {path}；证据 {self.details['evidence_ids']}；"
                        f"步骤 {self.details['step_ids']}；允许步骤 {self.details['allowed_step_ids']}。")
        super().__init__(self.message)


def writing_contract(task, base, eligible, answered):
    """Expose the existing two-way graph, never infer edges from semantic similarity."""
    steps = {s["id"]: s for s in task.steps}
    allowed = {e["id"]: [s for s in e["step_ids"] if s in answered and e["id"] in steps[s].get("evidence_ids", [])]
               for e in eligible}
    for eid, sids in allowed.items():
        if not sids:
            raise ReportWritingError("EVIDENCE_WITHOUT_ALLOWED_STEP", "/citation_scope", evidence_ids=[eid], input_error=True)
    by_step = {s: [eid for eid, sids in allowed.items() if s in sids] for s in sorted(answered)}
    missing = [sid for sid, eids in by_step.items() if not eids]
    if missing:
        raise ReportWritingError("STEP_WITHOUT_ELIGIBLE_EVIDENCE", "/citation_scope", step_ids=missing, input_error=True)
    valid_claims = {c["id"] for c in base["claims"] if (checks := [v for v in base["validation"]["checks"]
                    if v["claim_id"] == c["id"]]) and all(v["status"] == "VALID" for v in checks)}
    context = [copy.deepcopy(c) for c in base["claims"] if c["id"] in valid_claims
               and set(c["evidence_ids"]) <= allowed.keys() and set(c["step_ids"]) <= answered]
    payload = {"question": task.question,
        "steps": [{k: s[k] for k in ("id", "question", "evidence_ids")} for s in task.steps if s["id"] in answered],
        "sources": [{**{k: e[k] for k in ("id", "text", "document_name", "step_ids")},
                     "allowed_step_ids": allowed[e["id"]]} for e in eligible],
        "citation_scope": {"allowed_evidence_steps": allowed, "allowed_step_evidence": by_step},
        "research_claims": context,
        "limitations": copy.deepcopy(task.result.get("limitations", [])), "gaps": copy.deepcopy(task.result.get("gaps", []))}
    schema = WrittenReport.model_json_schema()
    fields = schema["$defs"]["WrittenParagraph"]["properties"]
    fields["evidence_ids"]["items"]["enum"] = list(allowed)
    fields["step_ids"]["items"]["enum"] = sorted(answered)
    fields["evidence_ids"]["uniqueItems"] = fields["step_ids"]["uniqueItems"] = True
    # Portable enums/descriptions guide ID selection. The server still enforces
    # pair relationships; schema enums alone cannot express semantic support.
    return payload, schema


def validate_paragraph_bindings(paragraph, by_id, answered, steps, path):
    eids, sids = paragraph.evidence_ids, paragraph.step_ids
    def fail(rule, **values):
        raise ReportWritingError(rule, path, **({"evidence_ids": eids, "step_ids": sids} | values))
    if len(eids) != len(set(eids)) or len(sids) != len(set(sids)):
        fail("DUPLICATE_BINDING_ID")
    if not set(eids) <= by_id.keys():
        fail("EVIDENCE_ID_UNAVAILABLE", evidence_ids=[e for e in eids if e not in by_id])
    if not set(sids) <= answered:
        fail("STEP_ID_UNAVAILABLE", step_ids=[s for s in sids if s not in answered])
    for sid in sids:
        if not any(sid in by_id[e]["step_ids"] and e in steps[sid].get("evidence_ids", []) for e in eids):
            fail("STEP_WITHOUT_CITED_EVIDENCE", step_ids=[sid])
    for eid in eids:
        allowed = [s for s in by_id[eid]["step_ids"] if s in answered and eid in steps[s].get("evidence_ids", [])]
        if not set(allowed) & set(sids):
            fail("EVIDENCE_STEP_MISMATCH", evidence_ids=[eid], allowed_step_ids=allowed)


def prepare(db, user, task):
    base = reports.compose(db, user, task, workflow=True)
    # Check all candidate evidence against SQL, not merely citations in the old result.
    probe = copy.deepcopy(base)
    probe["evidence"] = copy.deepcopy(task.evidence)
    probe["claims"] = [{"id": e["id"], "evidence_ids": [e["id"]], "step_ids": e["step_ids"]}
                       for e in task.evidence]
    checked = reports.validation(db, user, probe)
    valid = {c["evidence_id"] for c in checked["checks"] if c["status"] == "VALID"}
    conflicted = {eid for f in task.result.get("conflicts", []) for eid in f.get("evidence_ids", [])}
    answered = {s["id"] for s in task.steps if s.get("evidence_ids") and not s.get("gap")}
    eligible = [copy.deepcopy(e) for e in task.evidence if e["id"] in valid - conflicted
                and set(e["step_ids"]) & answered]
    return base, eligible, answered


def apply_writing(db, user, base, eligible, answered, data, settings):
    written = WrittenReport.model_validate(data)
    by_id = {e["id"]: e for e in eligible}
    originals = {c["text"] for c in base["claims"]}
    content = copy.deepcopy(base)
    content["evidence"] = copy.deepcopy(base["research"]["evidence"])
    steps = {s["id"]: s for s in base["research"]["steps"]}
    for section in written.sections:
        ids = []
        for i, paragraph in enumerate(section.paragraphs):
            eids, sids = paragraph.evidence_ids, paragraph.step_ids
            validate_paragraph_bindings(paragraph, by_id, answered, steps, f"/sections/{section.id}/paragraphs/{i}")
            if paragraph.text in originals or re.search(r"\[\d+\]", paragraph.text):
                raise ValueError("Report writing copies old findings or supplies unbound inline citations")
            selected = [by_id[eid] for eid in eids]
            if not verify_answer(paragraph.text, selected, settings.model_copy(update={"citation_verifier": "numeric"}),
                                 source_binding=list(range(1, len(selected) + 1)))["passed"]:
                raise ValueError("Report paragraph failed numeric/citation checks")
            cid = f"w{len(content['claims']) + 1}"
            content["claims"].append({"id": cid, "source_pointer": f"/report_writing/{section.id}/{i}",
                "kind": "synthesis", "text": paragraph.text, "evidence_ids": eids, "step_ids": sids,
                "quality_status": "NEEDS_REVIEW"})
            ids.append(cid)
        target = next(s for s in content["sections"] if s["id"] == section.id)
        target.update(paragraphs=[], claim_ids=ids)
        if section.id == "findings":
            target["claim_ids"] += [c["id"] for c in base["claims"] if c["kind"] == "evidence"]
    covered = {sid for c in content["claims"] if c["id"].startswith("w") for sid in c["step_ids"]}
    if not answered <= covered:
        raise ValueError("Report writing omitted an answered research step")
    used = {eid for c in content["claims"] for eid in c["evidence_ids"]}
    content["evidence"] = [e for e in content["evidence"] if e["id"] in used]
    content["validation"] = reports.validation(db, user, content)
    if content["validation"]["status"] != "VALID":
        raise ValueError("Report source validation failed before publication")
    content["sections"][2]["paragraphs"][0] = (
        "生成方式：在已保存的研究步骤、证据与缺口上进行模型报告写作；未新增检索或实验。"
        "模型分析逐段绑定来源，语义支持仍需人工复核。")
    return ReportContent.model_validate(content).model_dump()


def _write(check_task, invoke, database, settings, *, model_available, invocation_kind):
    task = check_task()
    with database.session() as db:
        from app.models import User
        base, eligible, answered = prepare(db, db.get(User, task.user_id), task)
    if not eligible:
        if base["validation"]["status"] in {"INVALID", "NEEDS_REVIEW"}:
            raise ValueError("Unverifiable sources cannot be used for automatic report publication")
        base["generation"]["reason"] = "没有来源可核实且无缺口/冲突的证据，未调用报告写作模型。"
        return base
    if not model_available:
        base["generation"]["reason"] = "模型未配置，保存原文整理版本，不宣称AI报告写作。"
        return base
    payload, schema = writing_contract(task, base, eligible, answered)
    data, metadata = invoke(INSTRUCTION, payload, schema,
                            output_tokens=min(settings.max_output_tokens, 4096))
    task = check_task()
    with database.session() as db:
        from app.models import User
        # Revalidate access and sources after the model response.
        user = db.get(User, task.user_id)
        fresh, eligible, answered = prepare(db, user, task)
        content = apply_writing(db, user, fresh, eligible, answered, data, settings)
    usage = {k: v for k, v in metadata.items() if k in {"input_tokens", "output_tokens"} and type(v) is int and v >= 0}
    content["generation"].update(method="llm_evidence_writing", generator="research-report-writing-v1",
        model=settings.llm_model, model_calls=1, input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0), estimated_cost_cny=None,
        writer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), semantic_checked=False,
        invocation_kind=invocation_kind)
    return content


def generate(runner):
    """Worker path: reuse its budget, usage accounting, cancellation and lease."""
    from app.services.answering import llm_ready
    def invoke(*args, **kwargs):
        data = runner.call(*args, **kwargs)
        return data, runner.check().usage["calls"][-1].get("usage", {})
    return _write(runner.check, invoke, runner.database, runner.settings,
                  model_available=llm_ready(), invocation_kind="provider")


def write_saved_report(database, user_id, task_id, settings, invoke):
    """Independent Mock verification path for immutable completed research.

    No provider is constructed and nothing is saved here. The explicit callback
    supplies (structured report, simulated usage); callers persist only in an
    isolated test database. This is not a new paid production API.
    """
    from app.models import ResearchTask, User
    expected = None
    def check_task():
        with database.session() as db:
            task, user = db.get(ResearchTask, task_id), db.get(User, user_id)
            if not task or task.user_id != user_id or not user or user.disabled:
                raise PermissionError("Saved research is not available to this owner")
            if task.status != "completed" or not task.result:
                raise ValueError("Independent writing requires completed, validated research")
            reports.authorize(db, user, task.knowledge_base_ids)
            fingerprint = reports.digest({k: getattr(task, k) for k in ("question", "plan", "steps", "evidence",
                "result", "usage", "knowledge_base_ids", "status", "updated_at")})
            if expected is not None and fingerprint != expected:
                raise ValueError("Saved research changed during report verification")
            return copy.deepcopy(task), fingerprint
    _, expected = check_task()
    return _write(lambda: check_task()[0], invoke, database, settings,
                  model_available=True, invocation_kind="mock")
