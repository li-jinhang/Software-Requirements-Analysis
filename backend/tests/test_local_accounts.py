"""Exercise real loopback account switching and portable backups."""

from io import BytesIO
import json
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
