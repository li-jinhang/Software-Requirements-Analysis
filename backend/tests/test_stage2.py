"""Phase-two offline regressions. No provider credentials or network needed."""

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module", autouse=True)
def offline(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
        patch.setenv("DATA_DIR", str(tmp_path_factory.mktemp("stage2-data")))
        patch.setenv("MOCK_LLM", "true")
        patch.setenv("EMBEDDING_PROVIDER", "local_lite")
        patch.setenv("OPENAI_API_KEY", "")
        yield


@pytest.mark.parametrize(
    ("term", "source"),
    [("梯度下降", "梯度下降"), ("L1正则化", "Ｌ１ 正则化"), ("TCP三次握手", "TCP 三次握手")],
)
def test_hybrid_recovers_exact_term_outside_vector_candidates(term, source):
    from app.services.rag_store import hybrid_rank

    rows = [
        ({"doc_title": "教材", "chunk_index": 0}, "本节讨论学习过程和使用方法。"),
        ({"doc_title": "教材", "chunk_index": 1}, source + "：定义、适用范围及典型例题。"),
    ]
    vector_only = {0: 0.98}
    hits = hybrid_rank(term, rows, vector_only, 1)
    assert hits[0]["chunk_index"] == 1 and 0 <= hits[0]["score"] <= 1


def test_sql_prerequisites_and_cross_session_history():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as DB
    from app.db.database import Base
    from app.db.models import KnowledgeNode as Node, KnowledgeEdge as Edge, Session, WeaknessLog
    from app.services.memory_service import get_memory_context

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with DB(engine) as db:
        for id, name in enumerate(["损失函数", "梯度", "梯度下降", "过拟合", "其他课程"], 1):
            db.add(
                Node(
                    id=id, doc_id=1 if id != 5 else 2, concept_name=name, summary=name, importance=3
                )
            )
        for a, b, k in [
            (1, 2, "prerequisite"),
            (2, 3, "prerequisite"),
            (3, 2, "prerequisite"),
            (4, 3, "contrast"),
        ]:
            db.add(
                Edge(
                    doc_id=1, source_node_id=a, target_node_id=b, relation_type=k, description="例"
                )
            )
        for id, doc in [(1, 1), (2, 1), (3, 2)]:
            db.add(Session(id=id, doc_id=doc, user_id=1, title="练习", persona_type="peer"))
        for sid, node in [(1, 1), (1, 4), (2, 3), (3, 5)]:
            db.add(
                WeaknessLog(
                    session_id=sid,
                    node_id=node,
                    weakness_type="omission",
                    detail="例",
                    is_resolved=sid == 1,
                )
            )
        db.commit()
        ctx = get_memory_context(db, 1, 2, concept=3)
        assert ctx["matched_node_ids"] == [3]
        assert {n["id"] for n in ctx["prerequisites"]} == {1, 2}
        assert {w["node_id"] for w in ctx["historical_weaknesses"]} == {1, 3}
        assert [w["node_id"] for w in ctx["weaknesses"]] == [3]
        assert 3 in get_memory_context(db, 1, query="为什么要用梯度下降？")["matched_node_ids"]
        assert not get_memory_context(db, 1, query="不存在的术语")["historical_weaknesses"]
        assert "prerequisites" not in get_memory_context(db, 1)


def test_bounded_hierarchical_synthesis_keeps_last_section(monkeypatch):
    from app.services import llm_client as llm

    sections = [f"章节{i}：" + ("教材原文" * 1800) for i in range(9)]
    source = "\n".join(sections)
    assert "".join(llm.segments(source)) == source
    calls = []

    def structured(_, schema, prompt, material):
        calls.append((schema, len(material)))
        assert len(material) < 8200
        if schema is llm.Digest:
            return llm.Digest(
                notes=" / ".join(f"章节{i}" for i in range(9) if f"章节{i}" in material)
            )
        assert all(f"章节{i}" in material for i in range(9))
        return llm.local_extract("概念：依据。", "教材")

    monkeypatch.setattr(llm, "structured", structured)
    graph = llm.live_extract(None, source, "课程")
    assert len(calls) > len(sections) + 1
    assert graph.structured_summary and all(n.key_points for n in graph.nodes)


def test_invalid_live_json_retries_twice_then_errors(caplog):
    from app.services import llm_client as llm

    calls = []

    def invalid(**params):
        calls.append(params)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"nodes":[]}'))]
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=invalid)))
    with pytest.raises(ValueError):
        llm.structured(client, llm.LIVE_EXTRACTION, "提取概念", "学习率：控制步长。")
    assert len(calls) == 3 and "准备重试" in caplog.text


