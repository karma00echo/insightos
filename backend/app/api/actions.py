"""Owner-only task management. No external execution or AI mutation tools."""

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.action_models import Action, ActionRevision
from app.action_schemas import ActionCommand, ActionCreate, ActionEdit, ActionStatus, Status
from app.api.dependencies import DB, CurrentUser
from app.decision_schemas import DecisionReference
from app.services import actions


class PrivateActionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "任务请求无效，请检查批准、字段和版本") from None
            except SQLAlchemyError:
                raise HTTPException(503, "任务存储暂不可用，请使用同一请求重试") from None

        return private_handler


router = APIRouter(prefix="/api/actions", tags=["actions"], route_class=PrivateActionRoute)


@router.post("/proposals")
def proposal(body: DecisionReference, db: DB, user: CurrentUser):
    return actions.proposal(db, user, body)


@router.post("", status_code=201)
def create(body: ActionCreate, db: DB, user: CurrentUser):
    return actions.mutate(db, user, "create", body)


@router.get("")
def listing(db: DB, user: CurrentUser, status: Status | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    query = select(Action).where(Action.owner_id == user.id)
    if status:
        query = query.where(Action.status == status)
    rows = db.scalars(query.order_by(Action.created_at.desc(), Action.id).offset(offset).limit(limit))
    return {"items": [actions.payload(db, user, row) for row in rows], "offset": offset, "limit": limit}


@router.get("/{action_id}")
def detail(action_id: str, db: DB, user: CurrentUser):
    return actions.payload(db, user, actions.owned(db, user, action_id))


@router.get("/{action_id}/history")
def history(action_id: str, db: DB, user: CurrentUser,
            offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    row = actions.owned(db, user, action_id)
    revisions = db.scalars(select(ActionRevision).where(ActionRevision.action_id == row.id)
                          .order_by(ActionRevision.version.desc()).offset(offset).limit(limit))
    return {"items": [actions.revision_payload(db, user, row, rev) for rev in revisions],
            "offset": offset, "limit": limit}


@router.put("/{action_id}")
def edit(action_id: str, body: ActionEdit, db: DB, user: CurrentUser):
    return actions.mutate(db, user, "edit", body, action_id)


@router.post("/{action_id}/status")
def status(action_id: str, body: ActionStatus, db: DB, user: CurrentUser):
    return actions.mutate(db, user, "status", body, action_id)


@router.delete("/{action_id}", status_code=204)
def remove(action_id: str, body: ActionCommand, db: DB, user: CurrentUser):
    actions.mutate(db, user, "delete", body, action_id)
    return Response(status_code=204)
