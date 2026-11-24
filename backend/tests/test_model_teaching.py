"""Run the whole HTTP path against a deterministic compatible provider."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from test_smoke import login, server


def test_model_processing_and_binding_isolation(server):
    api = server
    admin = login(api, "admin", "admin123")
    student = login(api, "student", "123456")
    root = "/api/v1/model-settings"
    preflight = api.options(
        root + "/bindings/report",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.is_success and "PUT" in preflight.headers["access-control-allow-methods"]
    assert api.get(root).status_code == 200
    assert api.get(root + "/availability").json() == dict.fromkeys(
        ("extract", "feynman", "encyclopedia", "report"), False,
    )
    missing = api.post("/api/v1/sessions", headers=student, json={"doc_id": 1})
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "model_required"
    missing = api.post(
        "/api/v1/documents/upload", headers=student, files={"file": ("file.md", b"lesson")},
    )
    assert missing.status_code == 409 and missing.json()["detail"]["purpose"] == "extract"
    assert api.get(root, headers={"Origin": "https://outside.example"}).status_code == 403
    assert api.get(root, headers={"Host": "outside.example"}).status_code == 403
    assert api.get(root + "/active", headers=student).status_code == 200
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            schema = body["response_format"]["json_schema"]["name"]
            material = body["messages"][-1]["content"]
            calls.append((schema, body["model"]))
            if schema == "Extraction":
                names = ["损失函数", "梯度", "学习率", "收敛", "泛化", "正则化"]
                value = {
                    "structured_summary": "模型解析的课程",
                    "nodes": [
                        {
                            "concept_name": name,
                            "summary": name + "的课程定义",
                            "importance": 3,
                            "key_points": ["课堂要点"],
                        }
                        for name in names
                    ],
                    "edges": [
                        {
                            "source": names[0],
                            "target": names[1],
                            "relation_type": "prerequisite",
                            "description": "定义基础",
                        }
                    ],
                }
            else:
                context = json.loads(material)
                node = context["nodes"][0]["id"]
                if schema == "Assessment":
                    value = {
                        "follow_up": "模型追问：能举一个反例吗？",
                        "node_id": node,
                        "delta": 11,
                        "feedback": "解释了适用前提",
                        "weakness_type": "omission",
                        "weakness_detail": "需要补充反例",
                    }
                elif schema == "Explanation":
                    assert context["history"] and context["excerpts"]
                    value = {
                        "source": "模型查证：结合第一个片段复述。",
                        "example": "误差为 1 和 2 时，均方误差为 2.5。",
                        "retell": "请解释为何要将误差平方。",
                        "node_id": node,
                        "source_indices": [0],
                    }
                else:
                    assert context["recent_dialogue"]
                    value = {
                        "coverage_score": 81,
                        "accuracy_score": 82,
                        "clarity_score": 83,
                        "logic_score": 84,
                        "overall_comment": "模型诊断结果",
                        "weakness_summary": ["复习边界"],
                        "review_plan": "完成反例练习",
                    }
            data = json.dumps(
                {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    Thread(target=provider.serve_forever, daemon=True).start()
    try:
        profile = {
            "name": "主方案",
            "model": "test-a",
            "mode": "api",
            "api_key": "sentinel-test-value",
            "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
        }
        first = api.post(root + "/profiles", json=profile).json()["id"]
        second = api.post(
            root + "/profiles", json={**profile, "model": "test-b"}
        ).json()["id"]
        assert (
            api.put(
                root + "/bindings/report", json={"profile_id": second}
            ).status_code
            == 204
        )
        exported = api.get(root + "/export")
        assert exported.json()["bindings"] == {"default": first, "report": second}
        assert api.post(root + "/import", json=exported.json()).json() == {
            "added": 0, "skipped": 2, "bindings_added": 0
        }
        assert api.post(root + "/import", json={**exported.json(), "bindings": {"default": "unknown"}}).status_code == 422
        assert b"sentinel-test-value" not in exported.content
        material = "损失函数度量预测误差。梯度指示局部上升方向。学习率决定步长并影响收敛。正则化有助于泛化。"
        doc = api.post(
            "/api/v1/documents/upload",
            headers=student,
            files={"file": ("lesson.md", material.encode())},
        )
        assert doc.status_code == 201, doc.text
        session = api.post(
            "/api/v1/sessions", headers=student, json={"doc_id": doc.json()["id"]}
        ).json()["id"]
        path = f"/api/v1/sessions/{session}"
        assert api.put(root + "/bindings/default", json={"profile_id": None}).status_code == 204
        assert api.put(root + "/bindings/report", json={"profile_id": None}).status_code == 204
        for window in ("feynman", "encyclopedia"):
            rejected = api.post(path + "/" + window + "/stream", headers=student, json={"content": "测试"})
            assert rejected.status_code == 409 and rejected.json()["detail"]["purpose"] == window
        assert api.post(path + "/report", headers=student).json()["detail"]["purpose"] == "report"
        assert api.get(path + "/messages", headers=student).json() == []
        assert api.put(root + "/bindings/default", json={"profile_id": first}).status_code == 204
        assert api.put(root + "/bindings/report", json={"profile_id": second}).status_code == 204
        for window in ["feynman", "encyclopedia"]:
            r = api.post(
                path + "/" + window + "/stream",
                headers=student,
                json={"content": "损失函数度量预测误差，细节是什么？"},
            )
            assert r.is_success and "event: done" in r.text, r.text
        messages = api.get(path + "/messages", headers=student).json()
        assert (
            messages[1]["content"].startswith("模型追问")
            and messages[1]["meta_json"]["updates"][0]["delta"] == 11
        )
        assert (
            "模型查证" in messages[3]["content"]
            and "**学科知识补充与举例**" in messages[3]["content"]
            and messages[3]["meta_json"]["citations"][0]["snippet"]
        )
        result = api.post(path + "/report", headers=student).json()
        assert result["overall_comment"] == "模型诊断结果" and result["logic_score"] == 84
        assert calls == [
            ("Extraction", "test-a"),
            ("Assessment", "test-a"),
            ("Explanation", "test-a"),
            ("Report", "test-b"),
        ]
        for endpoint in [root, "/api/v1/admin/export/json", "/api/v1/admin/export/db"]:
            assert b"sentinel-test-value" not in api.get(endpoint, headers=admin).content
    finally:
        provider.shutdown()
        provider.server_close()
