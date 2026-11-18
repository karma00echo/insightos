"""Independent commitments; source loss hides content without cancelling the task."""

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.action_models import Action, ActionMutation, ActionRevision
from app.decision_models import Decision
from app.models import new_id, utcnow
from app.services import decision_basis, decisions
from app.services.memories import digest

TRANSITIONS = {
    "todo": {"in_progress", "completed", "cancelled"},
    "in_progress": {"todo", "completed", "cancelled"},
    "completed": {"todo"},
    "cancelled": {"todo"},
}


def owned(db, user, action_id):
    row = db.scalar(select(Action).where(Action.id == action_id, Action.owner_id == user.id))
    if row is None:
        raise HTTPException(404, "任务不存在")
    return row


def source_state(db, user, row):
    if row.source_decision_id is None:
        return "manual"
    decision = db.get(Decision, row.source_decision_id)
    if decision is None or decision.owner_id != user.id:
        return "unavailable"
    if decision.status != "active":
        return "withdrawn"
    if decision.current_version != row.source_version:
        return "changed"
    refs = decision_basis.references(db, decision.id, row.source_version)
    if decision_basis.availability(db, user, refs) == "unavailable":
        return "unavailable"
    return "available"


def revision_payload(db, user, row, rev):
    state = source_state(db, user, row)
    result = dict(version=rev.version, status=rev.status, operation=rev.operation,
                  actor_id=rev.actor_id, created_at=rev.created_at)
    if state in {"manual", "available"} and rev.content is not None:
        result.update(content=rev.content, due_date=rev.due_date)
    return result


def payload(db, user, row):
    result = {key: getattr(row, key) for key in (
        "id", "current_version", "status", "approved_by", "approved_at", "created_at", "updated_at",
        "completed_at", "cancelled_at",
    )}
    result["source_state"] = source_state(db, user, row)
    if result["source_state"] == "available":
        result["source"] = {"id": row.source_decision_id, "version": row.source_version}
    result["revision"] = revision_payload(db, user, row, db.get(ActionRevision, (row.id, row.current_version)))
    return result


def proposal(db, user, reference):
    # No model call or draft storage. Re-read the confirmed source, never trust client text.
    value = decisions.reference_context(db, user, [reference])[0]
    content = value["content"]
    selected = next(o["label"] for o in content["options"] if o["id"] == content["selected_option"])
    return {"source": {"id": value["id"], "version": value["version"]},
            "candidate": {"title": ("落实：" + selected)[:300], "description": "", "due_date": None},
            "method": "manual_template", "notice": "手动行动提案，请补充具体步骤；未调用模型，尚未创建任务。"}


def purge_decision_content(db, decision_id):
    """Same transaction as permanent Decision deletion; leave only task metadata."""
    # Serialize against task edits/status copies, including on PostgreSQL.
    ids = list(db.scalars(select(Action.id).where(Action.source_decision_id == decision_id).with_for_update()))
    db.execute(update(ActionRevision).where(ActionRevision.action_id.in_(ids)).values(content=None, due_date=None))
    db.execute(update(ActionMutation).where(ActionMutation.action_id.in_(ids)).values(digest=None))


def operation(db, user, key):
    return db.scalar(select(ActionMutation).where(
        ActionMutation.owner_id == user.id, ActionMutation.request_id == str(key)))


def replay(db, user, op, kind, action_id, signature, expected):
    if op.operation != kind or (action_id is not None and op.action_id != action_id):
        raise HTTPException(409, "请求标识冲突")
    if op.digest is None:
        if kind == "delete":
            if op.version != expected + 1:
                raise HTTPException(409, "请求标识冲突")
            return None
        raise HTTPException(410, "任务或来源正文已清除，请重新读取")
    if op.digest != signature:
        raise HTTPException(409, "请求标识冲突")
    result = payload(db, user, owned(db, user, op.action_id))
    return {**result, "operation_version": op.version, "replayed": True}


