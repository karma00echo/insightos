"""Explicit decisions: CAS, atomic revisions and body-free replay tombstones."""

import copy

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.decision_models import Decision, DecisionBasis, DecisionMutation, DecisionRevision
from app.models import new_id, utcnow
from app.services import decision_basis
from app.services.memories import digest

CONTENT_FIELDS = ("question", "options", "selected_option", "reasons", "assumptions", "review_conditions")


def owned(db, user, decision_id):
    row = db.scalar(select(Decision).where(Decision.id == decision_id, Decision.owner_id == user.id))
    if row is None:
        raise HTTPException(404, "决定不存在")
    return row


def revision_payload(db, user, row, version):
    rev = db.get(DecisionRevision, (row.id, version))
    if rev is None:
        raise HTTPException(404, "版本不存在")
    refs = decision_basis.references(db, row.id, version)
    available = decision_basis.availability(db, user, refs)
    result = {
        "version": version,
        "status": rev.status,
        "created_at": rev.created_at,
        "revision_position": "current" if version == row.current_version else "superseded",
        "confirmation": "confirmed" if rev.status == "active" else "revoked",
        "availability": available,
        "evidence_status": "unverified",
    }
    if available != "unavailable":
        result.update(content=rev.content, basis=refs, actor_id=rev.actor_id, confirmed_at=rev.confirmed_at)
    return result


def payload(db, user, row):
    return {
        "id": row.id,
        "current_version": row.current_version,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "revision": revision_payload(db, user, row, row.current_version),
    }


def operation(db, user, key):
    return db.scalar(
        select(DecisionMutation).where(DecisionMutation.owner_id == user.id, DecisionMutation.request_id == str(key))
    )


def replay(db, user, op, action, decision_id, signature, expected):
    if op.action != action or (decision_id is not None and op.decision_id != decision_id):
        raise HTTPException(409, "请求标识冲突")
    if op.digest is None:
        if action == "delete":
            if expected != op.version - 1:
                raise HTTPException(409, "请求标识冲突")
            return None
        raise HTTPException(410, "决定已永久删除")
    if op.digest != signature:
        raise HTTPException(409, "请求标识冲突")
    result = payload(db, user, owned(db, user, op.decision_id))
    result.update(operation_version=op.version, replayed=True)
    return result


