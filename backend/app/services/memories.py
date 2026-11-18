"""Explicit private memory lifecycle. No model, vector store or chat writes."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.memory_models import MemoryOperation, MemoryRevision, PersonalMemory
from app.models import Chunk, Conversation, Document, DocumentRevision, Message, new_id, utcnow
from app.services.access import require_kb


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def source_snapshot(db, user, reference):
    """Resolve only supported origins, validate live ACLs, pin original version."""
    kind = reference["type"]
    if kind == "user_input":
        return {"type": kind}, "available"
    sid, version = reference["id"], reference["version"]
    if kind == "user_message":
        msg = db.get(Message, sid)
        conv = db.get(Conversation, msg.conversation_id) if msg else None
        if not msg or not conv or conv.user_id != user.id or msg.role != "user":
            raise HTTPException(422, "来源不可用")
        # User messages have no revision entity: immutable content digest is the version.
        if version != digest(msg.content):
            raise HTTPException(422, "来源不可用")
        return {"type": kind, "id": sid, "version": version,
                "conversation_id": conv.id}, "available"
    if kind != "document_chunk":
        raise HTTPException(422, "来源不可用")
    chunk = db.get(Chunk, sid)
    doc = db.get(Document, chunk.document_id) if chunk else None
    rev = db.get(DocumentRevision, version)
    if not chunk or not doc or not rev:
        raise HTTPException(422, "来源不可用")
    require_kb(db, user, doc.knowledge_base_id)
    if (chunk.revision_id != rev.id or rev.document_id != doc.id
            or doc.status == "deleting" or rev.status != "ready"
            or (doc.expires_at and doc.expires_at <= utcnow()) or not Path(rev.stored_path).is_file()):
        raise HTTPException(422, "来源不可用")
    result = {"type": kind, "id": sid, "version": version, "document_id": doc.id,
              "knowledge_base_id": doc.knowledge_base_id, "locator": chunk.locator,
              "page": chunk.page, "source_sha256": rev.sha256,
              "binding_digest": digest([chunk.text, chunk.locator, chunk.page, rev.sha256])}
    return result, "available" if doc.active_revision_id == version else "available_original_version"


def availability(db, user, memory):
    try:
        current, status = source_snapshot(db, user, memory.source)
        return status if current == memory.source else "unavailable"
    except (HTTPException, OSError, ValueError, KeyError, TypeError):
        return "unavailable"


def owned(db, user, memory_id):
    row = db.scalar(select(PersonalMemory).where(PersonalMemory.id == memory_id,
                                                 PersonalMemory.owner_id == user.id))
    if row is None:
        raise HTTPException(404, "记忆不存在")
    return row


def revision_payload(rev):
    return {"version": rev.version, "kind": rev.kind, "content": rev.content,
            "reason": rev.reason, "confirmation": rev.confirmation,
            "confirmed_by": rev.actor_id if rev.confirmed_at else None,
            "confirmed_at": rev.confirmed_at, "actor_id": rev.actor_id,
            "created_at": rev.created_at, "evidence_status": "unverified"}


def payload(db, user, memory):
    status = availability(db, user, memory)
    result = {"id": memory.id, "version": memory.version, "status": memory.status,
              "confirmation": "confirmed" if memory.status == "active" else "revoked",
              "source_availability": status, "evidence_status": "unverified",
              "created_at": memory.created_at, "updated_at": memory.updated_at}
    if status != "unavailable":
        rev = db.get(MemoryRevision, (memory.id, memory.version))
        result.update(revision=revision_payload(rev), source=memory.source)
    return result


def operation(db, user, request_id):
    return db.scalar(select(MemoryOperation).where(MemoryOperation.owner_id == user.id,
                                                  MemoryOperation.request_id == str(request_id)))


def replay(db, user, op, action, memory_id, signature, expected_version=None):
    if op.action != action or (memory_id is not None and op.memory_id != memory_id):
        raise HTTPException(409, "请求标识冲突")
    if op.digest is None:
        if action == "delete":
            if expected_version != op.version - 1:
                raise HTTPException(409, "请求标识冲突")
            return None
        raise HTTPException(410, "记忆已永久删除")
    if op.digest != signature:
        raise HTTPException(409, "请求标识冲突")
    result = payload(db, user, owned(db, user, op.memory_id))
    result.update(operation_version=op.version, replayed=True)
    return result


def mutate(db, user, action, body, memory_id=None):
    signature = digest({"action": action, "memory_id": memory_id, "body": body.model_dump(mode="json")})
    old_op = operation(db, user, body.request_id)
    if old_op:
        return replay(db, user, old_op, action, memory_id, signature, getattr(body, "expected_version", None))
    stamp = utcnow()
    try:
        if action == "create":
            try:
                source, _ = source_snapshot(db, user, body.source.model_dump(mode="json"))
            except (HTTPException, OSError):
                raise HTTPException(422, "来源不可用") from None
            memory = PersonalMemory(id=new_id(), owner_id=user.id, version=1, status="active",
                                    source=source, created_at=stamp, updated_at=stamp)
            db.add(memory)
            db.flush()
            memory_id, version = memory.id, 1
        else:
            memory = owned(db, user, memory_id)
            if memory.version != body.expected_version:
                raise HTTPException(409, "记忆版本冲突，请重新读取")
            if action == "edit" and availability(db, user, memory) == "unavailable":
                raise HTTPException(409, "来源不可用，不能编辑或重新确认")
            if action == "revoke" and memory.status == "withdrawn":
                raise HTTPException(409, "记忆已撤销")
            version = memory.version + 1
            condition = (PersonalMemory.id == memory_id, PersonalMemory.owner_id == user.id,
                         PersonalMemory.version == body.expected_version)
            if action == "delete":
                changed = db.execute(delete(PersonalMemory).where(*condition))
                # Remove request fingerprints too; keep only content-free deduplication tombstones.
                db.execute(update(MemoryOperation).where(MemoryOperation.owner_id == user.id,
                    MemoryOperation.memory_id == memory_id).values(digest=None))
            else:
                changed = db.execute(update(PersonalMemory).where(*condition).values(version=version,
                    status="withdrawn" if action == "revoke" else "active", updated_at=stamp))
            if changed.rowcount != 1:
                raise HTTPException(409, "记忆版本冲突，请重新读取")
        if action != "delete":
            previous = db.get(MemoryRevision, (memory_id, version - 1)) if action == "revoke" else None
            db.add(MemoryRevision(memory_id=memory_id, version=version,
                kind=previous.kind if previous else body.kind,
                content=previous.content if previous else body.content,
                reason="" if action in {"create", "revoke"} else body.reason,
                confirmation="revoked" if action == "revoke" else "confirmed",
                actor_id=user.id, confirmed_at=None if action == "revoke" else stamp, created_at=stamp))
        db.add(MemoryOperation(owner_id=user.id, request_id=str(body.request_id), memory_id=memory_id,
            action=action, version=version, digest=None if action == "delete" else signature, created_at=stamp))
        db.commit()
    except IntegrityError:
        db.rollback()
        old_op = operation(db, user, body.request_id)
        if old_op:
            return replay(db, user, old_op, action, None if action == "create" else memory_id, signature,
                          getattr(body, "expected_version", None))
        raise HTTPException(409, "记忆写入冲突，请重新读取") from None
    except BaseException:
        db.rollback()
        raise
    if action == "delete":
        return None
    db.expire_all()
    result = payload(db, user, owned(db, user, memory_id))
    result.update(operation_version=version, replayed=False)
    return result
