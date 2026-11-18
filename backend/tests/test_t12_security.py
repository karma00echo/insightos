"""T12 in-flight identity revocation, real auth/session SQL and mocked LLM only."""
# ruff: noqa: F811 -- pytest discovers the imported fixtures by name.
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from app.impact_models import ImpactReviewEvent
from app.models import AuthSession, User, utcnow
from sqlalchemy import func, select
from test_decision_candidates import cc  # noqa: F401
from test_decision_candidates import request as candidate_request
from test_decision_impact import analyze, impact  # noqa: F401


def invalidate(client, headers, kind):
    if kind == 'logout':
        assert client.post('/api/auth/logout', headers=headers).status_code == 204
        return
    with client.app.state.database.session() as db:
        if kind == 'disabled':
            for user in db.scalars(select(User)):
                user.disabled = True
        else:
            for session in db.scalars(select(AuthSession)):
                session.expires_at = utcnow()
        db.commit()


@pytest.mark.parametrize('kind', ['logout', 'disabled', 'expired'])
def test_candidate_identity_invalidated_during_model(cc, kind):
    c, h, state, calls = cc
    state['during'] = lambda: invalidate(c, h, kind)
    response = candidate_request(c, h)
    assert response.status_code == 401
    assert 'candidate' not in response.json() and len(calls) == 1


@pytest.mark.parametrize('kind', ['logout', 'disabled', 'expired'])
def test_impact_identity_invalidated_during_model(impact, kind):
    c, h, _, state, calls, _ = impact
    entered = Event()
    state['during'] = entered.set
    state['delay'] = 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(analyze, impact)
        assert entered.wait(10)
        invalidate(c, h, kind)
        response = pending.result(timeout=15)
    assert response.status_code == 401
    assert 'input' not in response.json() and len(calls) == 1
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(ImpactReviewEvent)) == 0
