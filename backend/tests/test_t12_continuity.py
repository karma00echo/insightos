"""Cookie-authenticated continuity across all six actual business API layers."""
# ruff: noqa: F811 -- pytest discovers the imported fixture by name.
import json
from types import SimpleNamespace

import pytest
from app.models import new_id
from app.services import decision_impact
from test_decision_candidates import cc  # noqa: F401
from test_decision_candidates import request as candidate_request
from test_decision_impact import proposal
from test_memories import create as memory_create
from test_memories import data as memory_data


@pytest.mark.parametrize('choice', ['keep', 'revise'])
def test_cookie_continuity_explicit_confirmations_and_relogin(cc, monkeypatch, choice):
    c, _, _, calls = cc
    h = {'X-RAG-Request': '1'}
    m = memory_create(c, h, memory_data(kind='plan', content='预算100元'))
    ref = dict(type='memory', id=m['id'], revision=1)
    result = candidate_request(c, h, sources=[ref])
    assert result.status_code == 200
    draft = result.json()
    assert not draft['confirmed']
    assert c.get('/api/decisions').json()['items'] == []
    assert c.get('/api/actions').json()['items'] == []
    final = dict(**draft['candidate'], basis=[ref], confirmed=True, request_id=new_id())
    final['question'] = '用户编辑后的问题'
    r = c.post('/api/decisions', headers=h, json=final)
    assert r.status_code == 201
    d = r.json()
    dp = '/api/decisions/' + d['id']
    assert c.post('/api/auth/logout', headers=h).status_code == 204
    assert c.get(dp).status_code == 401
    assert c.post('/api/auth/login', json=dict(email='owner@example.com', password='correct horse 47')).status_code == 200
    assert c.get(dp).json()['revision']['content']['question'] == final['question']
    r = c.post('/api/actions/proposals', headers=h, json=dict(id=d['id'], version=1))
    assert r.status_code == 200 and c.get('/api/actions').json()['items'] == []
    action = dict(**r.json()['candidate'], request_id=new_id(), approved=True, source=r.json()['source'])
    action.update(title='用户编辑后的行动', due_date='2026-12-01')
    r = c.post('/api/actions', headers=h, json=action)
    assert r.status_code == 201
    ap = '/api/actions/' + r.json()['id']
    r = c.put(ap, headers=h, json=dict(request_id=new_id(), expected_version=1, title=action['title'], description='明确步骤', due_date='2026-12-02', reason='调整计划'))
    assert r.status_code == 200 and r.json()['current_version'] == 2
    r = c.post(ap + '/status', headers=h, json=dict(request_id=new_id(), expected_version=2, status='completed', reason='用户确认完成'))
    assert r.status_code == 200
    assert c.get(dp + '/actions?version=1').json()['counts']['completed'] == 1
    r = c.put('/api/memories/' + m['id'], headers=h, json=dict(request_id=new_id(), expected_version=1, confirmed=True, kind='plan', content='预算200元', reason='用户调整预算'))
    assert r.status_code == 200
    assert c.get(dp).json()['current_version'] == 1
    r = c.post('/api/source-changes/detect', headers=h, json=dict(kind='memory', old=dict(id=m['id'], version='1'), new=dict(id=m['id'], version='2'), confirmed=True))
    assert r.status_code == 200 and r.json()['state'] == 'changed'
    target = dict(decision_id=d['id'], decision_version=1, change_id=r.json()['id'])

    class Model:
        def model_copy(self, update):
            return self

        async def ainvoke(self, messages, config):
            return SimpleNamespace(content=json.dumps(proposal(json.loads(messages[1].content)), ensure_ascii=False))

    monkeypatch.setattr(decision_impact, 'llm_ready', lambda: True)
    monkeypatch.setattr(decision_impact, 'configured_llm', lambda: Model())
    r = c.post('/api/decision-impact/analyze', headers=h, json=target)
    assert r.status_code == 200
    assert c.get(dp).json()['current_version'] == 1
    rp = '/api/decision-impact/' + r.json()['review']['analysis_id'] + '/review'
    r = c.post(rp, headers=h, json=dict(request_id=new_id(), expected_sequence=1, status=choice))
    assert r.status_code == 200
    assert c.get(dp).json()['current_version'] == 1
    assert c.get(ap).json()['status'] == 'completed'
    if choice == 'revise':
        edit = dict(**final, expected_version=1, reason='人工复核后明确修订')
        edit.update(request_id=new_id(), question='用户再次编辑后的问题')
        r = c.put(dp, headers=h, json=edit)
        assert r.status_code == 200 and r.json()['current_version'] == 2
        old = c.get(dp + '/versions/1').json()
        assert old['content']['question'] == final['question']
        assert c.get(ap).json()['status'] == 'completed'
    assert len(calls) == 1