def mutate(db, user, kind, body, action_id=None):
    original_id = action_id
    submitted = body.model_dump(mode="json")
    # Preserve T08 replay digests when optional reason was absent or empty.
    if not submitted.get("reason"):
        submitted.pop("reason", None)
    signature = digest({"operation": kind, "id": action_id, "body": submitted})
    expected = getattr(body, "expected_version", None)
    old = operation(db, user, body.request_id)
    if old:
        return replay(db, user, old, kind, action_id, signature, expected)
    stamp = utcnow()
    try:
        content, due = None, None
        if kind == "create":
            if body.source:
                # Write reservation serializes approval against permanent source deletion.
                db.execute(update(Decision).where(Decision.id == str(body.source.id), Decision.owner_id == user.id)
                           .values(current_version=Decision.current_version))
                decisions.reference_context(db, user, [body.source])
            row = Action(id=new_id(), owner_id=user.id, current_version=1, status="todo",
                         source_decision_id=str(body.source.id) if body.source else None,
                         source_version=body.source.version if body.source else None,
                         approved_by=user.id, approved_at=stamp, created_at=stamp, updated_at=stamp)
            db.add(row)
            db.flush()
            action_id, version, state = row.id, 1, "todo"
        else:
            # Lock before reading the old revision, so deletion cannot purge and then
            # have a stale status request copy protected text back into history.
            db.execute(update(Action).where(Action.id == action_id, Action.owner_id == user.id)
                       .values(current_version=Action.current_version))
            row = owned(db, user, action_id)
            if expected != row.current_version:
                raise HTTPException(409, "任务版本冲突，请重新读取后明确提交")
            version, state = expected + 1, row.status
            rev = db.get(ActionRevision, (action_id, expected))
            content, due = rev.content, rev.due_date
            if getattr(body, "reason", "") and source_state(db, user, row) not in {"manual", "available"}:
                raise HTTPException(409, "来源失效，请清空原因后仅更新状态")
            if kind == "edit" and (source_state(db, user, row) not in {"manual", "available"} or content is None):
                raise HTTPException(409, "来源失效，正文已保护；仍可更新状态或删除")
            if kind == "status":
                if body.status not in TRANSITIONS[row.status]:
                    raise HTTPException(409, "状态转换无效；完成或取消的任务仅可重新开启为待执行")
                state = body.status
            conditions = (Action.id == action_id, Action.owner_id == user.id, Action.current_version == expected)
            if kind == "delete":
                changed = db.execute(delete(Action).where(*conditions))
                db.execute(update(ActionMutation).where(ActionMutation.action_id == action_id).values(digest=None))
            else:
                changed = db.execute(update(Action).where(*conditions).values(
                    current_version=version, status=state, updated_at=stamp,
                    completed_at=(stamp if state == "completed" else None) if kind == "status" else row.completed_at,
                    cancelled_at=(stamp if state == "cancelled" else None) if kind == "status" else row.cancelled_at))
            if changed.rowcount != 1:
                raise HTTPException(409, "任务版本冲突，请重新读取")
        if kind != "delete":
            if kind in {"create", "edit"}:
                content, due = {"title": body.title, "description": body.description}, body.due_date
            if content is not None:
                content = {key: value for key, value in content.items() if key != "change_reason"}
                if getattr(body, "reason", ""):
                    content["change_reason"] = body.reason
            db.add(ActionRevision(action_id=action_id, version=version, status=state, content=content,
                                  due_date=due, operation=kind, actor_id=user.id, created_at=stamp))
        db.add(ActionMutation(owner_id=user.id, request_id=str(body.request_id), action_id=action_id,
                              operation=kind, version=version, digest=None if kind == "delete" else signature,
                              created_at=stamp))
        db.commit()
    except IntegrityError:
        db.rollback()
        old = operation(db, user, body.request_id)
        if old:
            return replay(db, user, old, kind, original_id, signature, expected)
        raise HTTPException(409, "任务写入冲突，请重新读取") from None
    except BaseException:
        db.rollback()
        raise
    if kind == "delete":
        return None
    db.expire_all()
    return {**payload(db, user, owned(db, user, action_id)), "operation_version": version, "replayed": False}


def linked(db, user, decision_id, version, offset, limit):
    decision = decisions.owned(db, user, decision_id)
    protected = {"available": False, "counts": None, "total": None, "items": [], "offset": offset, "limit": limit}
    if decision.status != "active" or decision.current_version != version:
        return protected
    if decisions.payload(db, user, decision)["revision"]["availability"] == "unavailable":
        return protected
    rows = db.scalars(select(Action).where(
        Action.owner_id == user.id, Action.source_decision_id == decision.id, Action.source_version == version,
    ).order_by(Action.created_at.desc(), Action.id))
    values = []
    counts = dict.fromkeys(TRANSITIONS, 0)
    for row in rows:
        value = payload(db, user, row)
        if value["source_state"] != "available":
            return protected
        counts[row.status] += 1
        values.append(value)
    return {"available": True, "version": version, "counts": counts, "total": len(values),
            "items": values[offset:offset + limit], "offset": offset, "limit": limit}
