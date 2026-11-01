"""Owner-scoped offline evaluations stored in the existing evaluation_runs table."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.dependencies import DB, CurrentUser
from app.api.research import owned
from app.api.routes import evaluation_payload
from app.models import Chunk, Document, DocumentRevision, EvaluationRun, KnowledgeBase
from app.research_evaluation_schemas import ResearchEvaluationRequest
from app.services.access import allowed_kbs
from app.services.research import visible_task
from app.services.research_evaluation import evaluate_research

router = APIRouter(prefix="/api/research-evaluations", tags=["research-evaluation"])


@router.post("", status_code=201)
def create(body: ResearchEvaluationRequest, db: DB, user: CurrentUser, request: Request):
    saved = owned(db, user, body.task_id)
    task = visible_task(db, user, saved)
    if task["restricted"]:
        raise HTTPException(403, "来源访问权限已撤销")
    comparison = None
    if body.comparison_task_id:
        comparison = visible_task(db, user, owned(db, user, body.comparison_task_id))
        if comparison["restricted"]:
            raise HTTPException(403, "对比任务的来源权限已撤销")
    from app.services.admission import consume
    consume(request.app.state.database, "research-evaluation:" + user.id, 10, 60)
    ids = {o.get("chunk_id") for e in task["evidence"] for o in (e.get("origins") or [e])}
    sources = {}
    if ids:
        for chunk, doc, revision in db.execute(select(Chunk, Document, DocumentRevision).join(
            Document, Document.id == Chunk.document_id).join(DocumentRevision,
            DocumentRevision.id == Chunk.revision_id).where(Chunk.id.in_(ids),
            Document.knowledge_base_id.in_(saved.knowledge_base_ids), Document.knowledge_base_id.in_(
                select(KnowledgeBase.id).where(allowed_kbs(user.id))))):
            sources[chunk.id] = {"text": chunk.text, "document_id": doc.id, "document_name": revision.name,
                "revision_id": chunk.revision_id, "knowledge_base_id": doc.knowledge_base_id,
                "page": chunk.page, "locator": chunk.locator, "source_sha256": revision.sha256.lower()}
    try:
        result = evaluate_research(body.case.model_dump(), task, sources, benchmark_version=body.benchmark_version,
                                   review=body.review.model_dump() if body.review else None, comparison_task=comparison)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    run = EvaluationRun(user_id=user.id, name=body.name, status="completed", use_ragas=False,
        knowledge_base_ids=saved.knowledge_base_ids, cases=[{"research_case": body.case.model_dump(),
        "research_task_id": saved.id, "benchmark_version": body.benchmark_version}], results=[result],
        metrics={"kind": "research_offline", "engineering": result["engineering"]["status"],
                 "quality": result["quality_status"], "new_model_calls": 0})
    db.add(run)
    db.commit()
    db.refresh(run)
    return evaluation_payload(run, db, user)
