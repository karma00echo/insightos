"""T09 linking, authorization, per-operation reasons and T08 compatibility."""

import json

import pytest
from app.action_models import ActionMutation, ActionRevision
from sqlalchemy import select
from test_actions import body, command, create
from test_api import register
from test_decisions import body as decision_body
from test_decisions import create as decision_create
from test_decisions import memory


def setup(c, basis=None):
    h = register(c)
    d = decision_create(c, h, decision_body(basis=basis or []))
    a = create(c, h, body(source={"id": d["id"], "version": 1}))
    return h, d, a


def url(d, version=1):
    return f'/api/decisions/{d["id"]}/actions?version={version}'


def test_progress_transitions_pagination_and_independent_decision(client):
    c = client
    h, d, a = setup(c)
    p = "/api/actions/" + a["id"]
    create(c, h)  # manual tasks never count
    b = create(c, h, body(source={"id": d["id"], "version": 1}))
    assert c.get(url(d) + "&limit=1", headers=h).json()["total"] == 2
    assert len(c.get(url(d) + "&limit=1&offset=1", headers=h).json()["items"]) == 1
    for v, state in enumerate(["in_progress", "completed", "todo", "cancelled"], 1):
        assert c.post(p + "/status", headers=h, json=command(v, status=state, reason="user reason")).status_code == 200
        r = c.get(url(d), headers=h).json()
        assert r["counts"][state] == (2 if state == "todo" else 1) and r["total"] == 2
        assert c.get("/api/decisions/" + d["id"], headers=h).json()["current_version"] == 1
    assert c.request("DELETE", "/api/actions/" + b["id"], headers=h, json=command(1)).status_code == 204
    assert c.get(url(d), headers=h).json()["total"] == 1


def test_reasons_history_digest_legacy_and_no_inheritance(client):
    c = client
    h, d, a = setup(c)
    p = "/api/actions/" + a["id"]
    legacy = dict(request_id=command(1)["request_id"], expected_version=1, title="legacy", description="", due_date=None)
    assert c.put(p, headers=h, json=legacy).status_code == 200
    assert c.put(p, headers=h, json={**legacy, "reason": ""}).json()["replayed"]
    edit = {**legacy, "request_id": command(2)["request_id"], "expected_version": 2, "reason": "PRIVATE-REASON", "due_date": "2026-12-03"}
    assert c.put(p, headers=h, json=edit).status_code == 200
    assert c.put(p, headers=h, json=edit).json()["replayed"]
    assert c.put(p, headers=h, json={**edit, "reason": "other"}).status_code == 409
    assert c.post(p + "/status", headers=h, json=command(3, status="completed")).status_code == 200
    history = c.get(p + "/history", headers=h).json()["items"]
    assert "change_reason" not in history[0]["content"]
    assert history[1]["content"]["change_reason"] == "PRIVATE-REASON"
    assert "change_reason" not in history[-1]["content"]
    assert c.put(p, headers=h, json={**edit, "request_id": command(4)["request_id"], "expected_version": 4, "reason": "x" * 501}).status_code == 422
    assert c.request("DELETE", "/api/decisions/" + d["id"], headers=h, json=command(1)).status_code == 204
    assert c.get(url(d), headers=h).status_code == 404
    assert "PRIVATE-REASON" not in c.get(p + "/history", headers=h).text
    with c.app.state.database.session() as db:
        assert all(r.content is None for r in db.scalars(select(ActionRevision)))
        assert all(r.digest is None for r in db.scalars(select(ActionMutation)))


@pytest.mark.parametrize("loss", ["edit", "revoke", "memory"])
def test_protected_linking_never_discloses_counts_or_reason(client, loss):
    c = client
    h = register(c)
    m = memory(c, h)
    d = decision_create(c, h, decision_body(basis=[{"type": "memory", "id": m["id"], "revision": 1}]))
    a = create(c, h, body(source={"id": d["id"], "version": 1}))
    p = "/api/actions/" + a["id"]
    assert c.post(p + "/status", headers=h, json=command(1, status="in_progress", reason="PRIVATE-REASON")).status_code == 200
    if loss == "edit":
        c.put("/api/decisions/" + d["id"], headers=h, json=decision_body(expected_version=1, reason="edit"))
    elif loss == "revoke":
        c.post("/api/decisions/" + d["id"] + "/revoke", headers=h, json=command(1))
    else:
        c.post("/api/memories/" + m["id"] + "/revoke", headers=h, json=command(1))
    r = c.get(url(d), headers=h).json()
    assert not r["available"] and r["counts"] is None and r["total"] is None and not r["items"]
    assert "PRIVATE" not in json.dumps(r) + c.get(p + "/history", headers=h).text
    assert c.post(p + "/status", headers=h, json=command(2, status="cancelled", reason="private")).status_code == 409
    assert c.post(p + "/status", headers=h, json=command(2, status="cancelled")).status_code == 200
    if loss == "edit":
        assert c.get(url(d, 2), headers=h).json()["total"] == 0


def test_owner_admin_and_invalid_queries(client):
    c = client
    h, d, _ = setup(c)
    for label in ["b", "admin"]:
        other = register(c, label + "@example.com")
        if label == "admin":
            assert c.post("/api/organizations", headers=other, json={"name": "org"}).status_code == 201
        assert c.get(url(d), headers=other).status_code == 404
    for suffix in ["&limit=101", "&offset=-1"]:
        assert c.get(url(d) + suffix, headers=h).status_code == 422


@pytest.mark.parametrize("loss", ["revoke", "expire", "delete", "version_missing", "replace"])
def test_linked_document_authorization(client, loss):
    from datetime import timedelta

    from app.models import Chunk, Document, DocumentRevision, KnowledgeBase, Organization, new_id, utcnow
    from test_memories import seed_source

    c = client
    h = register(c)
    ref = seed_source(c, "document_chunk")
    d = decision_create(c, h, decision_body(basis=[ref]))
    a = create(c, h, body(source={"id": d["id"], "version": 1}))
    p = "/api/actions/" + a["id"]
    c.post(p + "/status", headers=h, json=command(1, status="in_progress", reason="PRIVATE-REASON"))
    with c.app.state.database.session() as db:
        doc = db.get(Document, db.get(Chunk, ref["id"]).document_id)
        if loss == "revoke":
            org = Organization(name="revoked")
            db.add(org)
            db.flush()
            db.get(KnowledgeBase, doc.knowledge_base_id).organization_id = org.id
        elif loss == "expire":
            doc.expires_at = utcnow() - timedelta(seconds=1)
        elif loss == "delete":
            db.delete(doc)
        elif loss == "version_missing":
            db.delete(db.get(DocumentRevision, ref["version"]))
        else:
            doc.active_revision_id = new_id()
        db.commit()
    r = c.get(url(d), headers=h).json()
    assert r["available"] == (loss == "replace")
    if loss != "replace":
        assert r["counts"] is None and not r["items"]
        assert "PRIVATE" not in json.dumps(r) + c.get(p + "/history", headers=h).text
