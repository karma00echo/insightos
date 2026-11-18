"""Private user-operated API, never a model tool."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import DB, CurrentUser
from app.decision_models import Decision, DecisionRevision
from app.decision_schemas import DecisionAction, DecisionCreate, DecisionEdit, DecisionSearch
from app.services import decisions


class PrivateDecisionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "决定请求无效；请检查明确确认、选择、版本和依据字段") from None
            except SQLAlchemyError:
                raise HTTPException(503, "决定存储暂不可用，请使用同一请求标识重试") from None

        return private_handler


router = APIRouter(prefix="/api/decisions", tags=["decisions"], route_class=PrivateDecisionRoute)


@router.get("/{decision_id}/actions")
def linked_actions(decision_id: str, db: DB, user: CurrentUser, version: int = Query(..., ge=1),
                   offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    from app.services.actions import linked

    return linked(db, user, decision_id, version, offset, limit)


@router.post("", status_code=201)
def create(body: DecisionCreate, db: DB, user: CurrentUser):
    return decisions.mutate(db, user, "create", body)


@router.get("")
def listing(
    db: DB,
    user: CurrentUser,
    status: Literal["active", "withdrawn"] | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    query = select(Decision).where(Decision.owner_id == user.id)
    if status:
        query = query.where(Decision.status == status)
    rows = db.scalars(query.order_by(Decision.created_at.desc(), Decision.id).offset(offset).limit(limit))
    return {"items": [decisions.payload(db, user, row) for row in rows], "offset": offset, "limit": limit}


@router.post("/search")
def search(body: DecisionSearch, db: DB, user: CurrentUser):
    return decisions.search(db, user, body)


@router.get("/{decision_id}")
def detail(decision_id: str, db: DB, user: CurrentUser):
    return decisions.payload(db, user, decisions.owned(db, user, decision_id))


@router.get("/{decision_id}/history")
def history(
    decision_id: str, db: DB, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
):
    row = decisions.owned(db, user, decision_id)
    versions = db.scalars(
        select(DecisionRevision.version)
        .where(DecisionRevision.decision_id == row.id)
        .order_by(DecisionRevision.version.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [decisions.revision_payload(db, user, row, version) for version in versions],
        "offset": offset,
        "limit": limit,
    }


@router.get("/{decision_id}/versions/{version}")
def version_detail(decision_id: str, version: int, db: DB, user: CurrentUser):
    return decisions.revision_payload(db, user, decisions.owned(db, user, decision_id), version)


@router.put("/{decision_id}")
def edit(decision_id: str, body: DecisionEdit, db: DB, user: CurrentUser):
    return decisions.mutate(db, user, "edit", body, decision_id)


@router.post("/{decision_id}/revoke")
def revoke(decision_id: str, body: DecisionAction, db: DB, user: CurrentUser):
    return decisions.mutate(db, user, "revoke", body, decision_id)


@router.delete("/{decision_id}", status_code=204)
def remove(decision_id: str, body: DecisionAction, db: DB, user: CurrentUser):
    decisions.mutate(db, user, "delete", body, decision_id)
    return Response(status_code=204)
