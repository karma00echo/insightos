from fastapi import APIRouter, Query, Request

from app.api.decisions import PrivateDecisionRoute
from app.api.dependencies import DB, CurrentUser, session_recheck
from app.impact_schemas import ImpactRequest, ReviewChoice
from app.services import decision_impact

router = APIRouter(prefix='/api/decision-impact', tags=['decision-impact'], route_class=PrivateDecisionRoute)


@router.post('/analyze')
async def analyze(body: ImpactRequest, db: DB, user: CurrentUser, request: Request):
    return await decision_impact.analyze(db, user, body, request.app.state.database,
                                       request.app.state.settings, request.is_disconnected,
                                       session_recheck(request, user.id))


@router.post('/history')
def history(body: ImpactRequest, db: DB, user: CurrentUser, offset: int = Query(0, ge=0)):
    return decision_impact.history(db, user, body, offset)


@router.post('/{analysis_id}/review')
def review(analysis_id: str, body: ReviewChoice, db: DB, user: CurrentUser):
    return decision_impact.choose(db, user, analysis_id, body)
