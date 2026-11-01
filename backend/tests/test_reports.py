"""Offline report contracts, persistence, exact exports and provenance failures."""
import copy
from pathlib import Path

import pytest
from app.models import (
    Chunk,
    Document,
    DocumentRevision,
    EvaluationRun,
    KnowledgeBase,
    ResearchReport,
    ResearchTask,
    new_id,
)
from app.services import reports, research
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_api import create_kb, register


@pytest.fixture
def report_client(monkeypatch, request):
    monkeypatch.setattr(research, "run_research_worker", lambda database, settings, stop: stop.wait(60))
    # Any accidental model generation or retrieval during report tests fails.
    monkeypatch.setattr(research, "configured_llm", lambda *a, **k: pytest.fail("Report must not call a model"))
    return request.getfixturevalue("client")


def seed(c, h, texts=None, synthesis=False):
    texts = ["员工每年享有十五天带薪年假。"] if texts is None else texts
    kb = create_kb(c, h)
    evidence = []
    with c.app.state.database.session() as db:
        owner = db.get(KnowledgeBase, kb).user_id
        for i, text in enumerate(texts):
            did, rid, cid = new_id(), new_id(), new_id()
            path = Path(c.app.state.settings.data_dir) / (rid + ".md")
            path.write_text(text, encoding="utf8")
            doc = Document(id=did, knowledge_base_id=kb, name=f"source{i}.md", stored_path=str(path),
                sha256=rid, size_bytes=path.stat().st_size, status="ready", active_revision_id=rid)
            rev = DocumentRevision(id=rid, document_id=did, name=doc.name, stored_path=str(path),
                sha256=rid, size_bytes=doc.size_bytes, status="ready")
            chunk = Chunk(id=cid, document_id=did, revision_id=rid, text=text, page=1, ordinal=0,
                locator={"line_start": 1, "line_end": 1})
            db.add(doc)
            db.flush()
            db.add(rev)
            db.flush()
            db.add(chunk)
            research.add_evidence(evidence, {"id": cid, "text": text, "document_id": did,
                "revision_id": rid, "document_name": doc.name, "knowledge_base_id": kb, "page": 1,
                "locator": chunk.locator}, "s1")
        task = ResearchTask(user_id=owner, request_id=new_id(), question="员工年假制度有哪些规定？",
            knowledge_base_ids=[kb], status="completed", phase="completed", plan={"objective": "员工年假制度研究"},
            steps=[{"id": "s1", "question": "员工年假制度有哪些规定？", "status": "completed",
                "evidence_ids": [e["id"] for e in evidence], "rounds": [{"query": "年假规定"}]}], evidence=evidence,
            result={"provider": "offline_fixture", "findings": [{"kind": "evidence", "text": e["text"],
                "evidence_ids": [e["id"]], "step_ids": ["s1"]} for e in evidence], "analysis": [],
                "limitations": ["离线测试记录"], "conflicts": research.conflicts(evidence)})
        if synthesis and evidence:
            task.result["analysis"] = [{"kind": "synthesis", "text": "现有材料说明员工享有年假，具体适用条件仍需核对。",
                "evidence_ids": [e["id"] for e in evidence], "step_ids": ["s1"]}]
        db.add(task)
        db.commit()
        return task.id


def create(c, h, tid, key=None):
    return c.post("/api/research-reports", headers=h, json={"task_id": tid, "request_id": key or new_id()})


def test_versions_export_restart_and_no_model_calls(report_client):
    c, h = report_client, register(report_client)
    tid = seed(c, h, ["员工每年享有十五天带薪年假。", "请假需提前三天提交申请。"], synthesis=True)
    with c.app.state.database.session() as db:
        task_before = reports.digest(db.get(ResearchTask, tid).result)
        db.add(EvaluationRun(user_id=db.get(ResearchTask, tid).user_id, name="旧评测", cases=[{"research_task_id": tid}],
            metrics={"engineering": "ENGINEERING_PASS", "quality": "QUALITY_PENDING_HUMAN_REVIEW"}))
        db.commit()
    key = new_id()
    response = create(c, h, tid, key)
    assert response.status_code == 201, response.text
    first = response.json()
    assert len(first["content"]["sections"]) == 8
    assert first["content"]["validation"]["status"] == "VALID"
    assert first["content"]["generation"]["model_calls"] == 0
    assert first["content"]["evaluations"][0]["quality"] == "QUALITY_PENDING_HUMAN_REVIEW"
    assert first["content"]["claims"][-1]["quality_status"] == "NEEDS_REVIEW"
    assert first["quality_status"] == "QUALITY_PENDING_HUMAN_REVIEW"
    assert create(c, h, tid, key).json()["id"] == first["id"]
    second = create(c, h, tid).json()
    assert second["version"] == 2 and second["id"] != first["id"]
    path = "/api/research-reports/" + first["id"]
    exported = c.get(path + "/export", headers=h)
    assert exported.status_code == 200 and "charset=utf-8" in exported.headers["content-type"]
    assert "员工" in exported.content.decode("utf8")
    assert all(s["title"] in exported.text for s in first["content"]["sections"])
    with c.app.state.database.session() as db:
        row = db.get(ResearchReport, first["id"])
        assert exported.content == row.markdown.encode("utf8")
        assert reports.checksum(row.content, row.markdown) == row.content_sha256
        assert reports.digest(db.get(ResearchTask, tid).result) == task_before
    # A fresh app/database instance opens the persisted versions, without regeneration.
    from app.main import create_app
    with TestClient(create_app(c.app.state.settings)) as restarted:
        restored = restarted.get(path, headers=h).json()
        assert restored["content"] == first["content"]
        assert restarted.get(path + "/export", headers=h).content == exported.content
    assert len(c.get("/api/research-reports?task_id=" + tid, headers=h).json()) == 2


