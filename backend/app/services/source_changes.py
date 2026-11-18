"""Deterministic extracted-content comparison; explicit dependencies, never impact claims."""

from difflib import SequenceMatcher
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.decision_models import Decision, DecisionBasis
from app.memory_models import MemoryRevision, PersonalMemory
from app.models import Chunk, Document, DocumentRevision, User, utcnow
from app.services import decisions, memories
from app.services.access import require_kb
from app.source_change_models import SourceChange


def source_revision(db, user, kind, sid, version):
    if kind == "memory":
        row = db.get(PersonalMemory, sid, populate_existing=True)
        if row is None or row.owner_id != user.id:
            raise HTTPException(404, "记忆不存在")
        rev = db.get(MemoryRevision, (sid, int(version)), populate_existing=True)
        if row.status != "active" or not rev or rev.confirmation != "confirmed" or memories.availability(db, user, row) == "unavailable":
            raise HTTPException(404, "来源不可用")
        return rev
    doc = db.get(Document, sid, populate_existing=True)
    if not doc:
        raise HTTPException(404, "来源不可用")
    require_kb(db, user, doc.knowledge_base_id)
    rev = db.get(DocumentRevision, version, populate_existing=True)
    if not rev or rev.document_id != sid or doc.status == "deleting" or rev.status != "ready" or (doc.expires_at and doc.expires_at <= utcnow()) or not Path(rev.stored_path).is_file():
        raise HTTPException(404, "来源不可用")
    return rev


def resolve(db, user, kind, sid, version):
    rev = source_revision(db, user, kind, sid, version)
    if kind == "memory":
        return [dict(text=rev.kind + "\n" + rev.content, locator={"revision": rev.version, "fields": ["kind", "content"]}, actor_id=rev.actor_id,
                     changed_at=rev.created_at)], True
    # Legacy cleanup removes old retrieval chunks but retains original files.
    # Text parsers do not invoke OCR/vision; never invent vanished chunk IDs.
    if Path(rev.name).suffix.lower() in {".txt", ".md", ".csv", ".json", ".html", ".htm"}:
        from app.services.parsing import parse_document

        if Path(rev.stored_path).stat().st_size > 1024 * 1024:
            return [], False
        try:
            pages = parse_document(Path(rev.stored_path), rev.name)
        except (ValueError, UnicodeError):
            return [], False
        if not pages or sum(len(p.text) for p in pages) > 100000 or not any(p.text.strip() for p in pages):
            return [], False
        return [dict(text=p.text, locator={"representation": "reparsed_original", "page": p.page,
                                          "page_semantics": "logical_text_unit"}) for p in pages], True
    chunks = list(db.scalars(select(Chunk).where(Chunk.document_id == sid, Chunk.revision_id == version)
                             .order_by(Chunk.ordinal, Chunk.id).limit(501)))
    if not chunks or len(chunks) > 500 or (rev.chunk_count and len(chunks) != rev.chunk_count) or sum(len(c.text) for c in chunks) > 100000:
        return [], False
    return [dict(text=c.text, locator={"chunk_id": c.id, "ordinal": c.ordinal, "page": c.page, "locator": c.locator}) for c in chunks], True


def compare(db, user, row):
    old, a = resolve(db, user, row.kind, row.old_id, row.old_version)
    new, b = resolve(db, user, row.kind, row.new_id, row.new_version)
    if not a or not b:
        return dict(state="incomparable", differences=[], scope="extracted_chunks", truncated=False)
    if row.kind == "document" and old[0]["locator"].get("representation") != new[0]["locator"].get("representation"):
        return dict(state="incomparable", differences=[], scope="mixed_representations", truncated=False)
    left, right = [x["text"] for x in old], [x["text"] for x in new]
    # Bounded source units; autojunk off for repeated document sections.
    diffs = []
    for tag, i, j, k, end in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag != "equal":
            diffs.append({"operation": tag, "old": old[i:j], "new": new[k:end]})
    scope = "confirmed_memory_fields" if row.kind == "memory" else "extracted_chunks"
    if row.kind == "document" and old[0]["locator"].get("representation") == "reparsed_original":
        scope = "reparsed_original_text"
    return dict(state="changed" if diffs else "unchanged", differences=diffs[:50],
                truncated=len(diffs) > 50, scope=scope)


