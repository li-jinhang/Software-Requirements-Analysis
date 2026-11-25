from collections import Counter
from io import BytesIO
import json
from zipfile import ZipFile

from pydantic import ValidationError
import pytest
from time import monotonic, sleep

from test_local_accounts import ACCOUNTS, DOCS, auth
from test_smoke import events, fixture_server, login


def test_seeded_demo_uses_the_course_library_contract(fixture_server):
    api = fixture_server
    headers = login(api, "student", "123456")
    courses = {doc["title"]: doc for doc in api.get(DOCS, headers=headers).json()}
    assert set(courses) == {"机器学习基础：梯度下降与过拟合", "计算机网络"}
    for title, node_count, edge_count in (
        ("机器学习基础：梯度下降与过拟合", 8, 9),
        ("计算机网络", 20, 34),
    ):
        course = courses[title]
        assert course["is_library"] and course["library_id"] is None
        doc_path = f"{DOCS}/{course['id']}"
        graph = api.get(doc_path + "/graph", headers=headers).json()
        assert len(graph["nodes"]) == node_count and len(graph["edges"]) == edge_count
        archive = api.get(doc_path + "/memory/export", params={"format": "json"}, headers=headers)
        assert archive.status_code == 200 and len(archive.json()["核心概念树"]) == node_count
        if title == "计算机网络":
            assert Counter(n["status"] for n in graph["nodes"]) == {
                "mastered": 6, "learning": 5, "weak": 4, "unlearned": 5,
            }
            assert sum(bool(n["user_note"]) for n in graph["nodes"]) == 4
            report = api.get(doc_path + "/cognitive-doc", headers=headers).json()
            assert report["total"] == 40 and report["mastered_count"] == 17
            cards = api.get(doc_path + "/due-reviews", headers=headers).json()
            assert len(cards["due_items"]) == 12
            assert all(card["mastery_score"] == 100 for card in cards["due_items"])


def test_setup_returns_a_persisted_library_before_model_replies(fixture_server):
    api = fixture_server
    owner = auth(api.post(ACCOUNTS, json={"name": "后台建库学生"}))
    other = auth(api.post(ACCOUNTS, json={"name": "其他账号"}))
    gate = api.data_dir / "release-library-clarification"
    outage = api.data_dir / "fail-library-generation"
    outage.touch()

    def wait_for(path, status):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            response = api.get(path, headers=owner)
            assert response.is_success, response.text
            data = response.json()
            if data["parse_status"] == status:
                return data
            sleep(0.05)
        pytest.fail(f"资料库没有进入 {status}，当前为 {data['parse_status']}")

    try:
        job = api.post(DOCS + "/course-libraries/setup", headers=owner, json={"title": "操作系统"})
        assert job.status_code == 202, job.text
        library = job.json()
        path = DOCS + f"/{library['id']}"
        [visible] = api.get(DOCS, headers=owner).json()
        assert visible["id"] == library["id"] and visible["parse_status"] == "processing"
        assert api.get(path, headers=other).status_code == 404
        assert api.post(path + "/library-setup", headers=owner, json={}).status_code == 409
        gate.touch()
        pending = wait_for(path, "pending")
        question = pending["library_setup"]["question"]
        answer = {"question": question, "answer": "侧重进程调度"}
        assert api.post(path + "/library-setup", headers=other, json=answer).status_code == 404
        assert api.post(path + "/library-setup", headers=owner, json=answer).status_code == 202
        failed = wait_for(path, "failed")
        assert failed["error_message"] == "测试模型连接中断"
        assert failed["library_setup"]["request"]["answers"] == [answer]
        outage.unlink()
        assert api.post(path + "/library-setup", headers=owner, json={}).status_code == 202
        complete = wait_for(path, "completed")
        assert complete["id"] == library["id"] and complete["library_setup"] is None
        graph = api.get(path + "/graph", headers=owner).json()
        assert len(graph["nodes"]) == 12 and len(graph["edges"]) == 11
    finally:
        gate.touch()
        outage.unlink(missing_ok=True)


def test_library_source_keeps_owner_and_membership_after_backup(fixture_server):
    api = fixture_server
    owner = auth(api.post(ACCOUNTS, json={"name": "课程库所有者"}))
    other = auth(api.post(ACCOUNTS, json={"name": "其他学生"}))
    result = api.post(DOCS + "/course-libraries", json={"title": "操作系统"}, headers=owner)
    assert result.status_code == 201, result.text
    library = result.json()["document"]
    assert library["is_library"] and result.json()["graph"] == {"nodes": [], "edges": []}

    upload = {"data": {"library_id": library["id"]},
              "files": {"file": ("scheduling.md", "进程调度。抢占式调度。时间片轮转。".encode())}}
    assert api.post(DOCS + "/upload", headers=other, **upload).status_code == 404
    source = api.post(DOCS + "/upload", headers=owner, **upload)
    assert source.status_code == 201, source.text
    assert source.json()["library_id"] == library["id"] and not source.json()["is_library"]
    backup = api.get(ACCOUNTS + "/export", headers=owner)
    assert backup.status_code == 200, backup.text
    restored = auth(api.post(ACCOUNTS + "/import", files={"file": ("course.zip", backup.content)}))
    documents = api.get(DOCS, headers=restored).json()
    [new_library] = [doc for doc in documents if doc["is_library"]]
    [new_source] = [doc for doc in documents if not doc["is_library"]]
    assert new_library["id"] != library["id"] and new_source["library_id"] == new_library["id"]


