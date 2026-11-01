"""Offline composition of saved research. No retrieval, embedding or model calls."""
import copy
import html
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Chunk, Document, DocumentRevision, EvaluationRun, KnowledgeBase, ResearchReport, new_id, utcnow
from app.report_schemas import ReportContent
from app.services.access import allowed_kbs
from app.services.evidence import clean_text
from app.services.research_evaluation import digest

NOTICE = "引用绑定有效仅说明来源可追溯，不代表事实正确或语义支持充分；综合判断与研究质量仍需人工复核。"


def authorize(db, user, kb_ids):
    accessible = set(db.scalars(select(KnowledgeBase.id).where(allowed_kbs(user.id))))
    if set(kb_ids) - accessible:
        raise HTTPException(403, "来源访问权限已撤销，报告内容不可查看或导出")


def validation(db, user, content):
    """Validate original task scope, historical versions, positions and claim edges.

    Near duplicate text is deliberately unknown, never keyword-certified support.
    Deleted/expired sources are invalid; saved reports remain available with reasons.
    """
    scope = content["research"]["knowledge_base_ids"]
    authorize(db, user, scope)
    evidence = content["evidence"]
    by_id = {e["id"]: e for e in evidence}
    checks = []
    steps = {s["id"]: s for s in content["research"]["steps"]}
    duplicate = len(by_id) != len(evidence)
    for claim in content["claims"]:
        ids, sids = claim["evidence_ids"], claim["step_ids"]
        edge_valid = bool(ids and sids) and len(ids) == len(set(ids)) and all(
            sid in steps and any(sid in by_id.get(eid, {}).get("step_ids", []) and
                                eid in steps[sid].get("evidence_ids", []) for eid in ids) for sid in sids)
        edge_valid = edge_valid and all(any(sid in by_id.get(eid, {}).get("step_ids", []) and
                                           eid in steps[sid].get("evidence_ids", []) for sid in sids) for eid in ids)
        if not ids:
            checks.append({"claim_id": claim["id"], "evidence_id": None, "status": "INVALID", "reasons": ["结论没有绑定证据"]})
        for eid in ids:
            reasons, origins = [], []
            e = by_id.get(eid)
            if duplicate or not edge_valid:
                reasons.append("证据或子问题绑定无效/重复")
            if not e:
                reasons.append("引用 ID 不存在于当前研究任务")
            else:
                # The primary displayed/downloadable source is checked as well as all grouped origins.
                candidates = [e, *(e.get("origins") or [])]
                seen = set()
                for origin in candidates:
                    identity = digest({k: origin.get(k) for k in (
                        "chunk_id", "document_id", "revision_id", "knowledge_base_id", "document_name", "page", "locator")})
                    if identity in seen:
                        continue
                    seen.add(identity)
                    errors = []
                    chunk = db.get(Chunk, origin.get("chunk_id")) if origin.get("chunk_id") else None
                    doc = db.get(Document, origin.get("document_id")) if origin.get("document_id") else None
                    rev = db.get(DocumentRevision, origin.get("revision_id")) if origin.get("revision_id") else None
                    if origin.get("knowledge_base_id") not in scope or (doc and doc.knowledge_base_id not in scope):
                        errors.append("证据超出原研究资料范围")
                    if not chunk or not doc or not rev:
                        errors.append("原文档、版本或分块已失效")
                    else:
                        if not (chunk.document_id == doc.id == rev.document_id and chunk.revision_id == rev.id
                                and doc.knowledge_base_id == origin.get("knowledge_base_id") and rev.name == origin.get("document_name")):
                            errors.append("来源身份或版本不匹配")
                        loc = origin.get("locator") or {}
                        located = (isinstance(loc.get("line_start"), int) and isinstance(loc.get("line_end"), int)
                                   and 0 < loc["line_start"] <= loc["line_end"]) if not rev.name.lower().endswith(".pdf") else (
                                       isinstance(origin.get("page"), int) and origin["page"] > 0)
                        if not located or loc != (chunk.locator or {}) or origin.get("page") != chunk.page:
                            errors.append("引用位置无效或与分块不一致")
                        if doc.status == "deleting" or (doc.expires_at and doc.expires_at <= utcnow()):
                            errors.append("来源已删除或到期")
                        if not Path(rev.stored_path).is_file():
                            errors.append("版本原文文件不可用")
                    exact = bool(chunk and e.get("text") and e["text"] in clean_text(chunk.text))
                    origins.append({"chunk_id": origin.get("chunk_id"), "document_id": origin.get("document_id"),
                        "revision_id": origin.get("revision_id"), "source_sha256": rev.sha256 if rev else None,
                        "status": "INVALID" if errors else "VALID" if exact else "NEEDS_REVIEW",
                        "reasons": errors or ([] if exact else ["近重复片段无法逐字归属，需人工复核"])})
                reasons.extend(r for o in origins for r in o["reasons"] if o["status"] == "INVALID")
            status = "INVALID" if reasons else "NEEDS_REVIEW" if any(o["status"] == "NEEDS_REVIEW" for o in origins) else "VALID"
            checks.append({"claim_id": claim["id"], "evidence_id": eid, "status": status,
                           "reasons": list(dict.fromkeys(reasons)), "origins": origins})
    return {"status": "NO_CITATIONS" if not checks else "INVALID" if any(c["status"] == "INVALID" for c in checks)
            else "NEEDS_REVIEW" if any(c["status"] == "NEEDS_REVIEW" for c in checks) else "VALID",
            "valid": sum(c["status"] == "VALID" for c in checks), "total": len(checks), "checks": checks,
            "semantic_checked": False, "notice": NOTICE}


