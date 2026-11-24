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
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", timeout=25, trust_env=False
            ) as api:
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
    assert len(ctx["edges"]) == 9 and len(ctx["weaknesses"]) == 3
    assert {n["status"] for n in ctx["nodes"]} == {"mastered", "learning", "weak"}
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


def test_upload_teach_lookup_report_export(server, tmp_path):
    api = server
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
    assert list(archive) == ["核心概念树", "概念关联说明", "已掌握清单", "薄弱点警示"]
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
    assert len(snap.json()) == 8 and "password_hash" not in snap.text
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

    with TestClient(app) as api:
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
