"""Owner-scoped durable research API, with atomic idempotency and cancellation."""
import copy

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import DB, CurrentUser
from app.models import ResearchTask, utcnow
from app.research_schemas import ResearchCreate, ResearchRetry
from app.services.corpus import CorpusScope
from app.services.research import TERMINAL, event, visible_task

router = APIRouter(prefix="/api/research", tags=["research"])


def owned(db, user, task_id):
    task = db.scalar(select(ResearchTask).where(ResearchTask.id == task_id, ResearchTask.user_id == user.id))
    if not task:
        raise HTTPException(404, "研究任务不存在")
    return task


@router.post("", status_code=202)
def create(body: ResearchCreate, db: DB, user: CurrentUser, request: Request):
    old = db.scalar(select(ResearchTask).where(ResearchTask.user_id == user.id, ResearchTask.request_id == body.request_id))
    if old:
        # Compare the explicit request scope, even when all-accessible was snapshotted.
        original = old.usage.get("request_scope", [])
        if (old.question != body.question or sorted(set(original)) != sorted(set(body.knowledge_base_ids)) or
                old.usage.get("generate_report", False) != body.generate_report):
            raise HTTPException(409, "相同请求标识已用于不同研究问题或范围")
        return visible_task(db, user, old)
    scope = CorpusScope(db, user, body.knowledge_base_ids)
    from app.services.admission import consume
    consume(request.app.state.database, "research-create:" + user.id, 10)
    task = ResearchTask(user_id=user.id, active_user_id=user.id, question=body.question,
        request_id=body.request_id, knowledge_base_ids=scope.kb_ids, status="queued", phase="queued",
        usage={"request_scope": sorted(set(body.knowledge_base_ids)), "model_calls": 0, "reserved_tokens": 0,
               "generate_report": body.generate_report},
        events=[event("queued", "任务已保存，等待执行")])
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        old = db.scalar(select(ResearchTask).where(ResearchTask.user_id == user.id, ResearchTask.request_id == body.request_id))
        if (old and old.question == body.question and old.usage.get("request_scope", []) == sorted(set(body.knowledge_base_ids))
                and old.usage.get("generate_report", False) == body.generate_report):
            return visible_task(db, user, old)
        raise HTTPException(409, "已有研究任务正在执行，或请求标识冲突") from None
    return visible_task(db, user, task)


@router.get("")
def list_tasks(db: DB, user: CurrentUser):
    return [visible_task(db, user, task, summary=True) for task in db.scalars(select(ResearchTask)
        .where(ResearchTask.user_id == user.id).order_by(ResearchTask.created_at.desc()).limit(50))]


@router.get("/{task_id}")
def get_task(task_id: str, db: DB, user: CurrentUser):
    return visible_task(db, user, owned(db, user, task_id))


@router.post("/{task_id}/cancel")
def cancel(task_id: str, db: DB, user: CurrentUser):
    task = owned(db, user, task_id)
    if task.status not in TERMINAL:
        steps = copy.deepcopy(task.steps)
        for step in steps:
            if step["status"] in {"queued", "running"}:
                step["status"] = "cancelled"
        db.execute(update(ResearchTask).where(ResearchTask.id == task.id, ResearchTask.status.in_(["queued", "running"]))
            .values(status="cancelled", phase="cancelled", active_user_id=None, updated_at=utcnow(), steps=steps,
                    events=[*task.events, event("cancelled", "用户取消；已发出的模型请求可能仍计费，后续结果不会写入")]))
        db.commit()
        db.expire_all()
    return visible_task(db, user, owned(db, user, task_id))


@router.post("/{task_id}/retry", status_code=202)
def retry(task_id: str, body: ResearchRetry, db: DB, user: CurrentUser, request: Request):
    parent = owned(db, user, task_id)
    if parent.status not in TERMINAL:
        raise HTTPException(409, "当前研究尚未结束")
    old = db.scalar(select(ResearchTask).where(ResearchTask.user_id == user.id, ResearchTask.request_id == body.request_id))
    if old:
        if old.usage.get("resumed_from") != task_id:
            raise HTTPException(409, "请求标识冲突")
        return visible_task(db, user, old)
    # Validate exact saved scope; an empty snapshot stays empty, never widens to newly added KBs.
    accessible = CorpusScope(db, user, parent.knowledge_base_ids).kb_ids
    if set(accessible) != set(parent.knowledge_base_ids):
        raise HTTPException(409, "资料范围已变化，请创建新的研究任务")
    from app.services.admission import consume
    consume(request.app.state.database, "research-create:" + user.id, 10)
    task = ResearchTask(user_id=user.id, active_user_id=user.id, question=parent.question,
        request_id=body.request_id, knowledge_base_ids=parent.knowledge_base_ids, status="queued", phase="queued",
        plan=parent.plan, usage={"request_scope": parent.usage.get("request_scope", []), "resumed_from": parent.id,
                                 "model_calls": 0, "reserved_tokens": 0,
                                 "generate_report": parent.usage.get("generate_report", True)},
        events=[event("queued", "用户重新执行；复用已保存计划，重新检索当前版本")])
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        old = db.scalar(select(ResearchTask).where(ResearchTask.user_id == user.id, ResearchTask.request_id == body.request_id))
        if old and old.usage.get("resumed_from") == task_id:
            return visible_task(db, user, old)
        raise HTTPException(409, "已有研究任务正在执行") from None
    return visible_task(db, user, task)
