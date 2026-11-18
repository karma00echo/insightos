"""T08 real isolated API/SQL tests. No provider or external execution."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from app.action_models import Action, ActionMutation, ActionRevision
from app.action_schemas import ActionEdit
from app.models import Membership, Organization, User, new_id
from app.services import actions, decisions
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_api import register
from test_decisions import body as decision_body
from test_decisions import create as decision_create
from test_decisions import memory


def body(**kw):
    return dict(request_id=new_id(), approved=True, title="PRIVATE-TASK", description="PRIVATE-DESCRIPTION",
                due_date="2026-12-01", **kw)


def create(c, h, data=None):
    r = c.post("/api/actions", headers=h, json=data or body())
    assert r.status_code == 201, r.text
    return r.json()


def command(version, **kw):
    return dict(request_id=new_id(), expected_version=version, **kw)


@pytest.mark.parametrize("change", [
    {"approved": False}, {"approved": "true"}, {"approved": 1}, {"title": " "},
    {"owner_id": "x"}, {"actor_id": "x"}, {"status": "completed"},
    {"due_date": "invalid"}, {"source": {"id": new_id(), "version": 1}},
])
def test_unapproved_invalid_never_persist(client, change, caplog):
    h = register(client)
    r = client.post("/api/actions", headers=h, json={**body(), **change})
    assert r.status_code == 422
    assert "PRIVATE" not in r.text + caplog.text
    with client.app.state.database.session() as db:
        for model in [Action, ActionRevision, ActionMutation]:
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_manual_lifecycle_replay_and_erasure(client):
    c = client
    h = register(c)
    submitted = body()
    row = create(c, h, submitted)
    p = "/api/actions/" + row["id"]
    assert row["status"] == "todo" and row["source_state"] == "manual"
    assert row["approved_by"] == row["revision"]["actor_id"] and row["approved_at"]
    assert create(c, h, submitted)["id"] == row["id"]
    assert c.post("/api/actions", headers=h, json={**submitted, "title": "other"}).status_code == 409
    edit = dict(request_id=new_id(), expected_version=1, title="PRIVATE-EDIT", description="new", due_date=None)
    assert c.put(p, headers=h, json=edit).json()["current_version"] == 2
    assert c.put(p, headers=h, json=edit).json()["replayed"]
    assert c.put(p, headers=h, json={**edit, "request_id": new_id()}).status_code == 409
    v = 2
    for state in ["in_progress", "completed", "todo", "cancelled", "todo"]:
        req = command(v, status=state)
        result = c.post(p + "/status", headers=h, json=req)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == state
        assert bool(result.json()["completed_at"]) == (state == "completed")
        assert bool(result.json()["cancelled_at"]) == (state == "cancelled")
        assert c.post(p + "/status", headers=h, json=req).json()["replayed"]
        v += 1
    history = c.get(p + "/history", headers=h).json()["items"]
    assert len(history) == v and history[-1]["content"]["title"] == "PRIVATE-TASK"
    assert len(c.get("/api/actions?status=todo", headers=h).json()["items"]) == 1
    assert c.get("/api/actions?status=completed", headers=h).json()["items"] == []
    deletion = command(v)
    assert c.request("DELETE", p, headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", p, headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", p, headers=h, json={**deletion, "expected_version": 1}).status_code == 409
    assert c.post("/api/actions", headers=h, json=submitted).status_code == 410
    assert c.put(p, headers=h, json=edit).status_code == 410
    for suffix in ["", "/history"]:
        assert c.get(p + suffix, headers=h).status_code == 404
    with c.app.state.database.session() as db:
        assert not db.scalars(select(Action)).all() and not db.scalars(select(ActionRevision)).all()
        assert all(op.digest is None for op in db.scalars(select(ActionMutation)))


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_terminal_only_reopens_todo_and_edit_keeps_stamp(client, terminal):
    h = register(client)
    row = create(client, h)
    p = "/api/actions/" + row["id"]
    r = client.post(p + "/status", headers=h, json=command(1, status=terminal)).json()
    assert client.post(p + "/status", headers=h, json=command(2, status="in_progress")).status_code == 409
    assert client.post(p + "/status", headers=h, json=command(2, status=terminal)).status_code == 409
    e = client.put(p, headers=h, json=dict(request_id=new_id(), expected_version=2, title="edited"))
    assert e.status_code == 200
    assert e.json()[terminal + "_at"] == r[terminal + "_at"]


def test_owner_and_admin_isolation(client):
    c = client
    h = register(c)
    d = decision_create(c, h)
    row = create(c, h)
    p = "/api/actions/" + row["id"]
    for email, admin in [("b@example.com", False), ("admin@example.com", True)]:
        other = register(c, email)
        if admin:
            with c.app.state.database.session() as db:
                org = Organization(name="test")
                db.add(org)
                db.flush()
                user = db.scalar(select(User).where(User.email == email))
                db.add(Membership(organization_id=org.id, user_id=user.id, role="admin"))
                db.commit()
        assert c.get("/api/actions", headers=other).json()["items"] == []
        for suffix in ["", "/history"]:
            assert c.get(p + suffix, headers=other).status_code == 404
        assert c.put(p, headers=other, json=dict(request_id=new_id(), expected_version=1, title="x")).status_code == 404
        assert c.post(p + "/status", headers=other, json=command(1, status="completed")).status_code == 404
        assert c.request("DELETE", p, headers=other, json=command(1)).status_code == 404
        ref = dict(id=d["id"], version=1)
        assert c.post("/api/actions/proposals", headers=other, json=ref).status_code == 422
        assert c.post("/api/actions", headers=other, json=body(source=ref)).status_code == 422


@pytest.mark.parametrize("change", ["edit", "revoke", "delete", "memory_revoke", "memory_delete"])
def test_source_protection_and_no_automatic_cancel(client, change):
    c = client
    h = register(c)
    m = memory(c, h)
    d = decision_create(c, h, decision_body(basis=[dict(type="memory", id=m["id"], revision=1)]))
    ref = dict(id=d["id"], version=1)
    prop = c.post("/api/actions/proposals", headers=h, json=ref)
    assert prop.status_code == 200 and prop.json()["candidate"]["due_date"] is None
    assert c.get("/api/actions", headers=h).json()["items"] == []
    submitted = body(source=ref)
    row = create(c, h, submitted)
    p = "/api/actions/" + row["id"]
    assert "PRIVATE-REASON" not in json.dumps(row)
    dp = "/api/decisions/" + d["id"]
    if change == "edit":
        assert c.put(dp, headers=h, json=decision_body(expected_version=1, reason="change")).status_code == 200
    elif change == "revoke":
        assert c.post(dp + "/revoke", headers=h, json=command(1)).status_code == 200
    elif change == "delete":
        assert c.request("DELETE", dp, headers=h, json=command(1)).status_code == 204
    elif change == "memory_revoke":
        assert c.post("/api/memories/" + m["id"] + "/revoke", headers=h, json=command(1)).status_code == 200
    else:
        assert c.request("DELETE", "/api/memories/" + m["id"], headers=h, json=command(1)).status_code == 204
    assert c.post("/api/actions/proposals", headers=h, json=ref).status_code == 422
    assert c.post("/api/actions", headers=h, json=body(source=ref)).status_code == 422
    for endpoint in [p, p + "/history", "/api/actions"]:
        r = c.get(endpoint, headers=h)
        assert r.status_code == 200 and "PRIVATE-" not in r.text and "2026-12-01" not in r.text
    assert c.get(p, headers=h).json()["status"] == "todo"
    assert c.put(p, headers=h, json=dict(request_id=new_id(), expected_version=1, title="wash")).status_code == 409
    assert c.post(p + "/status", headers=h, json=command(1, status="cancelled")).status_code == 200
    if change == "delete":
        with c.app.state.database.session() as db:
            assert all(r.content is None and r.due_date is None for r in db.scalars(select(ActionRevision)))
            assert all(op.digest is None for op in db.scalars(select(ActionMutation).where(ActionMutation.operation == "create")))
        assert c.post("/api/actions", headers=h, json=submitted).status_code == 410
    assert c.request("DELETE", p, headers=h, json=command(2)).status_code == 204


def test_transaction_failure_rolls_back(client):
    c = client
    h = register(c)
    row = create(c, h)
    engine = c.app.state.database.engine

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO action_mutations"):
            raise OperationalError("injected", {}, Exception("private failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        r = c.post("/api/actions/" + row["id"] + "/status", headers=h, json=command(1, status="completed"))
        assert r.status_code == 503 and "private failure" not in r.text
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert c.get("/api/actions/" + row["id"], headers=h).json()["current_version"] == 1
    assert len(c.get("/api/actions/" + row["id"] + "/history", headers=h).json()["items"]) == 1


def test_concurrent_edit_cas(client):
    h = register(client)
    row = create(client, h)
    barrier = Barrier(2)
    database = client.app.state.database

    def write(title):
        with database.session() as db:
            user = db.get(User, row["approved_by"])
            barrier.wait(timeout=10)
            try:
                return actions.mutate(db, user, "edit", ActionEdit(
                    request_id=new_id(), expected_version=1, title=title), row["id"])["current_version"]
            except HTTPException as e:
                return e.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["one", "two"]))
    assert sorted(results) == [2, 409]


@pytest.mark.parametrize("failure", ["revoke", "expire", "delete", "version_missing", "replace"])
def test_all_document_dependencies_revalidated(client, failure):
    from datetime import timedelta

    from app.models import Chunk, Document, DocumentRevision, KnowledgeBase, utcnow
    from test_memories import seed_source

    c = client
    h = register(c)
    ref = seed_source(c, "document_chunk")
    d = decision_create(c, h, decision_body(basis=[dict(type="user_input"), ref]))
    source = dict(id=d["id"], version=1)
    row = create(c, h, body(source=source))
    with c.app.state.database.session() as db:
        doc = db.get(Document, db.get(Chunk, ref["id"]).document_id)
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
            db.delete(db.get(DocumentRevision, ref["version"]))
        else:
            doc.active_revision_id = new_id()
        db.commit()
    proposal = c.post("/api/actions/proposals", headers=h, json=source)
    value = c.get("/api/actions/" + row["id"], headers=h)
    if failure == "replace":
        assert proposal.status_code == 200 and value.json()["source_state"] == "available"
    else:
        assert proposal.status_code == 422 and value.json()["source_state"] == "unavailable"
        assert "PRIVATE" not in value.text
        assert c.post("/api/actions", headers=h, json=body(source=source)).status_code == 422
        assert "PRIVATE" not in c.get("/api/actions/" + row["id"] + "/history", headers=h).text


def test_concurrent_same_create_is_idempotent(client):
    from app.action_schemas import ActionCreate

    h = register(client)
    user_id = client.get("/api/auth/me", headers=h).json()["id"]
    submitted = ActionCreate(**body())
    barrier = Barrier(2)

    def write(_):
        with client.app.state.database.session() as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=10)
            return actions.mutate(db, user, "create", submitted)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(write, range(2)))
    assert ids[0] == ids[1]
    with client.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(Action)) == 1


def test_cookie_csrf_and_no_store(client):
    register(client)
    assert client.post("/api/actions", json=body()).status_code == 403
    response = client.post("/api/actions", headers={"X-RAG-Request": "1"}, json=body())
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("kind", ["create", "status"])
def test_source_delete_serializes_with_approval_and_status_copy(client, kind):
    from app.action_schemas import ActionCreate, ActionStatus
    from app.decision_schemas import DecisionAction

    c = client
    h = register(c)
    decision = decision_create(c, h)
    source = dict(id=decision["id"], version=1)
    row = create(c, h, body(source=source))
    database = c.app.state.database
    ready, deletion_started = Event(), Event()

    def pause(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO action_revisions"):
            ready.set()
            assert deletion_started.wait(timeout=10)

    def write():
        with database.session() as db:
            user = db.get(User, row["approved_by"])
            if kind == "create":
                return actions.mutate(db, user, "create", ActionCreate(**body(source=source)))
            return actions.mutate(db, user, "status", ActionStatus(
                request_id=new_id(), expected_version=1, status="completed"), row["id"])

    def remove_source():
        assert ready.wait(timeout=10)
        with database.session() as db:
            user = db.get(User, row["approved_by"])
            deletion_started.set()
            decisions.mutate(db, user, "delete", DecisionAction(request_id=new_id(), expected_version=1), decision["id"])

    event.listen(database.engine, "before_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            w = pool.submit(write)
            d = pool.submit(remove_source)
            w.result(timeout=30)
            d.result(timeout=30)
    finally:
        event.remove(database.engine, "before_cursor_execute", pause)
    with database.session() as db:
        assert all(r.content is None and r.due_date is None for r in db.scalars(select(ActionRevision)))
        assert all(op.digest is None for op in db.scalars(select(ActionMutation)))
        assert db.get(Action, row["id"]).status == ("completed" if kind == "status" else "todo")


def test_decision_delete_failure_restores_linked_task_content_atomically(client):
    c = client
    h = register(c)
    d = decision_create(c, h)
    row = create(c, h, body(source=dict(id=d["id"], version=1)))
    engine = c.app.state.database.engine

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO decision_mutations"):
            raise OperationalError("injected", {}, Exception("failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        assert c.request("DELETE", "/api/decisions/" + d["id"], headers=h, json=command(1)).status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert c.get("/api/actions/" + row["id"], headers=h).json()["revision"]["content"]["title"] == "PRIVATE-TASK"
    with c.app.state.database.session() as db:
        assert db.scalar(select(ActionMutation)).digest is not None
