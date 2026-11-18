"""T03 isolated lifecycle, provenance, privacy and race contracts."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from app.memory_models import MemoryOperation, MemoryRevision, PersonalMemory
from app.memory_schemas import MemoryCreate, MemoryEdit
from app.models import (
    Chunk,
    Conversation,
    Document,
    DocumentRevision,
    KnowledgeBase,
    KnowledgeBaseGrant,
    Membership,
    Message,
    Organization,
    User,
    new_id,
    utcnow,
)
from app.services import memories
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from test_api import register


@pytest.fixture
def mc(client):
    return client, register(client)


def data(**changes):
    return {
        "request_id": new_id(),
        "kind": "preference",
        "content": "PRIVATE-MEMORY-SENTINEL",
        "confirmed": True,
        **changes,
    }


def create(c, h, body=None):
    r = c.post("/api/memories", headers=h, json=body or data())
    assert r.status_code == 201, r.text
    return r.json()


def url(row):
    return "/api/memories/" + row["id"]


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"confirmed": "true"},
        {"confirmed": 1},
        {"confirmed": None},
        {"kind": "ai_inference"},
        {"kind": "user_decision"},
        {"kind": "fact"},
        {"owner_id": "forged"},
        {"evidence_status": "verified"},
        {"content": "  "},
        {"source": {"type": "report", "id": new_id(), "version": "1"}},
        {"source": {"type": "user_input", "id": new_id()}},
    ],
)
def test_reject_unconfirmed_and_category_forgery(mc, changes, caplog):
    c, h = mc
    r = c.post("/api/memories", headers=h, json=data(**changes))
    assert r.status_code == 422
    assert "PRIVATE-MEMORY-SENTINEL" not in r.text + caplog.text
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(PersonalMemory)) == 0
        assert db.scalar(select(func.count()).select_from(MemoryOperation)) == 0


@pytest.mark.parametrize("kind", ["user_fact", "preference", "plan"])
def test_classification_and_confirmation_are_not_verification(mc, kind):
    c, h = mc
    row = create(c, h, data(kind=kind))
    assert row["revision"]["kind"] == kind
    assert row["revision"]["confirmation"] == "confirmed"
    assert row["revision"]["confirmed_at"] and row["revision"]["confirmed_by"]
    assert row["evidence_status"] == row["revision"]["evidence_status"] == "unverified"
    assert c.get(url(row), headers=h).headers["cache-control"] == "no-store"


def test_lifecycle_history_idempotency_and_erasure(mc):
    c, h = mc
    body = data()
    row = create(c, h, body)
    assert create(c, h, body)["id"] == row["id"]
    assert c.post("/api/memories", headers=h, json={**body, "content": "different"}).status_code == 409
    edit = data(expected_version=1, reason="PRIVATE-REASON", content="PRIVATE-EDIT", kind="plan")
    r = c.put(url(row), headers=h, json=edit)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert c.put(url(row), headers=h, json=edit).json()["replayed"]
    assert c.put(url(row), headers=h, json={**edit, "request_id": new_id()}).status_code == 409
    revoke = {"request_id": new_id(), "expected_version": 2}
    r = c.post(url(row) + "/revoke", headers=h, json=revoke)
    assert r.status_code == 200 and r.json()["confirmation"] == "revoked"
    assert r.json()["status"] == "withdrawn" and r.json()["revision"]["confirmed_at"] is None
    assert c.post(url(row) + "/revoke", headers=h, json=revoke).json()["version"] == 3
    restore = data(expected_version=3, reason="explicit restore")
    assert c.put(url(row), headers=h, json=restore).json()["confirmation"] == "confirmed"
    history = c.get(url(row) + "/history", headers=h).json()["items"]
    assert [r["version"] for r in history] == [4, 3, 2, 1]
    assert history[2]["content"] == "PRIVATE-EDIT" and history[2]["reason"] == "PRIVATE-REASON"
    assert c.get(url(row) + "/history?limit=1&offset=2", headers=h).json()["items"] == [history[2]]
    deletion = {"request_id": new_id(), "expected_version": 4}
    assert c.request("DELETE", url(row), headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", url(row), headers=h, json=deletion).status_code == 204
    assert c.request("DELETE", url(row), headers=h, json={**deletion, "expected_version": 1}).status_code == 409
    assert c.get(url(row), headers=h).status_code == c.get(url(row) + "/history", headers=h).status_code == 404
    assert c.get("/api/memories", headers=h).json()["items"] == []
    assert c.post("/api/memories", headers=h, json=body).status_code == 410
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 0
        assert db.scalar(select(func.count()).select_from(PersonalMemory)) == 0
        ops = list(db.scalars(select(MemoryOperation)))
        assert len(ops) == 5 and all(op.digest is None for op in ops)


def test_owner_admin_isolation_and_authentication(mc):
    c, h = mc
    row = create(c, h)
    other = register(c, "admin@example.com")
    with c.app.state.database.session() as db:
        users = list(db.scalars(select(User).order_by(User.email)))
        org = Organization(name="test")
        db.add(org)
        db.flush()
        for user in users:
            db.add(Membership(organization_id=org.id, user_id=user.id, role="admin"))
        db.commit()
    for headers in [other]:
        assert c.get(url(row), headers=headers).status_code == 404
        assert c.get(url(row) + "/history", headers=headers).status_code == 404
        assert c.get("/api/memories", headers=headers).json()["items"] == []
        assert c.put(url(row), headers=headers, json=data(expected_version=1, reason="x")).status_code == 404
        action = {"request_id": new_id(), "expected_version": 1}
        assert c.post(url(row) + "/revoke", headers=headers, json=action).status_code == 404
        assert c.request("DELETE", url(row), headers=headers, json=action).status_code == 404
    c.cookies.clear()
    assert c.get(url(row)).status_code == 401


def seed_source(c, kind="user_message", role="user"):
    with c.app.state.database.session() as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        if kind == "user_message":
            conv = Conversation(user_id=owner.id, title="private")
            db.add(conv)
            db.flush()
            msg = Message(conversation_id=conv.id, role=role, content="WHOLE-CHAT-NOT-COPIED")
            db.add(msg)
            db.commit()
            return {"type": kind, "id": msg.id, "version": memories.digest(msg.content)}
        kb = KnowledgeBase(user_id=owner.id, name="private")
        db.add(kb)
        db.flush()
        doc = Document(
            knowledge_base_id=kb.id, name="s.md", stored_path="unused", sha256="sha", size_bytes=1, status="ready"
        )
        db.add(doc)
        db.flush()
        path = c.app.state.settings.data_dir / "memory-source.md"
        path.write_text("source", encoding="utf8")
        rev = DocumentRevision(
            document_id=doc.id, name="s.md", stored_path=str(path), sha256="sha", size_bytes=1, status="ready"
        )
        db.add(rev)
        db.flush()
        doc.active_revision_id = rev.id
        chunk = Chunk(
            document_id=doc.id,
            revision_id=rev.id,
            text="WHOLE-SOURCE-NOT-COPIED",
            ordinal=0,
            locator={"line_start": 1, "line_end": 1},
        )
        db.add(chunk)
        db.commit()
        return {"type": kind, "id": chunk.id, "version": rev.id}


@pytest.mark.parametrize("kind", ["user_message", "document_chunk"])
def test_sources_forgery_cross_user_and_no_automatic_copy(mc, kind):
    c, h = mc
    source = seed_source(c, kind)
    other = register(c, "other@example.com")
    for forged in [{**source, "id": new_id()}, {**source, "version": "forged"}]:
        assert c.post("/api/memories", headers=h, json=data(source=forged)).status_code == 422
    assert c.post("/api/memories", headers=other, json=data(source=source)).status_code == 422
    row = create(c, h, data(source=source))
    assert "WHOLE-" not in json.dumps(row)
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(PersonalMemory)) == 1
        assert db.get(MemoryRevision, (row["id"], 1)).content == "PRIVATE-MEMORY-SENTINEL"


def test_assistant_message_cannot_be_laundered_as_user_fact(mc):
    c, h = mc
    source = seed_source(c, role="assistant")
    assert c.post("/api/memories", headers=h, json=data(kind="user_fact", source=source)).status_code == 422


@pytest.mark.parametrize("failure", ["delete", "expire", "revoke", "file_missing", "version_missing", "changed"])
def test_document_source_fail_closed_every_surface(mc, failure):
    c, h = mc
    source = seed_source(c, "document_chunk")
    body = data(source=source)
    row = create(c, h, body)
    assert c.put(url(row), headers=h, json=data(expected_version=1, reason="SECRET-REASON")).status_code == 200
    with c.app.state.database.session() as db:
        chunk = db.get(Chunk, source["id"])
        doc = db.get(Document, chunk.document_id)
        rev = db.get(DocumentRevision, source["version"])
        if failure == "delete":
            db.delete(doc)
        elif failure == "expire":
            doc.expires_at = utcnow() - timedelta(seconds=1)
        elif failure == "revoke":
            kb = db.get(KnowledgeBase, doc.knowledge_base_id)
            org = Organization(name="revoked")
            db.add(org)
            db.flush()
            kb.organization_id = org.id  # owner is no longer a member
        elif failure == "file_missing":
            from pathlib import Path

            Path(rev.stored_path).unlink()
        elif failure == "version_missing":
            db.delete(rev)
        else:
            chunk.text = "changed"
        db.commit()
    for response in [
        c.get(url(row), headers=h),
        c.get("/api/memories", headers=h),
        c.post("/api/memories", headers=h, json=body),
        c.get(url(row) + "/history", headers=h),
    ]:
        assert "PRIVATE-MEMORY" not in response.text and "SECRET-REASON" not in response.text
        assert source["id"] not in response.text
    assert c.get(url(row), headers=h).json()["source_availability"] == "unavailable"
    assert c.get(url(row) + "/history", headers=h).status_code == 409
    assert c.put(url(row), headers=h, json=data(expected_version=2, reason="x")).status_code == 409
    assert (
        c.post(url(row) + "/revoke", headers=h, json={"request_id": new_id(), "expected_version": 2}).status_code == 200
    )
    assert (
        c.request("DELETE", url(row), headers=h, json={"request_id": new_id(), "expected_version": 3}).status_code
        == 204
    )


@pytest.mark.parametrize("change", ["delete", "edit"])
def test_message_source_unavailable(mc, change):
    c, h = mc
    source = seed_source(c)
    row = create(c, h, data(source=source))
    with c.app.state.database.session() as db:
        msg = db.get(Message, source["id"])
        if change == "delete":
            db.delete(msg)
        else:
            msg.content = "modified"
        db.commit()
    assert c.get(url(row), headers=h).json()["source_availability"] == "unavailable"


def test_new_document_version_does_not_replace_original(mc):
    c, h = mc
    source = seed_source(c, "document_chunk")
    row = create(c, h, data(source=source))
    with c.app.state.database.session() as db:
        doc = db.get(Document, db.get(Chunk, source["id"]).document_id)
        doc.active_revision_id = new_id()
        db.commit()
    r = c.get(url(row), headers=h).json()
    assert r["source_availability"] == "available_original_version"
    assert r["source"]["version"] == source["version"]


def test_transaction_failure_no_body_in_response_or_logs(mc, monkeypatch, caplog):
    c, h = mc
    from sqlalchemy.orm import Session

    original = Session.commit

    def fail(session):
        if any(isinstance(row, MemoryOperation) for row in session.new):
            raise OperationalError("INSERT", {"body": "PRIVATE-MEMORY-SENTINEL"}, Exception("private"))
        return original(session)

    monkeypatch.setattr(Session, "commit", fail)
    r = c.post("/api/memories", headers=h, json=data())
    assert r.status_code == 503
    assert "PRIVATE-MEMORY-SENTINEL" not in r.text + caplog.text
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(PersonalMemory)) == 0
        assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 0


def test_concurrent_edits_compare_and_swap(mc):
    c, h = mc
    row = create(c, h)
    barrier = Barrier(2)
    database = c.app.state.database

    def run(index):
        with database.session() as db:
            user = db.scalar(select(User).where(User.email == "owner@example.com"))
            barrier.wait(timeout=10)
            try:
                result = memories.mutate(
                    db,
                    user,
                    "edit",
                    MemoryEdit(**data(content=f"edit-{index}", expected_version=1, reason="race")),
                    row["id"],
                )
                return result["version"]
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [0, 1]))
    assert sorted(results) == [2, 409]
    assert len(c.get(url(row) + "/history", headers=h).json()["items"]) == 2


def test_pagination_and_validation_do_not_echo_private_fields(mc):
    c, h = mc
    for _ in range(3):
        create(c, h)
    page = c.get("/api/memories?limit=1&offset=1", headers=h).json()
    assert len(page["items"]) == 1
    assert c.get("/api/memories?limit=101", headers=h).status_code == 422
    assert (
        c.post(
            "/api/memories",
            headers=h,
            content='{"content":"PRIVATE-MEMORY-SENTINEL"',
        ).status_code
        == 422
    )


def test_shared_source_grant_revocation_hides_private_memory(mc):
    c, h = mc
    source = seed_source(c, "document_chunk")
    reader = register(c, "reader@example.com")
    with c.app.state.database.session() as db:
        owner = db.scalar(select(User).where(User.email == "owner@example.com"))
        user = db.scalar(select(User).where(User.email == "reader@example.com"))
        kb = db.get(KnowledgeBase, db.get(Document, db.get(Chunk, source["id"]).document_id).knowledge_base_id)
        org = Organization(name="shared")
        db.add(org)
        db.flush()
        kb.organization_id, kb.visibility = org.id, "restricted"
        db.add_all(
            [
                Membership(organization_id=org.id, user_id=owner.id, role="admin"),
                Membership(organization_id=org.id, user_id=user.id, role="reader"),
                KnowledgeBaseGrant(knowledge_base_id=kb.id, user_id=user.id, role="reader"),
            ]
        )
        db.commit()
        kb_id, reader_id = kb.id, user.id
    row = create(c, reader, data(source=source))
    assert c.get(url(row), headers=h).status_code == 404  # source admin is not memory owner
    with c.app.state.database.session() as db:
        db.delete(db.get(KnowledgeBaseGrant, (kb_id, reader_id)))
        db.commit()
    assert c.get(url(row), headers=reader).json()["source_availability"] == "unavailable"
    assert "PRIVATE-MEMORY" not in c.get("/api/memories", headers=reader).text


def test_edit_requires_reconfirmation_and_cannot_rebind_source(mc):
    c, h = mc
    row = create(c, h)
    for changes in [{"confirmed": False}, {"owner_id": new_id()}, {"source": {"type": "user_input"}}]:
        response = c.put(url(row), headers=h, json=data(expected_version=1, reason="x", **changes))
        assert response.status_code == 422
    assert (
        c.request("DELETE", url(row), headers=h, json={"request_id": new_id(), "expected_version": 2}).status_code
        == 409
    )
    assert c.get(url(row), headers=h).json()["version"] == 1
    body = data()
    del body["confirmed"]
    assert c.post("/api/memories", headers=h, json=body).status_code == 422


def test_simultaneous_duplicate_create_is_single_memory(mc):
    c, h = mc
    barrier = Barrier(2)
    body = MemoryCreate(**data())

    def run(_):
        with c.app.state.database.session() as db:
            user = db.scalar(select(User).where(User.email == "owner@example.com"))
            barrier.wait(timeout=10)
            return memories.mutate(db, user, "create", body)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, [0, 1]))
    assert ids[0] == ids[1]
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(PersonalMemory)) == 1
        assert db.scalar(select(func.count()).select_from(MemoryRevision)) == 1
        assert db.scalar(select(func.count()).select_from(MemoryOperation)) == 1
