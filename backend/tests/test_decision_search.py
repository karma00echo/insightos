import json

import pytest
from app.models import new_id
from test_api import register
from test_decision_candidates import request
from test_decisions import body, create, path

pytest_plugins = ["test_decision_candidates"]


def search(c, h, **kwargs):
    r = c.post("/api/decisions/search", headers=h, json=kwargs)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_search_current_lifecycle_and_owner(client):
    c = client
    a = register(c)
    row = create(c, a, body(question="Personal Memory first"))
    assert len(search(c, a, query="personal memory")) == 1
    b = register(c, "other@example.com")
    assert search(c, b, query="Memory") == []
    assert c.get(path(row), headers=b).status_code == 404
    edit = body(question="current only", reasons=["new reason"], expected_version=1, reason="changed")
    assert c.put(path(row), headers=a, json=edit).status_code == 200
    assert search(c, a, query="Personal") == []
    assert len(search(c, a, query="new reason", limit=1)) == 1
    action = dict(request_id=new_id(), expected_version=2)
    assert c.post(path(row) + "/revoke", headers=a, json=action).status_code == 200
    assert search(c, a) == []
    assert len(search(c, a, status="withdrawn")) == 1
    assert (
        c.request("DELETE", path(row), headers=a, json=dict(request_id=new_id(), expected_version=3)).status_code == 204
    )
    assert search(c, a, status="withdrawn") == []


def test_search_never_matches_revoked_memory(client):
    c = client
    h = register(c)
    memory = c.post(
        "/api/memories", headers=h, json=dict(request_id=new_id(), kind="preference", content="private", confirmed=True)
    ).json()
    row = create(c, h, body(basis=[dict(type="memory", id=memory["id"], revision=1)]))
    assert len(search(c, h, query="PRIVATE")) == 1
    c.post("/api/memories/" + memory["id"] + "/revoke", headers=h, json=dict(request_id=new_id(), expected_version=1))
    assert search(c, h, query="PRIVATE") == []
    assert search(c, h, query="nonexistent") == []
    assert search(c, h) == []
    assert "content" not in c.get(path(row) + "/versions/1", headers=h).json()


def test_reference_context_is_fresh_bounded_and_not_a_candidate(cc):
    c, h, state, calls = cc
    row = create(c, h)
    refs = [dict(id=row["id"], version=1)]
    r = request(c, h, decision_references=refs)
    assert r.status_code == 200, r.text
    assert r.json()["candidate"] is None
    injected = json.loads(calls[-1][1].content)["confirmed_decision_background"]
    assert injected[0]["content"]["question"] == row["revision"]["content"]["question"]
    assert "并非已经独立核验" in calls[-1][0].content
    c.put(path(row), headers=h, json=body(expected_version=1, reason="edit"))
    assert request(c, h, decision_references=refs).status_code == 422
    assert request(c, h, decision_references=[dict(id=new_id(), version=1)]).status_code == 422
    assert request(c, h, decision_references=refs * 4).status_code == 422


@pytest.mark.parametrize("action", ["revoke", "delete", "other_owner", "during"])
def test_reference_rejects_unavailable_and_inference_race(cc, action):
    c, h, state, calls = cc
    row = create(c, h)
    refs = [dict(id=row["id"], version=1)]
    if action == "revoke":
        c.post(path(row) + "/revoke", headers=h, json=dict(request_id=new_id(), expected_version=1))
    if action == "delete":
        c.request("DELETE", path(row), headers=h, json=dict(request_id=new_id(), expected_version=1))
    if action == "other_owner":
        h = register(c, "second@example.com")
    if action == "during":
        state["during"] = lambda: c.post(
            path(row) + "/revoke", headers=h, json=dict(request_id=new_id(), expected_version=1)
        )
    r = request(c, h, decision_references=refs)
    assert r.status_code == 422, r.text
    assert "PRIVATE" not in r.text
    assert len(calls) == (1 if action == "during" else 0)


@pytest.mark.parametrize("failure", ["revoke", "expire", "delete", "version_missing", "replace"])
def test_document_search_and_reference_authorization(cc, failure):
    from datetime import timedelta

    from app.models import Chunk, Document, DocumentRevision, KnowledgeBase, Organization, utcnow
    from test_memories import seed_source

    c, h, _, calls = cc
    ref = seed_source(c, "document_chunk")
    row = create(c, h, body(basis=[ref]))
    with c.app.state.database.session() as db:
        doc = db.get(Document, db.get(Chunk, ref["id"]).document_id)
        rev = db.get(DocumentRevision, ref["version"])
        if failure == "revoke":
            org = Organization(name="revoked")
            db.add(org)
            db.flush()
            db.get(KnowledgeBase, doc.knowledge_base_id).organization_id = org.id
        elif failure == "expire":
            doc.expires_at = utcnow() - timedelta(seconds=1)
        elif failure == "delete":
            db.delete(doc)
        elif failure == "version_missing":
            db.delete(rev)
        else:
            doc.active_revision_id = new_id()
        db.commit()
    refs = [dict(id=row["id"], version=1)]
    response = request(c, h, decision_references=refs)
    if failure == "replace":
        assert search(c, h, query="PRIVATE")[0]["revision"]["availability"] == "available_original_version"
        assert response.status_code == 200
    else:
        assert search(c, h, query="PRIVATE") == []
        assert search(c, h, query="absent") == []
        assert response.status_code == 422
        assert not calls
        assert "PRIVATE" not in response.text