def test_library_zip_round_trip_preserves_content_and_resets_learning(fixture_server):
    api = fixture_server
    owner = login(api, "student", "123456")
    recipient = auth(api.post(ACCOUNTS, json={"name": "接收资料库"}))
    library = next(doc for doc in api.get(DOCS, headers=owner).json() if doc["title"] == "计算机网络")
    path = DOCS + "/course-libraries"
    export = path + f"/{library['id']}/export"
    source_text = "# 滑动窗口控制\n\n发送方基于接收窗口限制在途数据并控制发送速率。".encode()
    uploaded = api.post(DOCS + "/upload", headers=owner, data={"library_id": library["id"]},
                        files={"file": ("滑动窗口控制.md", source_text)})
    assert uploaded.status_code == 201, uploaded.text
    source_id = uploaded.json()["id"]
    assert api.get(export, headers=recipient).status_code == 404
    assert api.get(path + f"/{source_id}/export", headers=owner).status_code == 404
    exported = api.get(export, headers=owner)
    assert exported.status_code == 200, exported.text
    assert exported.headers["Content-Type"] == "application/zip"
    with ZipFile(BytesIO(exported.content)) as archive:
        bundle = json.loads(archive.read("library.json"))
        assert set(bundle) == {"version", "library", "sources"}
        assert len(bundle["library"]["nodes"]) == 20 and len(bundle["library"]["edges"]) == 34
        assert not {"mastery_score", "user_note", "status"}.intersection(bundle["library"]["nodes"][0])
        assert not {"user_evidence", "issue_note", "review_count"}.intersection(bundle["library"]["items"][0])
        assert archive.read(f"sources/{source_id}.md") == source_text

    def import_zip(content):
        return api.post(path + "/import", headers=recipient, files={"file": ("课程.zip", content)})

    assert import_zip(b"invalid ZIP").status_code == 422
    assert api.get(DOCS, headers=recipient).json() == []
    imported = import_zip(exported.content)
    assert imported.status_code == 201, imported.text
    result = imported.json()
    copy = result["library"]["document"]
    [source] = result["sources"]
    assert copy["id"] != library["id"] and copy["title"] == library["title"]
    assert copy["structured_summary"] == library["structured_summary"]
    assert source["document"]["id"] != source_id and source["document"]["library_id"] == copy["id"]
    assert source["graph"]["nodes"]
    graph = result["library"]["graph"]
    fresh_ids = {node["id"] for node in graph["nodes"]}
    assert fresh_ids.isdisjoint(node["id"] for node in bundle["library"]["nodes"])
    assert all(edge[ref] in fresh_ids for edge in graph["edges"] for ref in ("source_node_id", "target_node_id"))
    assert all(node["status"] == "unlearned" and not node["mastery_score"] and not node["user_note"] for node in graph["nodes"])
    rubric = api.get(f"{DOCS}/{copy['id']}/cognitive-doc", headers=recipient).json()
    assert rubric["total"] == 40 and rubric["mastered_count"] == 0
    assert all(i["node_id"] in fresh_ids for section in rubric["sections"] for i in section["items"])
    sessions = DOCS.rsplit("/", 1)[0] + "/sessions"
    assert api.get(sessions, headers=recipient).json() == []
    convo = api.post(sessions, headers=recipient, json={"doc_id": copy["id"], "persona_type": "peer"})
    lookup = api.post(f"{sessions}/{convo.json()['id']}/encyclopedia/stream", headers=recipient,
                      json={"content": "滑动窗口控制"})
    hits = next(data["citations"] for kind, data in events(lookup) if kind == "citation")
    assert any(hit["doc_title"] == "滑动窗口控制" for hit in hits)

    again = import_zip(exported.content)
    assert again.status_code == 201
    assert again.json()["library"]["document"]["title"] == "计算机网络（导入）"
    assert api.get(DOCS + f"/{copy['id']}", headers=owner).status_code == 404


def test_curriculum_requires_connected_modules_and_acyclic_prerequisites(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from app.services.course_library import CurriculumPlan

    modules = ["数组", "查找", "排序"]
    concepts = ["下标", "随机访问", "扩容", "线性查找", "二分查找", "查找边界",
                "插入排序", "归并排序", "稳定性"]
    names = modules + concepts

    def edge(source, target, relation):
        return dict(source=source, target=target, relation_type=relation, description="课程关联说明")

    edges = [edge(modules[i // 3], name, "contains") for i, name in enumerate(concepts)]
    prerequisites = [edge("随机访问", "二分查找", "prerequisite"),
                     edge("排序", "二分查找", "prerequisite")]
    plan = dict(
        structured_summary="查找和排序的基础课程。",
        nodes=[dict(concept_name=n, summary=n, key_points=[n + "的定义"], importance=3) for n in names],
    )
    valid = CurriculumPlan(**plan, edges=edges + prerequisites)
    assert len(valid.cognitive_items) == len(names)
    with pytest.raises(ValidationError, match="模块间"):
        CurriculumPlan(**plan, edges=edges + prerequisites[:1])
    with pytest.raises(ValidationError, match="cycle"):
        CurriculumPlan(**plan, edges=edges + prerequisites + [edge("二分查找", "排序", "prerequisite")])
