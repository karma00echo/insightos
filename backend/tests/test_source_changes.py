"""Isolated T10 comparison, authorization and reference-only persistence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from app.memory_models import MemoryRevision
from app.models import Chunk, Document, DocumentRevision, KnowledgeBase, Organization, User, new_id, utcnow
from app.services import source_changes
from app.source_change_models import SourceChange
from app.source_change_schemas import ChangeDetect
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_api import register
from test_decisions import body as decision_body
from test_decisions import create as decision_create
from test_memories import create as memory_create
from test_memories import data, seed_source


def pair(kind, sid, old, new, new_id=None, **kw):
    return dict(kind=kind, old=dict(id=sid, version=str(old)), new=dict(id=new_id or sid, version=str(new)), confirmed=True, **kw)


def memory_pair(c, h, same=False):
    m = memory_create(c, h, data(kind="plan", content="PRIVATE old goal"))
    assert c.put("/api/memories/" + m["id"], headers=h, json=dict(request_id=new_id(), expected_version=1,
                confirmed=True, kind="plan", content="PRIVATE old goal" if same else "PRIVATE new goal", reason="user edited")).status_code == 200
    return m, pair("memory", m["id"], 1, 2)


def document_pair(c, same=False):
    ref = seed_source(c, "document_chunk")
    with c.app.state.database.session() as db:
        chunk = db.get(Chunk, ref["id"])
        rev = db.get(DocumentRevision, ref["version"])
        new = DocumentRevision(id=new_id(), document_id=chunk.document_id, name=rev.name, stored_path=rev.stored_path,
                               sha256=rev.sha256, size_bytes=rev.size_bytes, status="ready", chunk_count=1,
                               created_at=rev.created_at + timedelta(seconds=1))
        from pathlib import Path

        new_path = Path(rev.stored_path).with_name(new.id + ".md")
        new_path.write_text(Path(rev.stored_path).read_text() if same else "PRIVATE updated source", encoding="utf-8")
        new.stored_path = str(new_path)
        db.add(new)
        db.flush()
        db.add(Chunk(id=new_id(), document_id=chunk.document_id, revision_id=new.id, ordinal=0, text=chunk.text if same else "PRIVATE updated source", token_count=4, page=1, locator={"line_start": 1}))
        db.get(Document, chunk.document_id).active_revision_id = new.id
        db.commit()
        return ref, pair("document", chunk.document_id, rev.id, new.id)


@pytest.mark.parametrize("kind,same", [("memory", False), ("memory", True), ("document", False), ("document", True)])
def test_compare_and_deduplicate(client, kind, same):
    c = client
    h = register(c)
    _, body = memory_pair(c, h, same) if kind == "memory" else document_pair(c, same)
    first = c.post("/api/source-changes/detect", headers=h, json=body)
    assert first.status_code == 200, first.text
    r = first.json()
    assert r["state"] == ("unchanged" if same else "changed")
    assert bool(r["differences"]) != same
    assert c.post("/api/source-changes/detect", headers=h, json=body).json()["id"] == r["id"]
    assert len(c.get("/api/source-changes", headers=h).json()["items"]) == 1
    with c.app.state.database.session() as db:
        row = db.scalar(select(SourceChange))
        assert all("PRIVATE" not in str(getattr(row, col.name)) for col in SourceChange.__table__.columns)


@pytest.mark.parametrize("loss", ["revoke", "delete", "expire", "missing_version", "missing_file"])
def test_document_privacy_on_all_reads(client, loss):
    c = client
    h = register(c)
    _, body = document_pair(c)
    r = c.post("/api/source-changes/detect", headers=h, json=body).json()
    with c.app.state.database.session() as db:
        doc = db.get(Document, body["old"]["id"])
        if loss == "revoke":
            org = Organization(name="other")
            db.add(org)
            db.flush()
            db.get(KnowledgeBase, doc.knowledge_base_id).organization_id = org.id
        elif loss == "delete":
            db.delete(doc)
        elif loss == "expire":
            doc.expires_at = utcnow() - timedelta(seconds=1)
        elif loss == "missing_version":
            db.delete(db.get(DocumentRevision, body["old"]["version"]))
        else:
            db.get(DocumentRevision, body["old"]["version"]).stored_path += ".missing"
        db.commit()
    assert c.get("/api/source-changes", headers=h).json()["items"] == []
    assert c.get("/api/source-changes/" + r["id"], headers=h).json() == {"id": r["id"], "state": "unavailable"}
    assert c.post("/api/source-changes/detect", headers=h, json=body).status_code == 404


@pytest.mark.parametrize("loss", ["revoke", "delete"])
def test_memory_loss_never_becomes_goal_change(client, loss):
    c = client
    h = register(c)
    m, body = memory_pair(c, h)
    r = c.post("/api/source-changes/detect", headers=h, json=body).json()
    p = "/api/memories/" + m["id"]
    cmd = dict(request_id=new_id(), expected_version=2)
    if loss == "revoke":
        assert c.post(p + "/revoke", headers=h, json=cmd).status_code == 200
    else:
        assert c.request("DELETE", p, headers=h, json=cmd).status_code == 204
    assert c.get("/api/source-changes/" + r["id"], headers=h).json()["state"] == "unavailable"
    assert c.get("/api/source-changes", headers=h).json()["items"] == []
    assert c.post("/api/source-changes/detect", headers=h, json=body).status_code == 404


def test_fixed_original_document_and_explicit_decision_reference(client):
    c = client
    h = register(c)
    ref, body = document_pair(c)
    d = decision_create(c, h, decision_body(basis=[ref]))
    r = c.post("/api/source-changes/detect", headers=h, json=body).json()
    detail = c.get("/api/source-changes/" + r["id"], headers=h).json()
    assert detail["references"][0]["decision_id"] == d["id"]
    assert detail["references"][0]["relationship"] == "explicit_reference_only"
    assert c.get("/api/decisions/" + d["id"], headers=h).json()["revision"]["availability"] == "available_original_version"
    assert "PRIVATE-REASON" not in str(detail)


def test_memory_reference_and_unconfirmed_plan_rejected(client):
    c = client
    h = register(c)
    m, body = memory_pair(c, h)
    d = decision_create(c, h, decision_body(basis=[dict(type="memory", id=m["id"], revision=1)]))
    assert c.post("/api/memories", headers=h, json=data(confirmed=False)).status_code == 422
    r = c.post("/api/source-changes/detect", headers=h, json=body).json()
    assert c.get("/api/source-changes/" + r["id"], headers=h).json()["references"][0]["decision_id"] == d["id"]
    with c.app.state.database.session() as db:
        rev = db.get(MemoryRevision, (m["id"], 2))
        rev.confirmation, rev.confirmed_at = "revoked", None
        db.commit()
    assert c.post("/api/source-changes/detect", headers=h, json=body).status_code == 404


def test_owner_admin_forgery_and_no_implicit_pair(client):
    c = client
    h = register(c)
    m, body = memory_pair(c, h)
    r = c.post("/api/source-changes/detect", headers=h, json=body).json()
    for label in ["b", "admin"]:
        other = register(c, label + "@example.com")
        if label == "admin":
            assert c.post("/api/organizations", headers=other, json={"name": "org"}).status_code == 201
        assert c.get("/api/source-changes", headers=other).json()["items"] == []
        assert c.get("/api/source-changes/" + r["id"], headers=other).status_code == 404
        assert c.post("/api/source-changes/detect", headers=other, json=body).status_code == 404
    for invalid in [{**body, "confirmed": False}, {**body, "owner_id": new_id()}, {**body, "kind": "report"}, {**body, "new": {"id": m["id"], "version": "02"}}]:
        assert c.post("/api/source-changes/detect", headers=h, json=invalid).status_code == 422


def test_incomparable_and_explicit_cross_document_pair(client):
    c = client
    h = register(c)
    _, body = document_pair(c)
    with c.app.state.database.session() as db:
        db.get(DocumentRevision, body["new"]["version"]).name = "large.pdf"
        db.get(DocumentRevision, body["new"]["version"]).chunk_count = 10000
        db.commit()
    assert c.post("/api/source-changes/detect", headers=h, json=body).json()["state"] == "incomparable"
    _, other = document_pair(c)
    cross = {**body, "new": other["new"]}
    assert c.post("/api/source-changes/detect", headers=h, json={**cross, "confirmed": False}).status_code == 422
    assert c.post("/api/source-changes/detect", headers=h, json=cross).json()["relationship"] == "user_confirmed_pair"


def test_atomic_failure_retry_and_concurrent_dedup(client):
    c = client
    h = register(c)
    _, body = memory_pair(c, h)
    engine = c.app.state.database.engine

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO source_changes"):
            raise OperationalError("injected", {}, Exception("PRIVATE"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        assert c.post("/api/source-changes/detect", headers=h, json=body).status_code == 503
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    uid = c.get("/api/auth/me", headers=h).json()["id"]

    def detect(_):
        with c.app.state.database.session() as db:
            return source_changes.detect(db, db.get(User, uid), ChangeDetect(**body))["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(detect, range(2)))
    assert ids[0] == ids[1]
    with c.app.state.database.session() as db:
        assert db.scalar(select(func.count()).select_from(SourceChange)) == 1
