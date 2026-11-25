"""Exercise real loopback account switching and portable backups."""

from io import BytesIO
import json
import sqlite3
from zipfile import ZipFile

from test_smoke import server


ACCOUNTS = "/api/v1/local-accounts"
DOCS = "/api/v1/documents"
SESSIONS = "/api/v1/sessions"


def auth(response):
    assert response.is_success, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_local_accounts_round_trip_without_cross_account_access(server):
    assert server.get(ACCOUNTS, headers={"Origin": "https://outside.example"}).status_code == 403
    roster = server.get(ACCOUNTS).json()
    student = next(account for account in roster if account["username"] == "student")
    student_path = f"{ACCOUNTS}/{student['id']}/activate"
    assert student["protected"] and server.post(student_path, json={}).status_code == 401
    original = auth(server.post(student_path, json={"password": "123456"}))
    created = server.post(ACCOUNTS, json={"name": "独立账号"})
    empty = auth(created)
    empty_path = f"{ACCOUNTS}/{created.json()['user']['id']}/activate"
    assert server.get(DOCS, headers=auth(server.post(empty_path, json={}))).json() == []
    assert server.get(DOCS + "/1", headers=empty).status_code == 404
    assert server.get(ACCOUNTS + "/export").status_code == 401
    backup = server.get(ACCOUNTS + "/export", headers=original)
    assert backup.is_success, backup.text
    with ZipFile(BytesIO(backup.content)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["account.json"])
    assert manifest["name"] == "演示学生"
    assert len(manifest["documents"]) == 1 and manifest["documents"][0]["chunks"]
    assert "password_hash" not in entries["account.json"].decode()
    assert "sources/1.md" in entries

    imported = server.post(ACCOUNTS + "/import", files={"file": ("student.zip", backup.content)})
    restored = auth(imported)
    assert imported.json()["user"]["nickname"] == "演示学生（导入）"
    assert server.get(DOCS + "/1", headers=restored).status_code == 404
    [copy] = server.get(DOCS, headers=restored).json()
    assert copy["id"] != 1 and copy["chunk_count"] == manifest["documents"][0]["chunk_count"]
    path = f"{DOCS}/{copy['id']}"
    graph = server.get(path + "/graph", headers=restored).json()
    assert len(graph["nodes"]) == len(manifest["nodes"]) and len(graph["edges"]) == len(manifest["edges"])
    node = graph["nodes"][0]
    report_session = server.get(SESSIONS, headers=restored).json()[0]["id"]
    report_path = f"{SESSIONS}/{report_session}"
    assert report_session != 1
    assert len(server.get(report_path + "/messages", headers=restored).json()) == len(manifest["messages"])
    assert server.get(report_path + "/report", headers=restored).json()["clarity_score"] == 91
    assert server.patch(path + f"/nodes/{node['id']}/note", headers=restored, json={"user_note": "副本笔记"}).is_success
    assert server.get(DOCS + "/1/graph", headers=original).json()["nodes"][0]["user_note"] != "副本笔记"
    assert server.get(path + "/graph", headers=empty).status_code == 404
    with ZipFile(BytesIO(server.get(ACCOUNTS + "/export", headers=restored).content)) as archive:
        assert archive.read(f"sources/{copy['id']}.md") == entries["sources/1.md"]

    roster = server.get(ACCOUNTS).json()
    manifest["nodes"][0]["doc_id"] = -1
    entries["account.json"] = json.dumps(manifest).encode()
    broken = BytesIO()
    with ZipFile(broken, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    assert server.post(ACCOUNTS + "/import", files={"file": ("broken.zip", broken.getvalue())}).status_code == 422
    assert server.get(ACCOUNTS).json() == roster


def test_delete_requires_exact_confirmation_and_removes_only_current_account(server):
    roster = server.get(ACCOUNTS).json()
    student_id = next(account["id"] for account in roster if account["username"] == "student")
    original = auth(server.post(f"{ACCOUNTS}/{student_id}/activate", json={"password": "123456"}))
    backup = server.get(ACCOUNTS + "/export", headers=original)
    backup.raise_for_status()
    uploads = server.data_dir / "uploads"
    sources = set(uploads.iterdir())
    imported = server.post(ACCOUNTS + "/import", files={"file": ("student.zip", backup.content)})
    headers = auth(imported)
    deleted_id = imported.json()["user"]["id"]
    private_server = server.post("/api/v1/capabilities/servers", headers=headers, json={
        "name": "账号私有连接", "url": "http://127.0.0.1:1/mcp", "token": "account-private-key",
    })
    assert private_server.status_code == 201, private_server.text
    [new_source] = set(uploads.iterdir()) - sources
    [doc] = server.get(DOCS, headers=headers).json()
    [session] = server.get(SESSIONS, headers=headers).json()
    endpoint = ACCOUNTS + "/current"
    exact = {"confirmation": "确认删除"}
    assert server.request("DELETE", endpoint, json=exact).status_code == 401
    assert server.request("DELETE", endpoint, json=exact,
                          headers={**headers, "Origin": "https://outside.example"}).status_code == 403
    for invalid in ({}, {"confirmation": "确认"}, {"confirmation": "确认删除 "}, {**exact, "id": student_id}):
        assert server.request("DELETE", endpoint, headers=headers, json=invalid).status_code == 422
    assert server.get(DOCS, headers=headers).json() == [doc]
    assert new_source.is_file()

    response = server.request("DELETE", endpoint, headers=headers, json=exact)
    assert response.status_code == 204, response.text
    assert server.get(ACCOUNTS).json() == roster
    assert server.get(DOCS, headers=headers).status_code == 401
    with sqlite3.connect(server.data_dir / "capabilities" / "private.sqlite3") as extensions:
        assert extensions.execute("SELECT COUNT(*) FROM servers WHERE owner=?", (deleted_id,)).fetchone() == (0,)
    assert not new_source.exists() and all(source.is_file() for source in sources)
    assert not (server.data_dir / f".delete-{doc['id']}.pending").exists()
    with sqlite3.connect(server.data_dir / "feynman.db") as db:
        for table, column, value in [("users", "id", deleted_id), ("documents", "id", doc["id"]),
                                     ("knowledge_nodes", "doc_id", doc["id"]), ("sessions", "id", session["id"]),
                                     ("messages", "session_id", session["id"]),
                                     ("diagnostic_reports", "session_id", session["id"])]:
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (value,)).fetchone() == (0,)
    assert len(server.get(DOCS, headers=original).json()) == 1
    assert len(server.get(SESSIONS, headers=original).json()) == 1
    registered = server.post("/api/v1/auth/register", json={
        "username": "replacement", "nickname": "新注册账号", "password": "test-pass-123",
    })
    assert registered.status_code == 201 and registered.json()["id"] > deleted_id
    replacement = server.post(ACCOUNTS, json={"name": imported.json()["user"]["nickname"]})
    assert replacement.status_code == 201 and replacement.json()["user"]["id"] > registered.json()["id"]
    assert server.get(DOCS, headers=headers).status_code == 401


def test_seed_leaves_an_intentionally_empty_account_list_empty(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.core.config import settings
    from app.db.database import Base
    from app.db.models import RetiredAccountId, User
    from app.db.seed import seed

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(RetiredAccountId(id=2))
            db.commit()
            seed(db)
            assert db.scalar(select(User.id)) is None
    finally:
        engine.dispose()