def references(db, user, row):
    found = []
    bases = db.scalars(select(DecisionBasis).join(Decision, Decision.id == DecisionBasis.decision_id)
                      .where(Decision.owner_id == user.id).order_by(DecisionBasis.decision_id, DecisionBasis.version))
    for basis in bases:
        ref = basis.reference
        match = False
        for sid, version in [(row.old_id, row.old_version), (row.new_id, row.new_version)]:
            if row.kind == "memory":
                match |= ref.get("type") == "memory" and ref.get("id") == sid and str(ref.get("revision")) == version
            else:
                match |= ref.get("type") == "document_chunk" and ref.get("document_id") == sid and ref.get("version") == version
        if not match:
            continue
        decision = decisions.owned(db, user, basis.decision_id)
        if decisions.payload(db, user, decision)["revision"]["availability"] == "unavailable":
            continue
        if decisions.revision_payload(db, user, decision, basis.version)["availability"] == "unavailable":
            continue
        value = dict(decision_id=basis.decision_id, decision_version=basis.version, source=ref, decision_status=decision.status,
                     relationship="explicit_reference_only")
        if value not in found:
            found.append(value)
    return found


def payload(db, user, row, include_references=False):
    # No protected IDs, diff, type, count or locations survive source loss.
    try:
        result = compare(db, user, row)
    except (HTTPException, OSError, ValueError, TypeError):
        return {"id": row.id, "state": "unavailable"}
    result.update(id=row.id, kind=row.kind, old={"id": row.old_id, "version": row.old_version},
                  new={"id": row.new_id, "version": row.new_version}, detected_at=row.detected_at,
                  detected_by=row.owner_id, origin="system_detection_user_requested", detected_state=row.detected_state,
                  relationship="existing_version_chain" if row.old_id == row.new_id else "user_confirmed_pair")
    if include_references:
        refs = references(db, user, row)
        result["references"] = refs[:100]
        result["references_truncated"] = len(refs) > 100
    # Revalidate both revisions after comparison and reference lookup. They can
    # disappear or become inaccessible while those reads are in progress.
    try:
        old = source_revision(db, user, row.kind, row.old_id, row.old_version)
        new = source_revision(db, user, row.kind, row.new_id, row.new_version)
    except (HTTPException, OSError):
        return {"id": row.id, "state": "unavailable"}
    result["source_changed_at"] = new.created_at
    if row.kind == "document":
        result["binary_changed"] = old.sha256 != new.sha256
        result["source_changed_by"] = None  # uploader not bound to revision in existing schema
    else:
        result["source_changed_by"] = new.actor_id
    return result


def detect(db, user, body):
    fields = dict(owner_id=user.id, kind=body.kind, old_id=str(body.old.id), old_version=body.old.version,
                  new_id=str(body.new.id), new_version=body.new.version)
    try:
        # Owner lock serializes duplicate detection without touching source versions.
        db.execute(update(User).where(User.id == user.id).values(name=User.name))
        existing = db.scalar(select(SourceChange).filter_by(**fields))
        row = existing or SourceChange(**fields)
        comparison = compare(db, user, row)
        if body.kind == "document" and row.old_id == row.new_id:
            old = source_revision(db, user, row.kind, row.old_id, row.old_version)
            new = source_revision(db, user, row.kind, row.new_id, row.new_version)
            if old.created_at >= new.created_at:
                raise HTTPException(422, "版本顺序无效")
        if not existing:
            row.detected_state = comparison["state"]
            db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.scalar(select(SourceChange).filter_by(**fields))
        if row is None:
            raise HTTPException(409, "检测冲突，请重试") from None
    except BaseException:
        db.rollback()
        raise
    db.expire_all()
    return payload(db, user, db.get(SourceChange, row.id))
