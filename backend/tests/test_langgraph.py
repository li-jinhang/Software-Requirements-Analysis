"""U0003/U0004 acceptance tests: real StateGraph, SQL and HTTP/SSE adapter.

Provider responses are controlled in offline tests; no real API credit is used.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

SOURCE = "梯度下降沿负梯度方向更新参数。学习率过大可能震荡，足够小的步长有助于下降。"
ANSWER = "梯度下降沿负梯度方向更新参数，就像沿山坡向下走。如果学习率过大，可能越过谷底而震荡。"


@pytest.fixture
def teaching(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "unused-global-data"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.api.v1 import sessions
    from app.core.security import current_user
    from app.db.database import Base, get_db
    from app.db.models import User, Document, KnowledgeNode, KnowledgeEdge, Session
    from app.agents.teaching import model
    from app.services import memory_service

    engine = create_engine(f"sqlite:///{(tmp_path / 'teaching.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 2})

    @event.listens_for(engine, "connect")
    def pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(id=1, username="test", nickname="验收", password_hash="unused"))
        db.flush()
        db.add_all([
            Document(id=i, user_id=1, title="课程", subject="机器学习", file_type="md",
                     file_path="unused", parse_status="completed") for i in (1, 2)
        ])
        db.flush()
        db.add_all([
            KnowledgeNode(id=1, doc_id=1, concept_name="梯度下降", summary=SOURCE,
                          key_points=["负梯度方向", "学习率边界"], importance=5),
            KnowledgeNode(id=2, doc_id=1, concept_name="梯度", summary="局部上升最快的方向", importance=4),
            KnowledgeNode(id=3, doc_id=2, concept_name="其他课程", summary="其他资料", importance=3),
        ])
        db.flush()
        db.add(KnowledgeEdge(doc_id=1, source_node_id=2, target_node_id=1,
                             relation_type="prerequisite", description="梯度是前置概念"))
        db.add(Session(id=1, user_id=1, doc_id=1, title="验收", persona_type="peer", target_node_id=1))

    def dependency():
        with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(sessions.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = dependency
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(sessions, "SessionLocal", factory)
    monkeypatch.setattr(memory_service, "search_rag", lambda *a, **k: [
        {"doc_title": "课程", "chunk_index": 0, "snippet": SOURCE, "score": 0.9}
    ])
    monkeypatch.setattr(model.settings, "mock_llm", True)
    with TestClient(app) as api:
        yield SimpleNamespace(api=api, factory=factory, model=model, memory=memory_service,
                              sessions=sessions, monkeypatch=monkeypatch)
    engine.dispose()


def frames(response):
    assert response.status_code == 200, response.text
    return [(block.splitlines()[0][7:], json.loads(block.splitlines()[1][6:]))
            for block in response.text.strip().split("\n\n")]


def history(t):
    return t.api.get("/api/v1/sessions/1/messages").json()


def turn(t, text=ANSWER):
    return frames(t.api.post("/api/v1/sessions/1/feynman/stream", json={"content": text}))


def opening(t):
    return frames(t.api.post("/api/v1/sessions/1/opening/stream"))


def good_model(t, **changes):
    calls = []

    def assess(state, schema, correction=False):
        calls.append((state, correction))
        return json.dumps(dict({
            "node_id": 1, "student_evidence": state["content"], "source_evidence": SOURCE,
            "accuracy": 95, "clarity": 90, "problems": [], "on_topic": True,
            "followup_focus": "如果学习率过大，能否保证损失下降？",
        }, **changes), ensure_ascii=False)

    t.monkeypatch.setattr(t.model, "offline", lambda: False)
    t.monkeypatch.setattr(t.model, "assess", assess)
    t.monkeypatch.setattr(t.model, "followup", lambda s, p: iter(["如果条件改变，", "结果还成立吗？"]))
    return calls


def node(t):
    from app.db.models import KnowledgeNode, public
    with t.factory() as db:
        return public(db.get(KnowledgeNode, 1))


@pytest.mark.parametrize("persona,phrase", [("child", "生活"), ("peer", "适用条件"), ("examiner", "关键前提")])
def test_opening_personas_replay_and_no_scoring(teaching, persona, phrase):
    t = teaching
    assert t.api.patch("/api/v1/sessions/1/persona", json={"persona_type": persona}).status_code == 200
    result = opening(t)
    assert result[-1][0] == "done" and phrase in result[0][1]["delta"]
    assert all(kind != "memory_update" for kind, _ in result)
    count = len(history(t))
    assert opening(t)[-1] == result[-1] and len(history(t)) == count
    assert node(t)["mastery_score"] == 0


def test_target_node_validation(teaching):
    t = teaching
    assert t.api.post("/api/v1/sessions", json={"doc_id": 1, "node_id": 3}).status_code == 422
    response = t.api.post("/api/v1/sessions", json={"doc_id": 1, "node_id": 2})
    assert response.status_code == 201 and response.json()["target_node_id"] == 2


def test_two_deep_answers_persist_across_requests_and_role_change(teaching):
    t = teaching
    good_model(t)
    opening(t)
    first = turn(t)
    assert 0 < node(t)["mastery_score"] < 80 and node(t)["status"] != "mastered"
    assert history(t)[-1]["meta_json"]["assessment"]["streak"] == 1
    old_history = history(t)
    t.api.patch("/api/v1/sessions/1/persona", json={"persona_type": "examiner"})
    assert history(t)[:-1] == old_history
    assert history(t)[-1]["sender_role"] == "system"
    second = turn(t)
    assert node(t)["status"] == "mastered" and 80 <= node(t)["mastery_score"] <= 100
    last = history(t)[-1]
    assert last["meta_json"]["assessment"]["streak"] == 2
    assert last["meta_json"]["graph_nodes"] == [
        "load_context", "classify", "retrieve", "assess", "plan_memory", "respond", "persist_memory", "complete"
    ]
    assert last["content"] == "".join(data["delta"] for kind, data in second if kind == "token")
    assert all(k in {"token", "citation", "memory_update", "done"} for k, _ in first + second)


@pytest.mark.parametrize("problem", ["misconception", "omission", "jargon"])
def test_three_weakness_types(teaching, problem):
    t = teaching
    good_model(t, problems=[{"weakness_type": problem, "detail": "尚未说明边界条件"}])
    assert turn(t)[-1][0] == "done"
    from sqlalchemy import select
    from app.db.models import WeaknessLog
    with t.factory() as db:
        assert db.scalar(select(WeaknessLog)).weakness_type == problem
    assert 0 <= node(t)["mastery_score"] <= 100


@pytest.mark.parametrize("text,kind", [("我不会，卡壳了", "stuck"), ("讲个笑话吧", "off_topic")])
def test_special_branches_do_not_score(teaching, text, kind):
    t = teaching
    result = turn(t, text)
    assert all(k != "memory_update" for k, _ in result)
    last = history(t)[-1]
    assert last["meta_json"]["input_kind"] == kind
    assert "retrieve" not in last["meta_json"]["graph_nodes"]
    assert "查证" in last["content"] if kind == "stuck" else "回到" in last["content"]
    assert node(t)["mastery_score"] == 0


def test_subject_negation_is_not_mistaken_for_being_stuck(teaching):
    t = teaching
    calls = good_model(t)
    turn(t, "梯度下降在足够小的步长下通常不会使当前损失增大。")
    assert len(calls) == 1 and history(t)[-1]["meta_json"]["input_kind"] == "explain"


@pytest.mark.parametrize("changes,kind", [({"needs_help": True}, "stuck"), ({"on_topic": False}, "off_topic")])
def test_model_can_route_semantic_help_and_off_topic_input(teaching, changes, kind):
    t = teaching
    good_model(t, **changes)
    result = turn(t, "我想先把学习率的适用范围搞清楚，再继续说明这个过程。")
    assert history(t)[-1]["meta_json"]["input_kind"] == kind
    assert not any(k == "memory_update" for k, _ in result)


def test_lookup_breaks_streak_and_is_available_to_next_turn(teaching):
    t = teaching
    calls = good_model(t)
    opening(t)
    turn(t)
    assert history(t)[-1]["meta_json"]["assessment"]["streak"] == 1
    frames(t.api.post("/api/v1/sessions/1/encyclopedia/stream", json={"content": "梯度下降是什么？"}))
    turn(t)
    assert history(t)[-1]["meta_json"]["assessment"]["streak"] == 1
    assert node(t)["lookup_count"] == 1 and node(t)["status"] != "mastered"
    assert any(w["weakness_type"] == "need_lookup" for w in calls[-1][0]["memory"]["weaknesses"])
    turn(t)
    assert node(t)["status"] == "mastered"
    from sqlalchemy import select
    from app.db.models import WeaknessLog
    with t.factory() as db:
        assert all(w.is_resolved for w in db.scalars(select(WeaknessLog)))


def test_one_json_repair_then_no_numeric_writes(teaching):
    t = teaching
    good_model(t)
    calls = []

    def invalid(*a, **k):
        calls.append(1)
        return '{"node_id":1,"accuracy":999}'

    t.monkeypatch.setattr(t.model, "assess", invalid)
    result = turn(t)
    assert len(calls) == 2 and result[-1][0] == "done"
    assert all(k != "memory_update" for k, _ in result)
    assert node(t)["mastery_score"] == 0
    meta = history(t)[-1]["meta_json"]
    assert meta["assessment_error"] and meta["graph_nodes"].count("repair") == 1


@pytest.mark.parametrize("changes", [{"source_evidence": "编造的原文"}, {"student_evidence": "未说过的话"}, {"node_id": 3}])
def test_invented_evidence_or_foreign_node_is_rejected(teaching, changes):
    t = teaching
    calls = good_model(t, **changes)
    result = turn(t)
    assert len(calls) == 2 and not any(k == "memory_update" for k, _ in result)
    assert node(t)["mastery_score"] == 0


def test_no_rag_evidence_skips_assessment(teaching):
    t = teaching
    calls = good_model(t)
    t.monkeypatch.setattr(t.memory, "search_rag", lambda *a, **k: [])
    assert turn(t)[-1][0] == "done" and not calls
    assert node(t)["mastery_score"] == 0


def test_memory_failure_rolls_back_savepoint_but_keeps_question(teaching):
    t = teaching
    good_model(t)
    original = t.memory.update_mastery

    def broken(*a, **k):
        original(*a, **k)
        raise RuntimeError("simulated memory failure after SQL write")

    t.monkeypatch.setattr(t.memory, "update_mastery", broken)
    result = turn(t)
    assert result[-1][0] == "done" and not any(k == "memory_update" for k, _ in result)
    assert node(t)["mastery_score"] == 0
    last = history(t)[-1]
    assert "结果还成立吗" in last["content"] and "未同步" in last["content"]
    assert last["meta_json"]["memory_synced"] is False


def test_final_commit_failure_never_announces_or_commits_memory(teaching):
    t = teaching
    good_model(t)
    t.monkeypatch.setattr(t.sessions, "lock_active", lambda *a: (_ for _ in ()).throw(RuntimeError("commit blocked")))
    result = turn(t)
    assert result[-1][0] == "error" and not any(k in {"memory_update", "done"} for k, _ in result)
    assert node(t)["mastery_score"] == 0 and history(t) == []


def test_slow_model_does_not_hold_sqlite_write_lock(teaching):
    t = teaching
    good_model(t)
    original = t.model.assess
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    t.monkeypatch.setattr(t.model, "assess", slow)
    from app.db.models import KnowledgeNode
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(turn, t)
        try:
            assert entered.wait(5)
            with t.factory.begin() as db:
                db.get(KnowledgeNode, 2).user_note = "模型等待期间仍可保存另一节点笔记"
        finally:
            release.set()
        assert future.result(timeout=10)[-1][0] == "done"


def test_provider_failure_is_visible_and_does_not_fall_back_to_scores(teaching):
    t = teaching
    good_model(t)
    from app.agents.teaching.model import TeachingModelError
    t.monkeypatch.setattr(t.model, "assess", lambda *a: (_ for _ in ()).throw(TeachingModelError("模型超时，请重试")))
    result = turn(t)
    assert result[-1][0] == "error" and "超时" in result[-1][1]["detail"]
    assert node(t)["mastery_score"] == 0 and history(t) == []


def test_actual_sdk_json_repair_and_token_stream_over_local_http(teaching):
    """Exercise the real OpenAI SDK, graph and SSE without a paid provider."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import httpx
    from openai import OpenAI
    from pydantic import SecretStr

    t = teaching
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for text in ["如果学习率过大，", "还一定收敛吗？"]:
                    chunk = {"id": "test", "object": "chat.completion.chunk", "created": 1,
                             "model": "test", "choices": [{"index": 0, "delta": {"content": text}}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                payload = json.loads(body["messages"][-1]["content"])
                content = "{}" if len(calls) == 1 else json.dumps({
                    "node_id": payload["node"]["id"], "student_evidence": payload["content"],
                    "source_evidence": SOURCE, "accuracy": 95, "clarity": 90,
                    "problems": [], "followup_focus": "学习率过大时的收敛性",
                }, ensure_ascii=False)
                data = json.dumps({"id": "test", "object": "chat.completion", "created": 1,
                                   "model": "test", "choices": [{"index": 0,
                                   "message": {"role": "assistant", "content": content},
                                   "finish_reason": "stop"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        t.monkeypatch.setattr(t.model.settings, "mock_llm", False)
        t.monkeypatch.setattr(t.model.settings, "openai_api_key", SecretStr("local-test-only"))
        t.monkeypatch.setattr(t.model, "client", lambda: OpenAI(
            api_key="local-test-only", base_url=f"http://127.0.0.1:{server.server_port}/v1",
            max_retries=0, http_client=httpx.Client(trust_env=False),
        ))
        try:
            opening(t)
            result = turn(t)
        finally:
            server.shutdown()
            thread.join()
    assert len(calls) == 3 and calls[-1]["stream"] is True
    assert result[-1][0] == "done" and node(t)["mastery_score"] > 0
    assert "还一定收敛吗？" in history(t)[-1]["content"]
    assert history(t)[-1]["meta_json"]["graph_nodes"].count("repair") == 1


def test_transport_uses_exactly_two_exponential_retries(teaching):
    from contextlib import nullcontext
    import httpx
    from openai import RateLimitError
    from app.agents.teaching.model import TeachingModelError

    t = teaching
    calls, sleeps = [], []

    def limited(**kwargs):
        calls.append(kwargs)
        raise RateLimitError("limited", response=httpx.Response(
            429, request=httpx.Request("POST", "http://local.test/v1/chat/completions")
        ), body={})

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=limited)))
    t.monkeypatch.setattr(t.model, "client", lambda: nullcontext(sdk))
    t.monkeypatch.setattr(t.model.time, "sleep", sleeps.append)
    with pytest.raises(TeachingModelError, match="429"):
        t.model.request([{"role": "user", "content": "test"}])
    assert len(calls) == 3 and sleeps == [0.25, 0.5]


def test_low_quality_answer_resets_consecutive_progress(teaching):
    t = teaching
    good_model(t)
    opening(t)
    turn(t)
    good_model(t, problems=[{"weakness_type": "omission", "detail": "遗漏适用条件"}])
    turn(t)
    good_model(t)
    turn(t)
    assert history(t)[-1]["meta_json"]["assessment"]["streak"] == 1
    assert node(t)["status"] != "mastered"


def test_history_waits_for_an_accepted_turn_before_retry(teaching):
    """A reconnect must observe the committed turn before allowing a resend."""
    from concurrent.futures import TimeoutError
    t = teaching
    good_model(t)
    original = t.model.assess
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    t.monkeypatch.setattr(t.model, "assess", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = pool.submit(turn, t)
        assert entered.wait(5)
        recovering = pool.submit(history, t)
        try:
            with pytest.raises(TimeoutError):
                recovering.result(timeout=0.5)
        finally:
            release.set()
        assert accepted.result(timeout=10)[-1][0] == "done"
        messages = recovering.result(timeout=10)
        assert len(messages) == 2 and messages[0]["content"] == ANSWER