@pytest.mark.parametrize("state", ["queued", "running", "failed", "cancelled"])
def test_rejects_incomplete_tasks(report_client, state):
    c, h = report_client, register(report_client)
    tid = seed(c, h)
    with c.app.state.database.session() as db:
        db.get(ResearchTask, tid).status = state
        db.commit()
    assert create(c, h, tid).status_code == 409


def test_no_evidence_abstains_without_filling_facts(report_client):
    c, h = report_client, register(report_client)
    report = create(c, h, seed(c, h, [])).json()
    assert report["current_validation"]["status"] == "NO_CITATIONS"
    assert report["content"]["claims"] == []
    assert "证据不足" in c.get(f"/api/research-reports/{report['id']}/export", headers=h).text


def test_conflicts_are_retained_and_not_semantically_verified(report_client):
    c, h = report_client, register(report_client)
    tid = seed(c, h, ["员工住宿报销上限为每晚500元。", "员工住宿报销上限为每晚800元。"], synthesis=True)
    report = create(c, h, tid).json()
    assert all(f["quality_status"] == "POTENTIAL_CONFLICT" for f in report["content"]["claims"])
    assert report["current_validation"]["status"] == "VALID"
    assert report["current_validation"]["semantic_checked"] is False


@pytest.mark.parametrize("mutation", ["unknown_id", "outside_scope", "locator", "step", "missing_chunk"])
def test_invalid_bindings_preserve_reasons(report_client, mutation):
    c, h = report_client, register(report_client)
    tid = seed(c, h)
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        ev, result = copy.deepcopy(task.evidence), copy.deepcopy(task.result)
        if mutation == "unknown_id":
            result["findings"][0]["evidence_ids"] = ["e999"]
        elif mutation == "step":
            result["findings"][0]["step_ids"] = ["s9"]
        elif mutation == "missing_chunk":
            db.delete(db.get(Chunk, ev[0]["chunk_id"]))
        else:
            for origin in [ev[0], *ev[0]["origins"]]:
                if mutation == "outside_scope":
                    origin["knowledge_base_id"] = "forbidden"
                else:
                    origin["locator"] = {"line_start": 900, "line_end": 901}
        task.evidence, task.result = ev, result
        db.commit()
    response = create(c, h, tid)
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["current_validation"]["status"] == "INVALID"
    assert report["current_validation"]["checks"][0]["reasons"]
    assert report["content"]["claims"][0]["quality_status"] == "INSUFFICIENT_EVIDENCE"


def test_current_source_failure_does_not_change_saved_export(report_client):
    c, h = report_client, register(report_client)
    tid = seed(c, h)
    first = create(c, h, tid).json()
    path = "/api/research-reports/" + first["id"]
    original = c.get(path + "/export", headers=h).content
    with c.app.state.database.session() as db:
        doc = db.get(Document, first["content"]["evidence"][0]["document_id"])
        db.delete(doc)
        db.commit()
    restored = c.get(path, headers=h).json()
    assert restored["content"]["validation"]["status"] == "VALID"
    assert restored["current_validation"]["status"] == "INVALID"
    exported = c.get(path + "/export", headers=h)
    assert exported.content == original and exported.headers["x-current-citation-status"] == "INVALID"


def test_owner_scope_idempotency_and_revocation(report_client):
    c, h = report_client, register(report_client)
    other = register(c, "stranger-report@example.com")
    tid = seed(c, h)
    key = new_id()
    first = create(c, h, tid, key).json()
    tid2 = seed(c, h)
    assert create(c, h, tid2, key).status_code == 409
    assert create(c, other, tid).status_code == 404
    path = "/api/research-reports/" + first["id"]
    assert c.get(path, headers=other).status_code == 404
    assert c.get(path + "/export", headers=other).status_code == 404
    with c.app.state.database.session() as db:
        # Remove the owner's access while keeping historical rows.
        kb = db.get(KnowledgeBase, first["content"]["research"]["knowledge_base_ids"][0])
        kb.visibility = "restricted"
        from app.models import User
        kb.user_id = db.scalar(select(User.id).where(User.email == "stranger-report@example.com"))
        db.commit()
    assert c.get(path, headers=h).status_code == 403
    assert c.get(path + "/export", headers=h).status_code == 403
    assert all(r["id"] != first["id"] for r in c.get("/api/research-reports", headers=h).json())


def test_malformed_generation_tampering_and_failed_version_are_safe(report_client):
    c, h = report_client, register(report_client)
    tid = seed(c, h)
    first = create(c, h, tid).json()
    path = "/api/research-reports/" + first["id"]
    assert c.get(path + "/export?format=pdf", headers=h).status_code == 422
    with c.app.state.database.session() as db:
        task = db.get(ResearchTask, tid)
        task.result = {**task.result, "findings": [{"kind": "not-a-valid-kind", "text": "错误结构"}]}
        db.commit()
    assert create(c, h, tid).status_code == 422
    assert len(c.get("/api/research-reports?task_id=" + tid, headers=h).json()) == 1
    assert c.get(path, headers=h).status_code == 200
    with c.app.state.database.session() as db:
        db.get(ResearchReport, first["id"]).markdown += "tampered"
        db.commit()
    assert c.get(path, headers=h).status_code == 409
    assert c.get(path + "/export", headers=h).status_code == 409


def test_untrusted_markdown_is_literal():
    escaped = reports.md("<script>alert(1)</script> [假引用](https://example.com)\n# 伪造章节")
    assert "<script>" not in escaped and "\\[假引用\\]" in escaped and "\\#" in escaped