def compose(db, user, task, *, workflow=False):
    if (task.status != "completed" and not (workflow and task.status == "running" and task.phase == "writing_report")) or not task.result:
        raise HTTPException(409, "仅可从已完成且保存结果的研究任务生成报告")
    authorize(db, user, task.knowledge_base_ids)
    result = copy.deepcopy(task.result)
    snapshot = {k: copy.deepcopy(getattr(task, k)) for k in (
        "question", "plan", "steps", "knowledge_base_ids", "result", "evidence", "usage")}
    flags = result.get("conflicts", [])
    conflicted = {eid for flag in flags for eid in flag.get("evidence_ids", [])}
    claims = []
    for group in ("findings", "analysis"):
        for i, f in enumerate(result.get(group, [])):
            claims.append({"id": f"c{len(claims) + 1}", "source_pointer": f"/result/{group}/{i}",
                "kind": f["kind"], "text": f["text"], "evidence_ids": f.get("evidence_ids", []),
                "step_ids": f.get("step_ids", []), "quality_status": "POTENTIAL_CONFLICT" if
                    set(f.get("evidence_ids", [])) & conflicted else "NEEDS_REVIEW"})
    used = {eid for c in claims for eid in c["evidence_ids"]}
    evidence = [copy.deepcopy(e) for e in task.evidence if e["id"] in used]
    finding_ids = [c["id"] for c in claims if c["source_pointer"].startswith("/result/findings/")]
    analysis_ids = [c["id"] for c in claims if c["source_pointer"].startswith("/result/analysis/")]
    evaluations = []
    for run in db.scalars(select(EvaluationRun).where(EvaluationRun.user_id == user.id).order_by(EvaluationRun.created_at.desc())):
        if any(c.get("research_task_id") == task.id for c in run.cases):
            evaluations.append({"id": run.id, "created_at": run.created_at.isoformat() + "Z",
                "engineering": run.metrics.get("engineering"), "quality": run.metrics.get("quality"),
                "note": "保存既有评测状态；本次未执行或签署人工复核。"})
            if len(evaluations) >= 10:
                break
    completed = [s for s in task.steps if s.get("status") == "completed"]
    rounds = sum(len(s.get("rounds", [])) for s in task.steps)
    method = ["生成方式：按历史研究记录进行确定性整理，不新增研究、检索、实验或模型调用。",
              f"原任务保存 {len(task.steps)} 个步骤，其中状态为 completed 的步骤 {len(completed)} 个；记录检索 {rounds} 轮。"]
    method += [f"研究目标（计划）：{task.plan.get('objective', task.question)}"]
    method += [f"步骤 {s['id']}（{s.get('status', 'unknown')}）：{s['question']}；记录检索 {len(s.get('rounds', []))} 轮。" for s in task.steps]
    limitations = [*result.get("limitations", []), NOTICE,
        "报告保留历史摘录和模型判断；文字归属不证明相关性、客观真实性或不存在遗漏与冲突。",
        "未签署人工质量结论；第七阶段待复核项继续为 NEEDS_REVIEW，AI 辅助意见不视为人工确认。"]
    limitations += [f"证据缺口：{g.get('question', g.get('step_id', ''))}；{g.get('reason', '')}" for g in result.get("gaps", [])]
    limitations += [f"潜在冲突（未裁定）：{' / '.join(f.get('evidence_ids', []))}；{f.get('reason', '')}" for f in flags]
    if not claims:
        limitations.append("INSUFFICIENT_EVIDENCE：没有可引用的研究发现，无法形成事实结论。")
    def section(id, title, paragraphs=None, ids=None):
        return {"id": id, "title": title, "paragraphs": paragraphs or [], "claim_ids": ids or []}
    content = {"schema_version": "research-report-v1", "task_id": task.id,
        "task_sha256": digest(snapshot), "title": "研究报告：" + task.question,
        "generation": {"method": "deterministic", "generator": "research-report-v1", "model": None,
            "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_cost_cny": 0,
            "source_model_configuration": copy.deepcopy(task.usage.get("configuration")),
            "source_provider": result.get("provider")},
        "research": {**snapshot, "created_at": task.created_at.isoformat() + "Z", "updated_at": task.updated_at.isoformat() + "Z"},
        "claims": claims, "evidence": evidence, "evaluations": evaluations,
        "quality_status": "QUALITY_PENDING_HUMAN_REVIEW", "validation": {},
        "sections": [section("abstract", "研究标题与摘要", [f"研究主题：{task.question}",
            f"本报告整理历史记录中的 {len(finding_ids)} 项原文发现、{len(analysis_ids)} 项综合判断。所有内容保留来源与复核状态。"], (analysis_ids or finding_ids)[:2]),
            section("background", "研究背景与研究问题", [task.question, "范围仅限原任务保存的知识库与证据，不补写资料以外的背景事实。"]),
            section("method", "研究方法与过程", method),
            section("findings", "主要研究发现", [] if finding_ids else ["证据不足：没有保存可引用的主要发现。"], finding_ids),
            section("discussion", "综合分析与讨论", ["以下为历史模型综合判断，语义支持仍需人工复核。"] if analysis_ids else ["原任务没有保存综合判断，报告不补造分析。"], analysis_ids),
            section("conclusion", "研究结论", ["以下按原记录复述，综合判断未获人工确认；不能视为已证实结论。"] if claims else ["证据不足，无法形成有依据的事实结论。"], analysis_ids or finding_ids),
            section("limitations", "研究局限", limitations),
            section("references", "参考资料与引用", ["仅展示报告实际引用的历史证据；未被引用的检索结果不列作参考依据。"])]}
    content["validation"] = validation(db, user, content)
    by_id = {e["id"]: e for e in evidence}
    for c in claims:
        checks = [v for v in content["validation"]["checks"] if v["claim_id"] == c["id"]]
        if not checks or any(v["status"] == "INVALID" for v in checks):
            c["quality_status"] = "INSUFFICIENT_EVIDENCE"
        elif c["quality_status"] != "POTENTIAL_CONFLICT" and c["kind"] == "evidence" and all(v["status"] == "VALID" for v in checks) and any(
                c["text"] in by_id[eid]["text"] for eid in c["evidence_ids"] if eid in by_id):
            c["quality_status"] = "TEXT_ATTRIBUTED"
    return ReportContent.model_validate(content).model_dump()


