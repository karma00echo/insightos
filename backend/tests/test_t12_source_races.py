"""Supplemental T12 source deletion interleavings using real isolated SQL."""
# ruff: noqa: F811 -- imported pytest fixture.
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from app.memory_models import PersonalMemory
from app.models import DocumentRevision, new_id
from app.services import source_changes
from test_api import register
from test_decision_impact import analyze, impact  # noqa: F401
from test_decisions import body, create
from test_source_changes import document_pair, memory_pair


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


@pytest.mark.parametrize('loss', ['memory_delete', 'memory_revoke', 'document_old', 'document_new'])
def test_source_loss_after_compare_hides_entire_change(client, monkeypatch, loss):
    c = client
    h = register(c)
    if loss.startswith('memory'):
        _, pair = memory_pair(c, h)
    else:
        _, pair = document_pair(c)
    path = '/api/source-changes'
    detected = c.post(path + '/detect', headers=h, json=pair)
    assert detected.status_code == 200
    change = detected.json()
    assert change['state'] == 'changed' and change['differences']
    compare = source_changes.compare

    def compare_then_invalidate(db, user, row):
        result = compare(db, user, row)
        assert result['state'] == 'changed'
        with c.app.state.database.session() as concurrent:
            if loss.startswith('memory'):
                memory = concurrent.get(PersonalMemory, pair['old']['id'])
                if loss == 'memory_delete':
                    concurrent.delete(memory)
                else:
                    memory.status = 'withdrawn'
            else:
                side = 'old' if loss == 'document_old' else 'new'
                concurrent.delete(concurrent.get(DocumentRevision, pair[side]['version']))
            concurrent.commit()
        return result

    detail = path + '/' + change['id']
    with monkeypatch.context() as patch:
        patch.setattr(source_changes, 'compare', compare_then_invalidate)
        read = c.get(detail, headers=h)
    assert read.status_code == 200
    assert read.json() == {'id': change['id'], 'state': 'unavailable'}
    assert c.get(detail, headers=h).json() == read.json()
    assert c.get(path, headers=h).json()['items'] == []
    assert c.post(path + '/detect', headers=h, json=pair).status_code == 404


@pytest.mark.parametrize('operation', ['revoke', 'delete'])
def test_memory_invalidated_inside_impact_model(impact, operation):
    c, h, target, state, _, _ = impact
    memory, pair = memory_pair(c, h)
    decision = create(c, h, body(basis=[dict(type='memory', id=memory['id'], revision=1)]))
    change = c.post('/api/source-changes/detect', headers=h, json=pair).json()
    target.update(decision_id=decision['id'], change_id=change['id'])

    def invalidate():
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
