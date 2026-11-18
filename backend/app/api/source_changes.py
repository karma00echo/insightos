from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.decisions import PrivateDecisionRoute
from app.api.dependencies import DB, CurrentUser
from app.services import source_changes
from app.source_change_models import SourceChange
from app.source_change_schemas import ChangeDetect

router = APIRouter(prefix="/api/source-changes", tags=["source-changes"], route_class=PrivateDecisionRoute)


@router.post("/detect")
def detect(body: ChangeDetect, db: DB, user: CurrentUser):
    return source_changes.detect(db, user, body)


@router.get("")
def listing(db: DB, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    visible = []
    for row in db.scalars(select(SourceChange).where(SourceChange.owner_id == user.id)
                          .order_by(SourceChange.detected_at.desc(), SourceChange.id)):
        value = source_changes.payload(db, user, row)
        if value["state"] != "unavailable":
            visible.append(value)
    return {"items": visible[offset:offset + limit], "offset": offset, "limit": limit}


@router.get("/{change_id}")
def detail(change_id: str, db: DB, user: CurrentUser):
    row = db.scalar(select(SourceChange).where(SourceChange.owner_id == user.id, SourceChange.id == change_id))
    if row is None:
        raise HTTPException(404, "变化记录不存在")
    return source_changes.payload(db, user, row, include_references=True)
