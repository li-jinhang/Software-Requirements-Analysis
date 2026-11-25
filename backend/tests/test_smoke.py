"""End-to-end checks against a real Uvicorn process and disposable persistent stores."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    yield from serve(tmp_path_factory)


@pytest.fixture(scope="module")
def fixture_server(tmp_path_factory):
    yield from serve(tmp_path_factory, fixture_inference=True)


def serve(tmp_path_factory, *, fixture_inference=False):
    work = tmp_path_factory.mktemp("backend")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (work / "server.log").open("w", encoding="utf-8") as log:
        env = {
            **os.environ,
            "DATA_DIR": str(work / "data"),
            "MOCK_LLM": "true",
            "EMBEDDING_PROVIDER": "local_lite",
            "SEED_DEMO": "true",
            "OPENAI_API_KEY": "",
            "PYTHONIOENCODING": "utf-8",
            "ANONYMIZED_TELEMETRY": "False",
        }
        command = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
        if fixture_inference:
            command = [
                "-c",
                "import sys, uvicorn; sys.path.insert(0, 'tests'); "
                "from fixture_inference import install; install(); "
                "uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]))",
                str(port),
            ]
        proc = subprocess.Popen(
            [sys.executable, *command],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=25, trust_env=False
            ) as api:
                api.data_dir = work / "data"
                for _ in range(150):
                    if proc.poll() is not None:
                        pytest.fail(
                            (work / "server.log").read_text(encoding="utf-8", errors="replace")
                        )
                    try:
                        if api.get("/docs").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.2)
                else:
                    pytest.fail("Uvicorn startup timeout")
                yield api
        finally:
            proc.terminate()
            proc.wait(timeout=15)


def login(api, name, password):
    r = api.post("/api/v1/auth/login", json={"username": name, "password": password})
    r.raise_for_status()
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_demo_and_access(server):
    api = server
    assert "Swagger UI" in api.get("/docs").text
    assert "/api/v1/sessions/{id}/feynman/stream" in api.get("/openapi.json").json()["paths"]
    a = login(api, "admin", "admin123")
    s = login(api, "student", "123456")
    assert api.get("/api/v1/auth/me", headers=s).json()["role"] == "student"
    assert api.get("/api/v1/admin/stats", headers=s).status_code == 403
    assert api.get("/api/v1/admin/export/db").status_code == 401
    stats = api.get("/api/v1/admin/stats", headers=a).json()
    assert [stats[k] for k in ["total_users", "total_docs", "total_nodes", "total_sessions"]] == [
        2,
        1,
        8,
        1,
    ]
    ctx = api.get("/api/v1/documents/1/memory", headers=s).json()
    path = "/api/v1/documents/1/learning-path"
    assert api.get(path).status_code == 401
    assert api.get(path, headers=a).status_code == 404
    assert api.get(path, headers=s).json() == ctx["recommended_learning_path"]
    assert len(ctx["edges"]) == 9 and len(ctx["weaknesses"]) == 3
    assert all(n["status"] == "unlearned" and n["mastery_score"] == 0 for n in ctx["nodes"])
    assert api.get("/api/v1/sessions/1/report", headers=s).json()["clarity_score"] == 91
    assert {m["window_type"] for m in api.get("/api/v1/sessions/1/messages", headers=s).json()} == {
        "feynman",
        "encyclopedia",
    }


def events(resp):
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
    pairs = []
    for frame in resp.text.strip().split("\n\n"):
        event, data = frame.splitlines()
        pairs.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    assert pairs[-1][0] == "done"
    return pairs


def test_fixture_evaluation_preserves_focus_without_certifying_mastery(fixture_server):
    server = fixture_server
    h = login(server, "student", "123456")
    nodes = server.get("/api/v1/documents/1/graph", headers=h).json()["nodes"]
    target = next(n["id"] for n in nodes if n["concept_name"] == "梯度下降")
    sid = server.post("/api/v1/sessions", headers=h, json={"doc_id": 1, "node_id": target}).json()["id"]
    url = f"/api/v1/sessions/{sid}/feynman/stream"
    questions = []
    for topic in ["梯度下降", "过拟合", "过拟合"]:
        frames = events(server.post(url, headers=h, json={"content": topic}))
        questions.append("".join(d["delta"] for k, d in frames if k == "token"))
        if topic == "梯度下降":
            update = next(d["updates"][0] for k, d in frames if k == "memory_update")
            assert update["concept_name"] == topic and update["mastery_score"] < 80
    assert "「梯度下降」" in questions[0]
    assert all("「梯度下降」" in q for q in questions[1:])  # Unresolved criterion stays in focus.
    history = server.get(f"/api/v1/sessions/{sid}/messages", headers=h).json()
    assert all(m["meta_json"]["mode"] == "offline" for m in history
               if m["sender_role"] == "assistant" and m["window_type"] == "feynman")


def test_upload_teach_lookup_report_export(fixture_server, tmp_path):
    api = fixture_server
    base = "/api/v1"
    account = {"username": "smoke-user", "password": "smoke-123", "nickname": "联调"}
    assert api.post(base + "/auth/register", json=account).status_code == 201
    assert api.post(base + "/auth/register", json=account).status_code == 409
    h = login(api, account["username"], account["password"])
    admin = login(api, "admin", "admin123")
    assert api.get(base + "/documents/1", headers=h).status_code == 404
    source = (
        "梯度下降：沿负梯度更新参数。学习率：控制更新步长。正则化：约束模型复杂度。"
        "损失函数：衡量预测误差。验证集：用于选参。过拟合：训练表现好而验证表现较差。"
    ) * 12
    r = api.post(
        base + "/documents/upload",
        headers=h,
        files={"file": ("课程.md", source.encode(), "text/markdown")},
        data={"subject": "机器学习"},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["parse_status"] == "completed" and doc["chunk_count"] >= 2
    url = base + "/documents/" + str(doc["id"])
    graph = api.get(url + "/graph", headers=h).json()
    assert 6 <= len(graph["nodes"]) <= 15 and graph["edges"]
    n = graph["nodes"][0]
    assert (
        api.patch(url + f"/nodes/{n['id']}/note", headers=h, json={"user_note": "复习例题"}).json()[
            "user_note"
        ]
        == "复习例题"
    )
    archive = api.get(url + "/memory/export?format=json", headers=h).json()
    assert list(archive) == [
        "核心概念树",
        "概念关联说明",
        "已掌握清单",
        "薄弱点警示",
        "推荐学习与复讲路径",
        "Agent 认知文档",
    ]
    md = api.get(url + "/memory/export?format=md", headers=h).text
    assert all("## " + key in md for key in archive) and "复习例题" in md

    session = api.post(base + "/sessions", headers=h, json={"doc_id": doc["id"]}).json()
    chat = base + "/sessions/" + str(session["id"])
    assert (
        api.patch(chat + "/persona", headers=h, json={"persona_type": "examiner"}).json()[
            "persona_type"
        ]
        == "examiner"
    )
    left = events(
        api.post(chat + "/feynman/stream", headers=h, json={"content": "梯度下降沿负梯度更新。"})
    )
    assert any(event == "memory_update" and data["updates"][0]["delta"] > 0 for event, data in left)
    right = events(
        api.post(chat + "/encyclopedia/stream", headers=h, json={"content": "学习率是什么？"})
    )
    hits = next(data["citations"] for event, data in right if event == "citation")
    assert hits and all(hit["snippet"] in source and hit["doc_title"] == "课程" for hit in hits)
    history = api.get(chat + "/messages", headers=h).json()
    assert len(history) == 5 and history[-1]["id"] == right[-1][1]["message_id"]
    assert history[0]["sender_role"] == "system" and "听众已切换" in history[0]["content"]
    assert history[-1]["related_node_ids"] and all(
        node_id in {n["id"] for n in graph["nodes"]} for node_id in history[-1]["related_node_ids"]
    )
    assert history[-1]["content"] == "".join(
        data["delta"] for event, data in right if event == "token"
    )
    ctx = api.get(url + "/memory", headers=h).json()
    assert any(w["weakness_type"] == "need_lookup" for w in ctx["weaknesses"])
    assert sum(n["lookup_count"] for n in ctx["nodes"]) == 1
    report = api.post(chat + "/report", headers=h).json()
    assert all(
        0 <= report[k + "_score"] <= 100 for k in ["coverage", "accuracy", "clarity", "logic"]
    )
    assert (
        api.get(chat + "/report", headers=h).json()
        == report
        == api.post(chat + "/report", headers=h).json()
    )
    assert (
        api.post(chat + "/feynman/stream", headers=h, json={"content": "继续"}).status_code == 409
    )

    records = api.get(
        base + "/admin/tables/documents", headers=admin, params={"q": "课程", "page_size": 1}
    ).json()
    assert records["total"] == 1 and records["items"][0]["id"] == doc["id"]
    assert api.get(base + "/admin/stats", headers=admin).json()["top_weaknesses"]
    snap = api.get(base + "/admin/export/json", headers=admin)
    assert len(snap.json()) == 10 and "retired_account_ids" in snap.json()
    assert snap.json()["cognitive_items"] and "password_hash" not in snap.text
    backup = tmp_path / "restore.db"
    backup.write_bytes(api.get(base + "/admin/export/db", headers=admin).content)
    with sqlite3.connect(backup) as db:
        assert db.execute("pragma integrity_check").fetchone() == ("ok",)
        assert db.execute(
            "select count(*) from messages where session_id=?", (session["id"],)
        ).fetchone() == (5,)
    assert (
        api.delete(base + f"/admin/tables/documents/{doc['id']}", headers=admin).status_code == 204
    )
    assert api.get(url, headers=h).status_code == 404
    assert api.get(chat + "/messages", headers=h).status_code == 404


def test_retry_and_cross_store_cleanup(tmp_path, monkeypatch):
    # The HTTP tests import the app only in child processes. This isolated
    # component test also inspects vector records that have no public list API.
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local_lite")
    from app.main import app
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import Document
    from app.services import rag_store
    from app.services.memory_service import update_mastery
    from fixture_inference import install

    with TestClient(app) as api:
        install(monkeypatch.setattr)
        h = login(api, "student", "123456")
        response = api.post(
            "/api/v1/documents/upload", headers=h, files={"file": ("empty.md", b" ", "text/plain")}
        )
        assert response.status_code == 422, response.text
        id = response.json()["detail"]["doc_id"]
        url = f"/api/v1/documents/{id}"
        assert api.get(url, headers=h).json()["parse_status"] == "failed"
        with SessionLocal() as db:
            assert db.connection().exec_driver_sql("pragma journal_mode").scalar() == "wal"
            assert db.connection().exec_driver_sql("pragma busy_timeout").scalar() == 10000
            path = Path(db.get(Document, id).file_path)
        path.write_text(
            "损失函数：衡量误差。学习率：更新步长。梯度：损失变化的方向。", encoding="utf-8"
        )
        assert api.post(url + "/retry", headers=h).json()["parse_status"] == "completed"
        assert api.post(url + "/retry", headers=h).status_code == 409
        assert rag_store.search(id, "学习率")[0]["snippet"] in path.read_text(encoding="utf-8")
        node = api.get(url + "/graph", headers=h).json()["nodes"][0]
        with SessionLocal.begin() as db:
            assert update_mastery(db, node["id"], 999)["mastery_score"] == 100
            assert update_mastery(db, node["id"], -21)["status"] == "learning"
            assert update_mastery(db, node["id"], -999)["mastery_score"] == 0
        assert api.delete(url, headers=h).status_code == 204
        assert not path.exists() and not rag_store.search(id, "学习率")
        assert rag_store.search(1, "过拟合")


def test_delete_session_and_report_cascade_and_ownership(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local_lite")
    from app.main import app
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import (
        CognitiveItem,
        DiagnosticReport,
        Document,
        KnowledgeEdge,
        KnowledgeNode,
        Message,
        Session,
        WeaknessLog,
    )
    from app.services import rag_store
    from fixture_inference import install

    with TestClient(app) as api:
        install(monkeypatch.setattr)
        assert api.post("/api/v1/auth/register", json={"username": "owner-a", "password": "pw-123456", "nickname": "A"}).status_code == 201
        assert api.post("/api/v1/auth/register", json={"username": "other-b", "password": "pw-123456", "nickname": "B"}).status_code == 201
        ha = login(api, "owner-a", "pw-123456")
        hb = login(api, "other-b", "pw-123456")

        source = (
            "梯度下降：沿负梯度更新参数。学习率：控制更新步长。正则化：约束模型复杂度。"
            "损失函数：衡量预测误差。过拟合：训练好验证差。"
        ) * 10
        doc = api.post(
            "/api/v1/documents/upload",
            headers=ha,
            files={"file": ("测试删除.md", source.encode(), "text/markdown")},
            data={"subject": "机器学习"},
        ).json()
        doc_id = doc["id"]

        s1 = api.post("/api/v1/sessions", headers=ha, json={"doc_id": doc_id}).json()["id"]
        s2 = api.post("/api/v1/sessions", headers=ha, json={"doc_id": doc_id}).json()["id"]
        for sid in (s1, s2):
            events(api.post(f"/api/v1/sessions/{sid}/feynman/stream", headers=ha, json={"content": "梯度下降沿负梯度更新。"}))
            events(api.post(f"/api/v1/sessions/{sid}/encyclopedia/stream", headers=ha, json={"content": "学习率是什么？"}))
            assert api.post(f"/api/v1/sessions/{sid}/report", headers=ha).status_code == 200

        # Non-owner attempts are rejected cleanly with 404.
        assert api.delete(f"/api/v1/sessions/{s1}/report", headers=hb).status_code == 404
        assert api.delete(f"/api/v1/sessions/{s1}", headers=hb).status_code == 404
        assert api.delete(f"/api/v1/documents/{doc_id}", headers=hb).status_code == 404

        # Delete only report of s1: report disappears while session and messages remain.
        assert api.delete(f"/api/v1/sessions/{s1}/report", headers=ha).status_code == 204
        assert api.delete(f"/api/v1/sessions/{s1}/report", headers=ha).status_code == 404
        assert api.get(f"/api/v1/sessions/{s1}/report", headers=ha).status_code == 404
        assert len(api.get(f"/api/v1/sessions/{s1}/messages", headers=ha).json()) >= 4
        assert {row["id"] for row in api.get("/api/v1/sessions", headers=ha).json()} == {s1, s2}

        # Delete s2: cascades its messages, weakness_logs and diagnostic_report, leaving s1 & doc intact.
        assert api.delete(f"/api/v1/sessions/{s2}", headers=ha).status_code == 204
        assert api.delete(f"/api/v1/sessions/{s2}", headers=ha).status_code == 404
        assert {row["id"] for row in api.get("/api/v1/sessions", headers=ha).json()} == {s1}
        with SessionLocal() as db:
            assert db.get(Session, s2) is None
            assert list(db.scalars(select(Message).where(Message.session_id == s2))) == []
            assert list(db.scalars(select(WeaknessLog).where(WeaknessLog.session_id == s2))) == []
            assert list(db.scalars(select(DiagnosticReport).where(DiagnosticReport.session_id == s2))) == []
            assert db.get(Session, s1) is not None
            doc_path = Path(db.get(Document, doc_id).file_path)
            assert doc_path.exists()

        # Delete document: cascades remaining s1, nodes, edges, items, messages, weaknesses, file & chroma chunks.
        assert api.delete(f"/api/v1/documents/{doc_id}", headers=ha).status_code == 204
        assert api.get("/api/v1/documents", headers=ha).json() == []
        assert api.get("/api/v1/sessions", headers=ha).json() == []
        assert not doc_path.exists()
        assert not rag_store.search(doc_id, "梯度下降")
        with SessionLocal() as db:
            assert db.get(Document, doc_id) is None
            assert list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.doc_id == doc_id))) == []
            assert list(db.scalars(select(KnowledgeEdge).where(KnowledgeEdge.doc_id == doc_id))) == []
            assert list(db.scalars(select(CognitiveItem).where(CognitiveItem.doc_id == doc_id))) == []
            assert list(db.scalars(select(Session).where(Session.doc_id == doc_id))) == []
            assert list(db.scalars(select(Message).where(Message.session_id == s1))) == []
            assert list(db.scalars(select(WeaknessLog).where(WeaknessLog.session_id == s1))) == []