def mutate(db, user, action, body, decision_id=None):
    signature = digest({"action": action, "decision_id": decision_id, "body": body.model_dump(mode="json")})
    expected = getattr(body, "expected_version", None)
    old = operation(db, user, body.request_id)
    if old:
        return replay(db, user, old, action, decision_id, signature, expected)
    stamp = utcnow()
    try:
        refs = []
        if action == "create":
            refs = decision_basis.validate(db, user, body.basis) or [{"type": "user_input"}]
            row = Decision(
                id=new_id(), owner_id=user.id, current_version=1, status="active", created_at=stamp, updated_at=stamp
            )
            db.add(row)
            db.flush()
            decision_id, version = row.id, 1
        else:
            if action == "delete":
                # Serialize source-bound Action approvals with this permanent deletion.
                db.execute(update(Decision).where(Decision.id == decision_id, Decision.owner_id == user.id)
                           .values(current_version=Decision.current_version))
            row = owned(db, user, decision_id)
            if row.current_version != expected:
                raise HTTPException(409, "决定版本冲突，请重新读取")
            refs = decision_basis.references(db, row.id, expected)
            if action == "edit":
                if decision_basis.availability(db, user, refs) == "unavailable":
                    raise HTTPException(409, "依据不可用，不能编辑或重新确认")
                for ref in decision_basis.validate(db, user, body.basis):
                    if ref not in refs:
                        refs.append(ref)
                if len(refs) > 100:
                    raise HTTPException(422, "依据数量超过上限")
            if action == "revoke" and row.status == "withdrawn":
                raise HTTPException(409, "决定已撤销")
            version = expected + 1
            conditions = (
                Decision.id == decision_id,
                Decision.owner_id == user.id,
                Decision.current_version == expected,
            )
            if action == "delete":
                from app.services.actions import purge_decision_content

                purge_decision_content(db, decision_id)
                changed = db.execute(delete(Decision).where(*conditions))
                db.execute(
                    update(DecisionMutation)
                    .where(DecisionMutation.owner_id == user.id, DecisionMutation.decision_id == decision_id)
                    .values(digest=None)
                )
            else:
                changed = db.execute(
                    update(Decision)
                    .where(*conditions)
                    .values(
                        current_version=version,
                        status="withdrawn" if action == "revoke" else "active",
                        updated_at=stamp,
                    )
                )
            if changed.rowcount != 1:
                raise HTTPException(409, "决定版本冲突，请重新读取")
        if action != "delete":
            if action == "revoke":
                content = copy.deepcopy(db.get(DecisionRevision, (decision_id, expected)).content)
                content["change_reason"] = "user_revoked"
            else:
                submitted = body.model_dump(mode="json")
                content = {key: submitted[key] for key in CONTENT_FIELDS}
                content["change_reason"] = getattr(body, "reason", "")
            db.add(
                DecisionRevision(
                    decision_id=decision_id,
                    version=version,
                    status="withdrawn" if action == "revoke" else "active",
                    content=content,
                    actor_id=user.id,
                    confirmed_at=None if action == "revoke" else stamp,
                    created_at=stamp,
                )
            )
            db.flush()
            for i, ref in enumerate(refs):
                db.add(
                    DecisionBasis(decision_id=decision_id, version=version, ordinal=i, kind=ref["type"], reference=ref)
                )
        db.add(
            DecisionMutation(
                owner_id=user.id,
                request_id=str(body.request_id),
                decision_id=decision_id,
                action=action,
                version=version,
                digest=None if action == "delete" else signature,
                created_at=stamp,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        old = operation(db, user, body.request_id)
        if old:
            return replay(db, user, old, action, None if action == "create" else decision_id, signature, expected)
        raise HTTPException(409, "决定写入冲突，请重新读取") from None
    except BaseException:
        db.rollback()
        raise
    if action == "delete":
        return None
    db.expire_all()
    result = payload(db, user, owned(db, user, decision_id))
    result.update(operation_version=version, replayed=False)
    return result


def search(db, user, body):
    # Authorize before inspecting any protected text, including for match counts.
    rows = db.scalars(select(Decision).where(
        Decision.owner_id == user.id, Decision.status == body.status
    ).order_by(Decision.updated_at.desc(), Decision.id))
    matches = []
    terms = body.query.casefold().split()
    for row in rows:
        value = payload(db, user, row)
        rev = value["revision"]
        if rev["availability"] == "unavailable":
            continue
        content = rev["content"]
        chosen = next(o["label"] for o in content["options"] if o["id"] == content["selected_option"])
        text = " ".join([content["question"], chosen, *content["reasons"]]).casefold()
        if all(term in text for term in terms):
            matches.append(value)
    return {"items": matches[body.offset:body.offset + body.limit], "offset": body.offset, "limit": body.limit}


def reference_context(db, user, references):
    result = []
    for ref in references:
        try:
            row = owned(db, user, str(ref.id))
            if row.status != "active" or row.current_version != ref.version:
                raise ValueError()
            value = payload(db, user, row)
            if value["revision"]["availability"] == "unavailable":
                raise ValueError()
        except (HTTPException, ValueError):
            raise HTTPException(422, "引用决定不可用或版本已变化，请重新选择") from None
        result.append({"id": row.id, "version": row.current_version, "content": value["revision"]["content"]})
    import json
    if len(json.dumps(result, ensure_ascii=False)) > 6000:
        raise HTTPException(422, "引用内容过长，请减少引用")
    return result
