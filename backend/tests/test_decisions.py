"""T04 real isolated SQL/API lifecycle and privacy contracts. No model calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from app.decision_models import Decision, DecisionBasis, DecisionMutation, DecisionRevision
from app.decision_schemas import DecisionAction, DecisionCreate, DecisionEdit
from app.models import Chunk, Document, DocumentRevision, KnowledgeBase, Membership, Organization, User, new_id, utcnow
from app.services import decisions
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_api import register
from test_memories import seed_source


@pytest.fixture
def dc(client):
    return client, register(client)


def body(**changes):
    return {
        "request_id": new_id(),
        "confirmed": True,
        "question": "PRIVATE-DECISION question",
        "options": [{"id": "a", "label": "Memory"}, {"id": "b", "label": "Agents"}],
        "selected_option": "a",
        "reasons": ["PRIVATE-REASON"],
        "assumptions": ["small scope"],
        "review_conditions": ["requirements change"],
        **changes,
    }


def create(c, h, data=None):
    r = c.post("/api/decisions", headers=h, json=data or body())
    assert r.status_code == 201, r.text
    return r.json()


def path(row):
    return "/api/decisions/" + row["id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"confirmed": "true"},
        {"confirmed": 1},
        {"confirmed": None},
        {"owner_id": new_id()},
        {"actor_id": new_id()},
        {"selected_option": "missing"},
        {"options": [{"id": "a", "label": "a"}, {"id": "a", "label": "b"}]},
        {"reasons": []},
        {"question": " "},
        {"kind": "ai_suggestion"},
        {"basis": [{"type": "report", "id": new_id(), "version": "1"}]},
        {"basis": [{"type": "research_evidence", "id": new_id(), "version": "1"}]},
    ],
)
def test_confirmation_and_invalid_inputs_never_persist(dc, changes, caplog):
    c, h = dc
    r = c.post("/api/decisions", headers=h, json=body(**changes))
    assert r.status_code == 422
    assert "PRIVATE-" not in r.text + caplog.text
    with c.app.state.database.session() as db:
        for model in [Decision, DecisionRevision, DecisionBasis, DecisionMutation]:
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_lifecycle_and_content_history_erasure(dc):
    c, h = dc
    submitted = body()
    row = create(c, h, submitted)
    assert row["revision"]["content"]["assumptions"] == submitted["assumptions"]
    assert row["revision"]["content"]["review_conditions"] == submitted["review_conditions"]
    assert row["revision"]["confirmed_at"] and row["revision"]["actor_id"]
    assert row["revision"]["evidence_status"] == "unverified"
    assert create(c, h, submitted)["id"] == row["id"]
    assert c.post("/api/decisions", headers=h, json={**submitted, "question": "other"}).status_code == 409
    edit = body(expected_version=1, reason="changed choice", selected_option="b")
    assert c.put(path(row), headers=h, json=edit).json()["current_version"] == 2
    assert c.put(path(row), headers=h, json=edit).json()["replayed"]
    assert c.put(path(row), headers=h, json={**edit, "request_id": new_id()}).status_code == 409
    old = c.get(path(row) + "/versions/1", headers=h).json()
    assert old["content"]["selected_option"] == "a" and old["revision_position"] == "superseded"
    action = {"request_id": new_id(), "expected_version": 2}
    revoked = c.post(path(row) + "/revoke", headers=h, json=action).json()
    assert revoked["status"] == "withdrawn" and revoked["revision"]["confirmation"] == "revoked"
    assert revoked["revision"]["confirmed_at"] is None
    assert c.post(path(row) + "/revoke", headers=h, json=action).json()["replayed"]
    assert len(c.get("/api/decisions?status=withdrawn", headers=h).json()["items"]) == 1
    assert c.get("/api/decisions?status=active", headers=h).json()["items"] == []
    assert c.put(path(row), headers=h, json=body(expected_version=3, reason="restored")).json()["status"] == "active"
    assert len(c.get(path(row) + "/history", headers=h).json()["items"]) == 4
    deletion = {"request_id": new_id(), "expected_version": 4}
    assert c.request("DELETE", path(row), headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", path(row), headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", path(row), headers=h, json={**deletion, "expected_version": 1}).status_code == 409
    for suffix in ["", "/history", "/versions/1"]:
        assert c.get(path(row) + suffix, headers=h).status_code == 404
    assert c.get("/api/decisions", headers=h).json()["items"] == []
    assert c.post("/api/decisions", headers=h, json=submitted).status_code == 410
    assert c.put(path(row), headers=h, json=edit).status_code == 410
    with c.app.state.database.session() as db:
        for model in [Decision, DecisionRevision, DecisionBasis]:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert all(op.digest is None for op in db.scalars(select(DecisionMutation)))


def test_owner_and_organization_admin_isolation(dc):
    c, h = dc
    row = create(c, h)
    admin = register(c, "admin@example.com")
    with c.app.state.database.session() as db:
        org = Organization(name="org")
        db.add(org)
        db.flush()
        for user in db.scalars(select(User)):
            db.add(Membership(organization_id=org.id, user_id=user.id, role="admin"))
        db.commit()
    for suffix in ["", "/history", "/versions/1"]:
        assert c.get(path(row) + suffix, headers=admin).status_code == 404
    assert c.get("/api/decisions", headers=admin).json()["items"] == []
    assert c.put(path(row), headers=admin, json=body(expected_version=1, reason="x")).status_code == 404
    action = {"request_id": new_id(), "expected_version": 1}
    assert c.post(path(row) + "/revoke", headers=admin, json=action).status_code == 404
    assert c.request("DELETE", path(row), headers=admin, json=action).status_code == 404
    c.cookies.clear()
    assert c.get(path(row)).status_code == 401


def memory(c, h):
    r = c.post(
        "/api/memories",
        headers=h,
        json={"request_id": new_id(), "kind": "preference", "content": "MEMORY-BODY-NEVER-COPIED", "confirmed": True},
    )
    assert r.status_code == 201
    return r.json()


@pytest.mark.parametrize("change", ["revoke", "delete", "edit"])
def test_memory_pinned_revision_and_lifecycle(dc, change):
    c, h = dc
    m = memory(c, h)
    ref = {"type": "memory", "id": m["id"], "revision": 1}
    row = create(c, h, body(basis=[ref]))
    assert row["revision"]["basis"] == [ref]
    assert "MEMORY-BODY" not in json.dumps(row)
    action = {"request_id": new_id(), "expected_version": 1}
    if change == "edit":
        c.put(
            "/api/memories/" + m["id"],
            headers=h,
            json={**action, "kind": "plan", "content": "new memory", "reason": "updated", "confirmed": True},
        )
        got = c.get(path(row), headers=h).json()
        assert got["revision"]["basis"] == [ref] and got["revision"]["availability"] == "available"
    else:
        if change == "revoke":
            c.post("/api/memories/" + m["id"] + "/revoke", headers=h, json=action)
        else:
            c.request("DELETE", "/api/memories/" + m["id"], headers=h, json=action)
        for suffix in ["", "/history", "/versions/1"]:
            r = c.get(path(row) + suffix, headers=h)
            assert "PRIVATE-" not in r.text and "MEMORY-BODY" not in r.text and m["id"] not in r.text
        assert c.get(path(row), headers=h).json()["status"] == "active"  # independent decision status
        assert (
            c.put(path(row), headers=h, json=body(expected_version=1, reason="remove basis", basis=[])).status_code
            == 409
        )
        assert c.post(path(row) + "/revoke", headers=h, json=action).status_code == 200
        assert (
            c.request("DELETE", path(row), headers=h, json={"request_id": new_id(), "expected_version": 2}).status_code
            == 204
        )


def test_memory_owner_revision_and_unconfirmed_rejected(dc):
    c, h = dc
    m = memory(c, h)
    other = register(c, "other@example.com")
    for headers, revision in [(other, 1), (h, 99)]:
        assert (
            c.post(
                "/api/decisions",
                headers=headers,
                json=body(basis=[{"type": "memory", "id": m["id"], "revision": revision}]),
            ).status_code
            == 422
        )
    unconfirmed = body()
    del unconfirmed["confirmed"]
    assert c.post("/api/decisions", headers=h, json=unconfirmed).status_code == 422


@pytest.mark.parametrize("kind", ["user_message", "document_chunk"])
def test_original_source_binding_and_forgery(dc, kind):
    c, h = dc
    ref = seed_source(c, kind)
    other = register(c, "other@example.com")
    for headers, source in [(other, ref), (h, {**ref, "id": new_id()}), (h, {**ref, "version": "forged"})]:
        assert c.post("/api/decisions", headers=headers, json=body(basis=[source])).status_code == 422
    row = create(c, h, body(basis=[ref]))
    assert "WHOLE-" not in json.dumps(row)


@pytest.mark.parametrize("failure", ["revoke", "expire", "delete", "version_missing", "file_missing", "replace"])
def test_document_dependencies_protect_reason_and_all_history(dc, failure):
    c, h = dc
    ref = seed_source(c, "document_chunk")
    submitted = body(basis=[ref])
    row = create(c, h, submitted)
    # basis=[] cannot strip inherited protection.
    assert (
        c.put(path(row), headers=h, json=body(expected_version=1, reason="PRIVATE-EDIT", basis=[])).status_code == 200
    )
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
        elif failure == "file_missing":
            from pathlib import Path

            Path(rev.stored_path).unlink()
        else:
            doc.active_revision_id = new_id()
        db.commit()
    got = c.get(path(row), headers=h).json()
    if failure == "replace":
        assert got["revision"]["availability"] == "available_original_version"
        assert got["revision"]["basis"][-1]["version"] == ref["version"]
        return
    for response in [
        c.get(path(row), headers=h),
        c.get(path(row) + "/history", headers=h),
        c.get(path(row) + "/versions/1", headers=h),
        c.get("/api/decisions", headers=h),
        c.post("/api/decisions", headers=h, json=submitted),
    ]:
        assert "PRIVATE-" not in response.text and ref["id"] not in response.text
    assert c.put(path(row), headers=h, json=body(expected_version=2, reason="x")).status_code == 409


def test_storage_failure_atomicity_and_private_errors(dc, monkeypatch, caplog):
    c, h = dc
    original = Session.commit

    def failure(db):
        if any(isinstance(row, DecisionMutation) for row in db.new):
            raise OperationalError("INSERT", {"content": "PRIVATE-DECISION"}, Exception("PRIVATE-REASON"))
        return original(db)

    monkeypatch.setattr(Session, "commit", failure)
    r = c.post("/api/decisions", headers=h, json=body())
    assert r.status_code == 503 and "PRIVATE-" not in r.text + caplog.text
    with c.app.state.database.session() as db:
        for model in [Decision, DecisionRevision, DecisionBasis, DecisionMutation]:
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("mode", ["create", "edit", "delete_edit", "delete_replay"])
def test_concurrent_mutations(dc, mode):
    c, h = dc
    submitted = body()
    row = None if mode == "create" else create(c, h, submitted)
    barrier = Barrier(2)

    def run(i):
        with c.app.state.database.session() as db:
            user = db.scalar(select(User).where(User.email == "owner@example.com"))
            barrier.wait(timeout=10)
            try:
                if mode == "create" or (mode == "delete_replay" and i == 1):
                    result = decisions.mutate(db, user, "create", DecisionCreate(**submitted))
                    return result["id"]
                if mode == "edit" or (mode == "delete_edit" and i == 1):
                    result = decisions.mutate(
                        db, user, "edit", DecisionEdit(**body(expected_version=1, reason=str(i))), row["id"]
                    )
                    return result["current_version"]
                decisions.mutate(db, user, "delete", DecisionAction(request_id=new_id(), expected_version=1), row["id"])
                return 204
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0, 1]))
    if mode == "create":
        assert results[0] == results[1]
    elif mode == "edit":
        assert sorted(results) == [2, 409]
    elif mode == "delete_edit":
        assert 409 in results or 404 in results
    else:
        assert 204 in results
        assert c.get(path(row), headers=h).status_code == 404
        assert c.post("/api/decisions", headers=h, json=submitted).status_code == 410


def test_pagination_schema_and_reconfirmation(dc):
    c, h = dc
    row = create(c, h)
    create(c, h)
    assert len(c.get("/api/decisions?limit=1&offset=1", headers=h).json()["items"]) == 1
    assert c.get("/api/decisions?status=superseded", headers=h).status_code == 422
    assert c.get("/api/decisions?limit=101", headers=h).status_code == 422
    assert c.get(path(row) + "/versions/999", headers=h).status_code == 404
    assert c.put(path(row), headers=h, json=body(expected_version=1, reason="x", confirmed=False)).status_code == 422
    assert c.get(path(row), headers=h).headers["cache-control"] == "no-store"


def test_memory_underlying_source_loss_hides_decision(dc):
    c, h = dc
    source = seed_source(c, "document_chunk")
    m = c.post(
        "/api/memories",
        headers=h,
        json={
            "request_id": new_id(),
            "kind": "user_fact",
            "content": "MEMORY-SOURCE-PRIVATE",
            "confirmed": True,
            "source": source,
        },
    ).json()
    row = create(c, h, body(basis=[{"type": "memory", "id": m["id"], "revision": 1}]))
    with c.app.state.database.session() as db:
        db.get(Document, db.get(Chunk, source["id"]).document_id).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    for suffix in ["", "/history", "/versions/1"]:
        r = c.get(path(row) + suffix, headers=h)
        assert "PRIVATE-" not in r.text and "MEMORY-SOURCE" not in r.text
    assert c.get(path(row), headers=h).json()["revision"]["availability"] == "unavailable"


def test_multiple_basis_one_invalid_masks_entire_revision(dc):
    c, h = dc
    source = seed_source(c, "document_chunk")
    m = memory(c, h)
    row = create(c, h, body(basis=[{"type": "memory", "id": m["id"], "revision": 1}, source]))
    with c.app.state.database.session() as db:
        db.delete(db.get(Document, db.get(Chunk, source["id"]).document_id))
        db.commit()
    r = c.get(path(row), headers=h)
    assert "PRIVATE-" not in r.text and m["id"] not in r.text and source["id"] not in r.text


def test_assistant_messages_rejected_and_cookie_csrf_enforced(dc):
    c, h = dc
    source = seed_source(c, role="assistant")
    assert c.post("/api/decisions", headers=h, json=body(basis=[source])).status_code == 422
    assert c.post("/api/decisions", json=body()).status_code == 403
    assert c.post("/api/decisions", headers={"X-RAG-Request": "1"}, json=body()).status_code == 201


def test_failed_edit_rolls_back_version_revision_basis_and_operation(dc, monkeypatch):
    c, h = dc
    row = create(c, h)
    original = Session.commit

    def fail(db):
        if any(isinstance(item, DecisionMutation) for item in db.new):
            raise OperationalError("INSERT", {}, Exception("injected"))
        return original(db)

    monkeypatch.setattr(Session, "commit", fail)
    assert c.put(path(row), headers=h, json=body(expected_version=1, reason="x")).status_code == 503
    assert c.get(path(row), headers=h).json()["current_version"] == 1
    assert len(c.get(path(row) + "/history", headers=h).json()["items"]) == 1
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(DecisionMutation)) == 1
        assert db.scalar(select(func.count()).select_from(DecisionBasis)) == 1
