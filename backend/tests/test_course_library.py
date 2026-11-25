from pydantic import ValidationError
import pytest
from time import monotonic, sleep

from test_local_accounts import ACCOUNTS, DOCS, auth
from test_smoke import fixture_server


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