@pytest.mark.parametrize("port", [5173, 3000, 4173])
def test_local_frontend_cors(port):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app, raise_server_exceptions=True) as client:
        for host in ("localhost", "127.0.0.1"):
            origin = f"http://{host}:{port}"
            r = client.options(
                "/api/v1/sessions",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
            assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin


def test_chroma_hybrid_definition_lookup(tmp_path, monkeypatch):
    import chromadb
    from app.services import rag_store as rag

    store = chromadb.PersistentClient(path=str(tmp_path / "index"))
    monkeypatch.setattr(rag, "client", lambda: store)
    monkeypatch.setattr(rag.settings, "embedding_provider", "local_lite")
    terms = ["梯度下降", "L1正则化", "TCP三次握手"]
    chunks = [
        "梯度下降：沿目标函数的负梯度方向迭代更新参数。"
        + "通过不同的学习率比较训练曲线，观察稳定性与收敛速度。" * 7,
        "L1 正则化：将参数绝对值之和加入损失函数。"
        + "在适当条件下它使部分系数变为零，特征选择可利用这种稀疏性。" * 7,
        "TCP 三次握手：客户端与服务器交换序号并确认通信能力。"
        + "两端通过确认号表明已经收到对端的初始序列号，再进入已建立状态。" * 7,
        "梯度的方向与下降方向",
        "L1 与 L2 的正则化比较",
        "TCP 握手的次数",
    ]
    rag.index(1, "术语定义", chunks)
    rag.index(2, "其他资料", terms)
    baseline = []
    for i, term in enumerate(terms):
        pure = rag.collection().query(
            query_embeddings=[rag.lite(term)], where={"doc_id": 1}, n_results=1
        )
        baseline.append(pure["metadatas"][0][0]["chunk_index"] == i)
        hit = rag.search(1, term, 1)[0]
        assert hit["chunk_index"] == i and hit["doc_title"] == "术语定义"
    assert sum(baseline) < len(terms)


def test_live_probe_over_local_compatible_http(tmp_path):
    """Exercise the actual SDK and the CLI without spending API credit."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import os
    import subprocess
    import sys
    import threading
    from app.services.llm_client import local_extract

    graph = local_extract("调度：分配处理器。死锁：循环等待。", "课程").model_dump_json()
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(self.path)
            if self.path.endswith("/chat/completions"):
                content = graph if "response_format" in body else "进程调度决定下一个运行的进程。"
                data = {
                    "id": "unit",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                }
            elif self.path.endswith("/embeddings"):
                data = {
                    "object": "list",
                    "model": "test",
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0]}
                        for i in range(len(body["input"]))
                    ],
                }
            else:
                self.send_error(404)
                return
            encoded = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/v1"
        env = {
            **os.environ,
            "DATA_DIR": str(tmp_path),
            "PYTHONIOENCODING": "utf-8",
            "OPENAI_API_KEY": "probe-only",
            "OPENAI_BASE_URL": url,
            "EMBEDDING_BASE_URL": url,
            "EMBEDDING_PROVIDER": "api",
            "NO_PROXY": "127.0.0.1",
            "EMBEDDING_API_KEY": "probe-only",
        }
        try:
            cli = Path(__file__).resolve().parents[1] / "scripts/check_llm_live.py"
            result = subprocess.run(
                [sys.executable, cli], env=env, capture_output=True, encoding="utf8", timeout=60
            )
        finally:
            server.shutdown()
            thread.join()
    assert result.returncode == 0, result.stderr
    assert seen.count("/v1/chat/completions") == 2 and seen.count("/v1/embeddings") >= 2
    assert "首条命中" in result.stdout and "probe-only" not in result.stdout + result.stderr


def test_file_read_retries_transient_io_only(monkeypatch):
    from app.services import doc_parser

    attempts = []

    def reader(path):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError("busy")
        return "可读正文"

    monkeypatch.setattr(doc_parser, "_read", reader)
    assert doc_parser.parse("sample") == "可读正文" and len(attempts) == 3


def test_sections_paragraphs_and_overlapping_long_blocks():
    from app.services.doc_parser import split_chunks

    long = "参数" * 550
    chunks = split_chunks("# 课名\n\n## 定义\n\n定义正文。\n\n第二段。\n### 演算\n" + long)
    assert chunks[:2] == ["## 定义\n定义正文。", "第二段。"]
    windows = chunks[2:]
    assert all(0 < len(chunk) <= 450 for chunk in windows)
    assert windows[0] + "".join(chunk[80:] for chunk in windows[1:]) == "### 演算\n" + long


def test_low_scoring_unrelated_tail_is_not_cited():
    from app.services.rag_store import hybrid_rank

    chunks = ["## 损失函数\n预测误差。", "## L1 正则化\n系数稀疏。"]
    rows = [({"doc_title": "示范课", "chunk_index": i}, s) for i, s in enumerate(chunks)]
    assert [h["chunk_index"] for h in hybrid_rank("损失函数如何举例？", rows, {0: .7, 1: .14}, 4)] == [0]
    assert not hybrid_rank("死锁", rows, {0: .1, 1: .14}, 4)


def test_existing_sqlite_migrates_without_losing_memory(tmp_path):
    import subprocess
    import os
    import sys

    # Boot in a fresh interpreter to exercise the same entry point as Uvicorn.
    backend = Path(__file__).resolve().parents[1]
    code = """
import sys
sys.path.insert(0, 'tests')
from fixture_inference import install
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.db.database import engine
with TestClient(app):
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE knowledge_nodes SET user_note='legacy note' WHERE id=1")
        conn.exec_driver_sql("UPDATE documents SET chunk_count=2 WHERE id=1")
        conn.exec_driver_sql("ALTER TABLE knowledge_nodes DROP COLUMN key_points")
    # SQLite represents the new FK as a table constraint, so DROP COLUMN
    # cannot recreate the old schema. Rebuild this disposable test table with
    # FK checks off on a separate connection, keeping every original record.
    import sqlite3
    with sqlite3.connect(engine.url.database) as legacy:
        legacy.executescript('''
BEGIN;
CREATE TABLE sessions_legacy (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    title VARCHAR NOT NULL, persona_type VARCHAR NOT NULL, status VARCHAR NOT NULL,
    created_at DATETIME NOT NULL, ended_at DATETIME
);
INSERT INTO sessions_legacy SELECT id,user_id,doc_id,title,persona_type,status,created_at,ended_at FROM sessions;
DROP TABLE sessions;
ALTER TABLE sessions_legacy RENAME TO sessions;
COMMIT;
''')
with TestClient(app) as api:
    token=api.post('/api/v1/auth/login',json={'username':'student','password':'123456'}).json()['access_token']
    headers={'Authorization':'Bearer '+token}
    old=api.get('/api/v1/documents/1/graph',headers=headers).json()['nodes'][0]
    assert old['key_points']==[] and old['user_note']=='legacy note'
    assert api.get('/api/v1/sessions',headers=headers).json()[0]['target_node_id'] is None
    assert api.get('/api/v1/documents/1',headers=headers).json()['chunk_count']==8
    install()
    response=api.post('/api/v1/documents/upload',headers=headers,files={'file':('notes.md',b'Alpha: first. Beta: second.')})
    assert response.status_code==201,response.text
    doc=response.json()
    assert doc['structured_summary']
    ctx=api.get(f"/api/v1/documents/{doc['id']}/memory",headers=headers).json()
    assert all(n['key_points'] for n in ctx['nodes'])
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend,
        env={
            **os.environ,
            "DATA_DIR": str(tmp_path),
            "SEED_DEMO": "true",
            "MOCK_LLM": "true",
            "EMBEDDING_PROVIDER": "local_lite",
        },
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
