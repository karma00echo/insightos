"""Authenticated transient proposal endpoint. No persistence or writing tools."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import DB, CurrentUser, session_recheck
from app.candidate_schemas import CandidateRequest, CandidateResponse
from app.services.decision_candidates import extract


class PrivateCandidateRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "临时讨论请求无效，请检查上下文和来源") from None
            except SQLAlchemyError:
                raise HTTPException(503, "来源或配额暂不可用，请稍后重试") from None

        return private_handler


router = APIRouter(prefix="/api/decision-candidates", tags=["decision-candidates"], route_class=PrivateCandidateRoute)


@router.post("", response_model=CandidateResponse)
def propose(body: CandidateRequest, db: DB, user: CurrentUser, request: Request):
    return extract(db, user, body, request.app.state.database, request.app.state.settings,
                   session_recheck(request, user.id))