def md(text):
    # Quotes may contain untrusted Markdown/HTML from documents; render as literal text.
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(str(text), quote=False))


def markdown(content, report_id, version, created_at):
    lines = [f"# {md(content['title'])}", "", f"Report ID: {report_id}", f"Research Task ID: {content['task_id']}",
        f"版本: {version} · 创建时间 UTC: {created_at.isoformat()}Z",
        f"生成方式: {content['generation']['method']} · 新增模型调用: {content['generation']['model_calls']}",
        *(["验证模式: MOCK · 本地模拟报告写作；不代表真实模型输出或实际费用。"]
          if content["generation"].get("invocation_kind") == "mock" else []),
        f"原任务摘要 SHA256: {content['task_sha256']}", "质量状态: QUALITY_PENDING_HUMAN_REVIEW", "", NOTICE, "",
        f"保存时引用检查: {content['validation']['status']} · {content['validation']['valid']}/{content['validation']['total']}",
        "引用核验是保存时快照；最新来源有效性请在应用中查看，不因导出自动重新生成内容。", ""]
    claims = {c["id"]: c for c in content["claims"]}
    for section in content["sections"]:
        lines += [f"## {section['title']}", ""]
        for p in section["paragraphs"]:
            lines += [md(p), ""]
        for cid in section["claim_ids"]:
            c = claims[cid]
            lines += [f"**{cid} · {c['quality_status']}** · 原始位置: {c['source_pointer']}", ""]
            lines += ["> " + md(line) for line in c["text"].splitlines()]
            lines += ["", "引用: " + " / ".join(md(eid) for eid in c["evidence_ids"]), ""]
        if section["id"] == "references":
            for e in content["evidence"]:
                lines += [f"### {md(e['id'])} · {md(e.get('document_name', '未知来源'))}", "",
                    f"Document ID: {md(e.get('document_id'))} · Revision ID: {md(e.get('revision_id'))}",
                    f"Chunk ID: {md(e.get('chunk_id'))} · Page: {e.get('page')} · Locator: {md(e.get('locator'))}", ""]
                lines += ["> " + md(line) for line in e["text"].splitlines()]
                for origin in e.get("origins", []):
                    lines += ["", "合并来源: " + md({k: origin.get(k) for k in (
                        "document_id", "revision_id", "chunk_id", "document_name", "page", "locator")})]
                lines += [""]
            lines += ["### 逐结论引用绑定核验（不含语义判定）", ""]
            for check in content["validation"]["checks"]:
                lines += [md(f"{check['claim_id']} → {check['evidence_id']}: {check['status']}；{check['reasons']}"), ""]
                for origin in check.get("origins", []):
                    lines += [md(origin), ""]
            for evaluation in content["evaluations"]:
                lines += ["既有评测快照: " + md(evaluation), ""]
    return "\n".join(lines)


