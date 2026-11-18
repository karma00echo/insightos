"""Revision-bound references. Never snapshot personal memory content."""

from fastapi import HTTPException
from sqlalchemy import select

from app.decision_models import DecisionBasis
from app.memory_models import MemoryRevision
from app.services import memories


def resolve(db, user, reference):
    if reference["type"] == "memory":
        memory = memories.owned(db, user, reference["id"])
        revision = db.get(MemoryRevision, (memory.id, reference["revision"]))
        if (
            memory.status != "active"
            or not revision
            or revision.confirmation != "confirmed"
            or memories.availability(db, user, memory) == "unavailable"
        ):
            raise HTTPException(422, "依据不可用")
        return {"type": "memory", "id": memory.id, "revision": revision.version}, "available"
    return memories.source_snapshot(db, user, reference)


def validate(db, user, references):
    result = []
    for reference in references:
        try:
            snapshot, _ = resolve(db, user, reference.model_dump(mode="json"))
        except (HTTPException, OSError, ValueError, KeyError, TypeError):
            raise HTTPException(422, "依据不可用") from None
        if snapshot not in result:
            result.append(snapshot)
    return result


def references(db, decision_id, version):
    return list(
        db.scalars(
            select(DecisionBasis.reference)
            .where(DecisionBasis.decision_id == decision_id, DecisionBasis.version == version)
            .order_by(DecisionBasis.ordinal)
        )
    )


def availability(db, user, refs):
    status = "available"
    for reference in refs:
        try:
            current, state = resolve(db, user, reference)
            if current != reference:
                return "unavailable"
            if state == "available_original_version":
                status = state
        except (HTTPException, OSError, ValueError, KeyError, TypeError):
            return "unavailable"
    return status
