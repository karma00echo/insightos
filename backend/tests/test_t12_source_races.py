"""Supplemental T12 source deletion interleavings using real isolated SQL."""
# ruff: noqa: F811 -- imported pytest fixture.
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.models import new_id
from test_api import register
from test_decision_impact import analyze, impact  # noqa: F401
from test_decisions import body, create
from test_source_changes import memory_pair


def test_source_delete_races_detection_without_resurrecting_content(client):
    c, gate = client, Barrier(2)
    h = register(c)
    memory, pair = memory_pair(c, h)

    def work(kind):
        gate.wait(timeout=10)
        if kind == 'delete':
            return c.request('DELETE', '/api/memories/' + memory['id'], headers=h,
                             json=dict(request_id=new_id(), expected_version=2))
        return c.post('/api/source-changes/detect', headers=h, json=pair)

    with ThreadPoolExecutor(max_workers=2) as pool:
        detected, deleted = list(pool.map(work, ['detect', 'delete']))
    assert deleted.status_code == 204
    assert detected.status_code in {200, 404}
    assert c.get('/api/source-changes', headers=h).json()['items'] == []
    if detected.status_code == 200:
        value = c.get('/api/source-changes/' + detected.json()['id'], headers=h).json()
        assert value == dict(id=detected.json()['id'], state='unavailable')
    assert c.post('/api/source-changes/detect', headers=h, json=pair).status_code == 404


@pytest.mark.parametrize('operation', ['revoke', 'delete'])
def test_memory_invalidated_inside_impact_model(impact, operation):
    c, h, target, state, _, _ = impact
    memory, pair = memory_pair(c, h)
    decision = create(c, h, body(basis=[dict(type='memory', id=memory['id'], revision=1)]))
    change = c.post('/api/source-changes/detect', headers=h, json=pair).json()
    target.update(decision_id=decision['id'], change_id=change['id'])

    def invalidate():
        from app.memory_models import PersonalMemory
        with c.app.state.database.session() as db:
            row = db.get(PersonalMemory, memory['id'])
            if operation == 'delete':
                db.delete(row)
            else:
                row.status = 'withdrawn'
            db.commit()

    state['during'] = invalidate
    result = analyze(impact)
    assert result.status_code == 409
    assert 'input' not in result.json()
    from app.impact_models import ImpactReviewEvent
    from sqlalchemy import func, select
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(ImpactReviewEvent)) == 0