def checksum(content, text):
    return digest({"content": content, "markdown": text})


def verify_saved(report):
    if checksum(report.content, report.markdown) != report.content_sha256:
        raise HTTPException(409, "报告内容完整性检查失败，请保留原版本并重新生成")
    ReportContent.model_validate(report.content)


def save_version(db, user, task, content, request_id):
    """Append an immutable version in the caller's transaction; never commit a task here."""
    from sqlalchemy import func
    old = db.scalar(select(ResearchReport).where(ResearchReport.user_id == user.id,
                                                ResearchReport.request_id == request_id))
    if old:
        if old.task_id != task.id:
            raise ValueError("Report request ID belongs to a different task")
        return old
    authorize(db, user, task.knowledge_base_ids)
    content = ReportContent.model_validate(content).model_dump()
    version = (db.scalar(select(func.max(ResearchReport.version)).where(ResearchReport.task_id == task.id)) or 0) + 1
    stamp, report_id = utcnow(), new_id()
    text = markdown(content, report_id, version, stamp)
    row = ResearchReport(id=report_id, user_id=user.id, task_id=task.id, request_id=request_id,
        version=version, title=content["title"], content=content, markdown=text,
        content_sha256=checksum(content, text), created_at=stamp, updated_at=stamp)
    db.add(row)
    db.flush()
    return row
