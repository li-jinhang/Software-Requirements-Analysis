"""Skills, account isolation, graph dispatch and real MCP protocol regression."""
from io import BytesIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from zipfile import ZipFile

import httpx
import pytest


@pytest.fixture
def caps(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.api.v1 import capabilities
    from app.core.security import current_user
    from app.core.config import settings
    from app.db.database import Base, get_db
    from app.db.models import User, Document, KnowledgeNode, Session
    from app.services import extension_store, memory_service
    from app.agents import capability_graph
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'business.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add_all([User(id=i, username=f"u{i}", nickname="test", password_hash="unused", role="admin" if i == 1 else "student") for i in (1,2)])
        db.flush()
        db.add(Document(id=1, user_id=1, title="课程", subject="数学", file_type="md", file_path="unused", parse_status="completed"))
        db.flush()
        db.add(KnowledgeNode(id=1, doc_id=1, concept_name="梯度", summary="负梯度方向", importance=3))
        db.add(Session(id=1, user_id=1, doc_id=1, title="验收", persona_type="peer"))
    def dependency():
        with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(capabilities.router)
    identity = SimpleNamespace(id=1, role="admin")
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[current_user] = lambda: identity
    monkeypatch.setattr(capabilities, "require_local_workbench", lambda request: None)
    monkeypatch.setattr(capabilities, "SessionLocal", factory)
    monkeypatch.setattr(memory_service, "search_rag", lambda *a, **k: [])
    calls = []
    def text(instructions, material, on_chunk=None):
        calls.append((instructions, json.loads(material)))
        if on_chunk:
            on_chunk("## 复习笔记\n")
            on_chunk("沿负梯度方向更新。")
        return "## 复习笔记\n沿负梯度方向更新。"
    monkeypatch.setattr(capability_graph, "require_profile", lambda _: {})
    monkeypatch.setattr(capability_graph, "ModelConnection", lambda _: SimpleNamespace(text=text))
    with TestClient(app) as api:
        yield SimpleNamespace(api=api, identity=identity, factory=factory, calls=calls, storage=extension_store, folder=tmp_path)
    engine.dispose()


def package(name="test-study", body="根据课程写复习提纲", extra=None):
    out = BytesIO()
    with ZipFile(out, "w") as z:
        z.writestr(f"{name}/SKILL.md", f"---\nname: {name}\ndescription: 复习技能\nmetadata:\n  version: '2.0'\n---\n{body}\n")
        for path, text in (extra or {}).items():
            z.writestr(path, text)
    return out.getvalue()


def upload(t, data=None, **fields):
    return t.api.post("/capabilities/skills/import", files={"file": ("skill.zip", data or package())}, data=fields)


def test_import_update_disable_and_private_isolation(caps):
    t = caps
    result = upload(t)
    assert result.status_code == 201, result.text
    skill = result.json()
    assert skill["available"] and skill["version"] == "2.0"
    assert upload(t).status_code == 409
    assert upload(t, replace="true").json()["id"] == skill["id"]
    assert t.api.patch("/capabilities/skills/" + skill["id"], json={"enabled": False}).status_code == 204
    assert not t.api.get("/capabilities").json()["skills"][0]["available"]
    t.identity.id, t.identity.role = 2, "student"
    assert t.api.get("/capabilities").json()["skills"] == []
    assert t.api.get("/capabilities/skills/" + skill["id"]).status_code == 404
    assert t.api.delete("/capabilities/skills/" + skill["id"]).status_code == 404


def test_public_skill_read_only_and_no_student_publish(caps):
    t = caps
    id = upload(t).json()["id"]
    t.api.patch("/capabilities/skills/" + id, json={"shared": True})
    t.identity.id, t.identity.role = 2, "student"
    row = t.api.get("/capabilities").json()["skills"][0]
    assert row["shared"] and not row["editable"]
    assert t.api.patch("/capabilities/skills/" + id, json={"enabled": False}).status_code == 403
    own = upload(t).json()["id"]
    assert t.api.patch("/capabilities/skills/" + own, json={"shared": True}).status_code == 403


@pytest.mark.parametrize("extra", [{"../escape.txt":"bad"}, {"C:/bad.txt":"bad"}, {"test-study/other/SKILL.md":"duplicate"}])
def test_zip_path_and_multiple_skill_rejection(caps, extra):
    assert upload(caps, package(extra=extra)).status_code == 422
    assert caps.api.get("/capabilities").json()["skills"] == []


def test_script_skill_is_honestly_unavailable(caps):
    row = upload(caps, package(extra={"test-study/scripts/run.py":"raise RuntimeError()"})).json()
    assert not row["available"] and "脚本" in row["reason"]


def test_skill_uses_graph_and_persists_without_grading(caps):
    from app.db.models import KnowledgeNode, Message
    from sqlalchemy import select
    t = caps
    id = upload(t).json()["id"]
    body = {"session_id":1,"kind":"skill","capability_id":id,"content":"生成复习笔记","request_id":"unique-test-1"}
    result = t.api.post("/capabilities/execute", json=body)
    assert result.status_code == 200, result.text
    assert "复习笔记" in result.json()["message"]["content"]
    assert len(t.calls) == 1 and t.calls[0][1]["任务"] == body["content"]
    assert t.api.post("/capabilities/execute", json=body).json()["replayed"]
    assert len(t.calls) == 1
    with t.factory() as db:
        assert db.get(KnowledgeNode,1).mastery_score == 0
        assert len(db.scalars(select(Message)).all()) == 2
    t.identity.id = 2
    assert t.api.post("/capabilities/execute", json={**body,"request_id":"foreign-req-1"}).status_code == 404


def test_progress_history_are_non_model_actions(caps):
    for kind in ("progress", "history"):
        result = caps.api.post("/capabilities/execute", json={"session_id":1,"kind":kind,"request_id":"command-" + kind})
        assert result.status_code == 200, result.text
        assert result.json()["message"]["meta_json"]["no_assessment"]
    assert not caps.calls


def test_capability_stream_confirms_commit_and_does_not_grade(caps):
    id = upload(caps).json()["id"]
    response = caps.api.post("/capabilities/execute/stream", json={"session_id":1,"kind":"skill","capability_id":id,"content":"复习任务","request_id":"stream-skill-1"})
    assert response.status_code == 200
    assert "event: token" in response.text and "event: done" in response.text
    assert "event: error" not in response.text
    failed = caps.api.post("/capabilities/execute/stream", json={"session_id":1,"kind":"skill","capability_id":"missing","content":"复习任务","request_id":"stream-missing"})
    assert "event: error" in failed.text and "event: done" not in failed.text


def test_mcp_credentials_and_parameter_guards(caps):
    t = caps
    row = t.api.post("/capabilities/servers",json={"name":"test","url":"http://127.0.0.1:1/mcp","token":"private-test-secret"}).json()
    assert row["has_token"] and "private-test-secret" not in json.dumps(t.api.get("/capabilities").json())
    assert t.api.put("/capabilities/servers/"+row["id"],json={"name":"test","url":"http://127.0.0.1:2/mcp"}).status_code == 422
    assert t.api.post("/capabilities/servers",json={"name":"bad","url":"file:///private"}).status_code == 422
    response=t.api.post("/capabilities/execute",json={"session_id":1,"kind":"mcp","capability_id":row["id"],"request_id":"unconfirmed-1"})
    assert response.status_code == 422
    t.identity.id=2
    assert t.api.post("/capabilities/servers/"+row["id"]+"/connect").status_code == 404


def test_real_mcp_discovery_archive_and_calendar(caps):
    t = caps
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0)); port=sock.getsockname()[1]
    script=Path(__file__).resolve().parents[1]/"scripts"/"learning_mcp.py"
    with (t.folder/"mcp.log").open("w") as log:
        process=subprocess.Popen([sys.executable,str(script),"--port",str(port),"--directory",str(t.folder/"notes")],stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1",port),timeout=.1): break
                except OSError: time.sleep(.1)
            row=t.api.post("/capabilities/servers",json={"name":"学习档案","url":f"http://127.0.0.1:{port}/mcp"}).json()
            result=t.api.post("/capabilities/servers/"+row["id"]+"/connect")
            assert result.status_code==200,result.text
            assert len(result.json()["tools"])==4
            for tool,args in [("archive_learning_note",{"title":"学习率","content":"步长影响收敛"}), ("create_review_reminder",{"title":"复习学习率","date":"2026-12-01"})]:
                out=t.api.post("/capabilities/execute",json={"session_id":1,"kind":"mcp","capability_id":row["id"],"tool":tool,"arguments":args,"confirmed":True,"request_id":"real-"+tool})
                assert out.status_code==200,out.text
            assert len(list((t.folder/"notes").glob("*.md")))==1
            assert len(list((t.folder/"notes").glob("*.ics")))==1
            invalid=t.api.post("/capabilities/execute",json={"session_id":1,"kind":"mcp","capability_id":row["id"],"tool":"archive_learning_note","arguments":{},"confirmed":True,"request_id":"invalid-arguments"})
            assert invalid.status_code==422,invalid.text
        finally:
            process.terminate();process.wait(timeout=10)
