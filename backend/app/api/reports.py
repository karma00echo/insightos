"""Immutable owner-scoped report versions, stable UTF-8 export and live validation."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DB, CurrentUser
from app.api.research import owned
from app.models import ResearchReport, new_id, utcnow
from app.report_schemas import ReportCreate
from app.services import reports

router = APIRouter(prefix="/api/research-reports", tags=["research-reports"])


def get_owned(db, user, report_id):
    report = db.scalar(select(ResearchReport).where(ResearchReport.id == report_id, ResearchReport.user_id == user.id))
    if not report:
        raise HTTPException(404, "报告不存在")
    # Check both saved and original scopes to prevent bypass via stale/modified metadata.
    task = owned(db, user, report.task_id)
    reports.authorize(db, user, task.knowledge_base_ids)
    reports.authorize(db, user, report.content["research"]["knowledge_base_ids"])
    return report


def payload(report, db=None, user=None):
    base = {"id": report.id, "task_id": report.task_id, "title": report.title, "version": report.version,
        "created_at": report.created_at.isoformat() + "Z", "updated_at": report.updated_at.isoformat() + "Z",
        "quality_status": report.content["quality_status"], "citation_status": report.content["validation"]["status"]}
    if db is not None:
        reports.verify_saved(report)
        base.update(content=report.content, content_sha256=report.content_sha256,
                    current_validation=reports.validation(db, user, report.content))
    return base


@router.post("", status_code=201)
def create(body: ReportCreate, db: DB, user: CurrentUser, request: Request):
    old = db.scalar(select(ResearchReport).where(ResearchReport.user_id == user.id, ResearchReport.request_id == body.request_id))
    if old:
        if old.task_id != body.task_id:
            raise HTTPException(409, "报告请求标识冲突")
        return payload(get_owned(db, user, old.id), db, user)
    task = owned(db, user, body.task_id)
    from app.services.admission import consume
    consume(request.app.state.database, "research-report:" + user.id, 10, 60)
    try:
        content = reports.compose(db, user, task)
    except (ValidationError, ValueError, KeyError, TypeError):
        raise HTTPException(422, "历史研究结构不符合报告契约，未保存新报告；原记录和版本已保留") from None
    version = (db.scalar(select(func.max(ResearchReport.version)).where(ResearchReport.task_id == task.id)) or 0) + 1
    stamp, report_id = utcnow(), new_id()
    text = reports.markdown(content, report_id, version, stamp)
    report = ResearchReport(id=report_id, user_id=user.id, task_id=task.id, request_id=body.request_id,
        version=version, title=content["title"], content=content, markdown=text,
        content_sha256=reports.checksum(content, text), created_at=stamp, updated_at=stamp)
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        old = db.scalar(select(ResearchReport).where(ResearchReport.user_id == user.id, ResearchReport.request_id == body.request_id))
        if old and old.task_id == task.id:
            return payload(get_owned(db, user, old.id), db, user)
        raise HTTPException(409, "报告版本发生并发冲突，请重试；原版本已保留") from None
    return payload(report, db, user)


@router.get("")
def list_reports(db: DB, user: CurrentUser, task_id: str | None = None):
    query = select(ResearchReport).where(ResearchReport.user_id == user.id)
    if task_id:
        owned(db, user, task_id)
        query = query.where(ResearchReport.task_id == task_id)
    output = []
    for report in db.scalars(query.order_by(ResearchReport.created_at.desc()).limit(100)):
        try:
            get_owned(db, user, report.id)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        output.append(payload(report))
    return output


@router.get("/{report_id}")
def get_report(report_id: str, db: DB, user: CurrentUser):
    return payload(get_owned(db, user, report_id), db, user)


@router.get("/{report_id}/export")
def export_report(report_id: str, db: DB, user: CurrentUser, format: str = "markdown"):
    if format != "markdown":
        raise HTTPException(422, "当前支持 Markdown 导出；PDF 尚未实现")
    report = get_owned(db, user, report_id)
    reports.verify_saved(report)
    current = reports.validation(db, user, report.content)
    return Response(report.markdown.encode("utf8"), media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="research-report-{report.id}-v{report.version}.md"',
        "Cache-Control": "no-store", "X-Report-SHA256": report.content_sha256,
        "X-Current-Citation-Status": current["status"]})
