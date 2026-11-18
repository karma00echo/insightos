"""Owner-only integration API; never registered as an LLM tool."""
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import DB, CurrentUser
from app.memory_models import MemoryRevision, PersonalMemory
from app.memory_schemas import MemoryAction, MemoryCreate, MemoryEdit
from app.services import memories


class PrivateMemoryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default error includes submitted values, even with hide_input_in_errors.
                raise HTTPException(422, "记忆请求无效；请检查分类、明确确认、版本和来源字段") from None
            except SQLAlchemyError:
                raise HTTPException(503, "记忆存储暂不可用，请使用同一请求标识重试") from None
        return private_handler


router = APIRouter(prefix="/api/memories", tags=["personal-memory"], route_class=PrivateMemoryRoute)


@router.post("", status_code=201)
def create(body: MemoryCreate, db: DB, user: CurrentUser):
    return memories.mutate(db, user, "create", body)


@router.get("")
def list_memories(db: DB, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    rows = db.scalars(select(PersonalMemory).where(PersonalMemory.owner_id == user.id)
        .order_by(PersonalMemory.created_at.desc(), PersonalMemory.id).offset(offset).limit(limit))
    return {"items": [memories.payload(db, user, row) for row in rows], "offset": offset, "limit": limit}


@router.get("/{memory_id}")
def get_memory(memory_id: str, db: DB, user: CurrentUser):
    return memories.payload(db, user, memories.owned(db, user, memory_id))


@router.get("/{memory_id}/history")
def history(memory_id: str, db: DB, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    memory = memories.owned(db, user, memory_id)
    if memories.availability(db, user, memory) == "unavailable":
        raise HTTPException(409, "来源不可用，历史内容已隐藏")
    rows = db.scalars(select(MemoryRevision).where(MemoryRevision.memory_id == memory.id)
        .order_by(MemoryRevision.version.desc()).offset(offset).limit(limit))
    return {"items": [memories.revision_payload(row) for row in rows], "offset": offset, "limit": limit}


@router.put("/{memory_id}")
def edit(memory_id: str, body: MemoryEdit, db: DB, user: CurrentUser):
    return memories.mutate(db, user, "edit", body, memory_id)


@router.post("/{memory_id}/revoke")
def revoke(memory_id: str, body: MemoryAction, db: DB, user: CurrentUser):
    return memories.mutate(db, user, "revoke", body, memory_id)


@router.delete("/{memory_id}", status_code=204)
def remove(memory_id: str, body: MemoryAction, db: DB, user: CurrentUser):
    memories.mutate(db, user, "delete", body, memory_id)
    return Response(status_code=204)
