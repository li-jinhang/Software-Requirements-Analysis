"""Keep active recall scheduling independent from LangGraph assessment."""

from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

from test_langgraph import SOURCE, frames, teaching


def test_schedule_advances_caps_and_resets():
    from app.services.review import next_interval

    stage = 0
    cases = [
        ("remembered", 1, timedelta(days=1)), ("remembered", 2, timedelta(days=3)),
        ("remembered", 3, timedelta(days=7)), ("remembered", 4, timedelta(days=15)),
        ("remembered", 5, timedelta(days=15)), ("remembered", 5, timedelta(days=15)),
        ("again", 0, timedelta(minutes=10)), ("remembered", 1, timedelta(days=1)),
    ]
    for outcome, expected, delay in cases:
        stage, interval = next_interval(stage, outcome)
        assert (stage, interval) == (expected, delay)


def test_old_db_migration_is_repeatable(teaching):
    from app.db.review_migration import migrate_reviews

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO sessions VALUES (1)")
        db.execute("CREATE TABLE cognitive_items (status TEXT, updated_at DATETIME)")
        db.executemany("INSERT INTO cognitive_items VALUES (?, '2026-01-01 00:00:00')",
                       [(s,) for s in ("untested", "wrong", "partial", "mastered")])
        migrate_reviews(db.execute)
        db.execute("UPDATE cognitive_items SET skip_count=1 WHERE status='partial'")
        migrate_reviews(db.execute)
        assert db.execute("SELECT mode, target_item_ids, review_completed FROM sessions").fetchone() == ("teach", "[]", 0)
        assert db.execute("SELECT review_stage, next_review_at, skip_count FROM cognitive_items ORDER BY rowid").fetchall() == [
            (0, None, 0), (0, "2026-01-01 00:10:00", 0),
            (0, "2026-01-01 12:00:00", 1), (1, "2026-01-02 00:00:00", 0),
        ]


def prepare(t):
    from app.api.v1 import documents
    from app.db.models import CognitiveItem, now

    t.api.app.include_router(documents.router, prefix="/api/v1")
    start = now().replace(microsecond=0)
    with t.factory.begin() as db:
        for i, doc, status, delay in [(1, 1, "mastered", 12), (2, 1, "wrong", -1),
                                      (3, 1, "untested", None), (4, 2, "partial", 2)]:
            db.add(CognitiveItem(id=i, doc_id=doc, node_id=1 if doc == 1 else 3,
                                 item_order=i, section_title="梯度下降", statement=SOURCE,
                                 key_criteria="能解释方向", status=status,
                                 issue_note="没有解释方向" if i == 2 else "",
                                 next_review_at=start + timedelta(hours=delay) if delay is not None else None))
    return "/api/v1/documents/1", start


def create(t, ids=None, mode="assessment"):
    body = dict(doc_id=1, mode=mode)
    if ids is not None:
        body["target_item_ids"] = ids
    return t.api.post("/api/v1/sessions", json=body)


def test_review_eligibility_and_schedule_are_independent_of_assessment(teaching):
    from app.db.models import CognitiveItem, KnowledgeNode, Session

    t = teaching
    path, start = prepare(t)
    assert t.api.get(path + "/due-reviews").json()["all_reviewable_items"] == []
    with t.factory.begin() as db:
        db.get(KnowledgeNode, 1).mastery_score = 80
        db.get(KnowledgeNode, 3).mastery_score = 100
        db.get(CognitiveItem, 4).status = "mastered"
        db.add(CognitiveItem(id=5, doc_id=1, node_id=1, status="mastered", item_order=5,
                             section_title="梯度下降", statement=SOURCE, key_criteria="能解释原因",
                             updated_at=start - timedelta(days=2)))
    queue = t.api.get(path + "/due-reviews").json()
    assert [i["id"] for i in queue["all_reviewable_items"]] == [5, 1]
    assert [i["id"] for i in queue["due_items"]] == [5]
    assert queue["due_items"][0]["mastery_score"] == 80
    assert datetime.fromisoformat(queue["due_items"][0]["next_review_at"]) == start - timedelta(days=1)
    assert create(t).status_code == 422
    assert create(t, mode="review").status_code == 422
    assert create(t, [4]).status_code == 422
    old_client = create(t, [3], mode="review").json()
    assert old_client["target_item_ids"] == [3] and old_client["mode"] == "assessment"
    for _ in range(2):
        queue = t.api.post(path + "/reviews/5/skip").json()
        assert not queue["due_items"]
        item = next(i for i in queue["all_reviewable_items"] if i["id"] == 5)
        assert item["skip_count"] == 1 and item["review_count"] == 0
        assert abs(datetime.fromisoformat(item["next_review_at"]) - start - timedelta(days=1)) < timedelta(seconds=5)
    assert t.api.post(path + "/reviews/4/skip").status_code == 404
    for item_id in (2, 3):
        assert t.api.post(path + f"/reviews/{item_id}/result", json={"outcome": "remembered", "review_count": 0}).status_code == 409
    with t.factory() as db:
        session_count = db.query(Session).count()
    for _ in range(2):
        result = t.api.post(path + "/reviews/5/result", json={"outcome": "remembered", "review_count": 0})
        assert result.is_success, result.text
        item = next(i for i in result.json()["all_reviewable_items"] if i["id"] == 5)
        assert (item["review_count"], item["review_stage"]) == (1, 1)
    result = t.api.post(path + "/reviews/5/result", json={"outcome": "again", "review_count": 1}).json()
    item = next(i for i in result["all_reviewable_items"] if i["id"] == 5)
    assert (item["review_count"], item["review_stage"], item["status"]) == (2, 0, "mastered")
    assert datetime.fromisoformat(item["next_review_at"]) - datetime.fromisoformat(item["last_reviewed_at"]) == timedelta(minutes=10)
    with t.factory() as db:
        assert db.query(Session).count() == session_count
        assert db.get(KnowledgeNode, 1).mastery_score == 80
        assert db.get(CognitiveItem, 5).user_evidence == ""
    assert t.api.post(path + "/simulate-time-advance", json={"days": -1}).status_code == 422
    queue = t.api.post(path + "/simulate-time-advance", json={"days": 2}).json()
    assert {i["id"] for i in queue["due_items"]} == {1, 5}
    other = t.api.get("/api/v1/documents/2/due-reviews").json()["all_reviewable_items"][0]
    assert datetime.fromisoformat(other["next_review_at"]) == start + timedelta(hours=2)


def test_targeted_assessment_follows_selection_and_does_not_record_review(teaching):
    from app.agents.teaching import rubric
    from app.db.models import CognitiveItem, Session

    t = teaching
    path, start = prepare(t)
    s = create(t, [2, 1, 2]).json()
    assert s["target_item_ids"] == [2, 1] and not s["review_completed"]
    sid = s["id"]
    base = f"/api/v1/sessions/{sid}"
    requested = []

    def question(state, persona):
        requested.append((state["next_item"]["id"], state["next_item"]["issue_note"]))
        yield "换一个场景，你会怎么解释？"

    def assess(state, schema, correction=False):
        item = state["current_item_id"]
        assert [i["id"] for i in state["cognitive_items"]] == [item]
        claim = state["content"]
        issues = [dict(issue_type="wrong", detail="应沿负梯度更新", user_claim=claim)] if item == 2 else []
        return json.dumps(dict(node_id=1, student_evidence=claim, source_evidence=SOURCE,
            accuracy=20 if issues else 90, clarity=80, followup_focus="梯度方向", problems=[],
            item_marks=[dict(item_id=item, status="wrong" if issues else "mastered",
                             user_evidence=claim, issues=issues)]))

    t.monkeypatch.setattr(t.model, "followup", question)
    t.monkeypatch.setattr(t.model, "assess", assess)
    t.monkeypatch.setattr(rubric, "require_profile", lambda purpose: {})
    t.monkeypatch.setattr(rubric, "ModelConnection", lambda _: SimpleNamespace(structured=
        lambda schema, *_: schema(explanations=[dict(issue_number=1, accurate_explanation=SOURCE, source_indices=[0])])))
    frames(t.api.post(base + "/opening/stream"))
    assert requested == [(2, "没有解释方向")]
    claim = "梯度下降沿正梯度更新参数。"
    bad = dict(frames(t.api.post(base + "/feynman/stream", json={"content": claim})))
    assert bad["encyclopedia_push"]["issue_type"] == "wrong"
    assert bad["cognitive_update"]["current_item_id"] == 1 and not bad["cognitive_update"]["review_completed"]
    assert requested[-1][0] == 1
    endpoint = path + f"/cognitive-doc?session_id={sid}"
    assert t.api.get(endpoint).json()["current_item_id"] == 1
    good = dict(frames(t.api.post(base + "/feynman/stream", json={"content": SOURCE})))
    assert good["cognitive_update"]["review_completed"] and good["cognitive_update"]["current_item_id"] is None
    assert t.api.get(endpoint).json()["current_item_id"] is None
    assert t.api.post(base + "/feynman/stream", json={"content": SOURCE}).status_code == 409
    with t.factory() as db:
        assert db.get(Session, sid).review_completed
        for item_id, delay in [(2, -1), (1, 12)]:
            row = db.get(CognitiveItem, item_id)
            assert row.review_count == 0 and row.last_reviewed_at is None
            assert row.next_review_at == start + timedelta(hours=delay)
        assert db.get(CognitiveItem, 3).review_count == 0


def test_due_reviews_traceability_and_ai_challenge_verify(teaching):
    from app.db.models import CognitiveItem, KnowledgeNode
    from app.services import rag_store, review

    t = teaching
    path, start = prepare(t)
    with t.factory.begin() as db:
        node = db.get(KnowledgeNode, 1)
        node.mastery_score = 92
        node.user_note = "注意负号决定下坡方向"
        item = db.get(CognitiveItem, 1)
        item.user_evidence = "沿负梯度方向逐步迭代更新参数。"
        item.issue_note = "曾忽略学习率过大的震荡边界"
        item.next_review_at = start - timedelta(hours=2)

    rag_store.client.cache_clear()
    rag_store.index(
        1,
        "梯度下降课程",
        [
            "梯度下降沿损失函数负梯度方向更新参数。学习率过大可能导致参数在极小值附近震荡。",
            "梯度是偏导数向量，指向局部函数值增长最快的方向。",
            "无关背景：神经网络的权重初始化方法有很多种。",
        ],
        local_only=True,
    )

    queue = t.api.get(path + "/due-reviews").json()
    assert len(queue["due_items"]) == 1
    card = queue["due_items"][0]
    assert card["section_title"] == "梯度下降"
    assert card["concept_name"] == "梯度下降"
    assert card["key_criteria"] == "能解释方向"
    assert card["user_evidence"] == "沿负梯度方向逐步迭代更新参数。"
    assert card["issue_note"] == "曾忽略学习率过大的震荡边界"
    assert card["user_note"] == "注意负号决定下坡方向"
    assert card["review_stage"] == 0
    assert card["next_remembered_days"] == 1
    assert 1 <= len(card["source_snippets"]) <= 2
    assert any("负梯度" in s["snippet"] for s in card["source_snippets"])

    # Without a configured model, both endpoints degrade gracefully without raising.
    t.monkeypatch.setattr(review, "resolve", lambda purpose: None)
    ch_fallback = t.api.post(path + "/reviews/1/ai-challenge")
    assert ch_fallback.status_code == 200
    assert ch_fallback.json()["available"] is False
    assert "核验判据" in ch_fallback.json()["hint"]
    assert "尚未配置" in ch_fallback.json()["notice"]

    vr_fallback = t.api.post(path + "/reviews/1/ai-verify", json={"answer": "沿负梯度下降"})
    assert vr_fallback.status_code == 200
    assert vr_fallback.json()["available"] is False
    assert vr_fallback.json()["score_hint"] == "again"

    # With a configured fixture model, structured challenge & verification respect schema and do not mutate schedule.
    t.monkeypatch.setattr(
        review,
        "resolve",
        lambda purpose: {"mode": "api", "model": "fixture", "base_url": "http://127.0.0.1:1/v1", "api_key": "k"}
        if purpose == "feynman"
        else None,
    )

    def fake_structured(schema, instructions, material):
        payload = json.loads(material)
        if "learner_answer" not in payload:
            assert payload["issue_note"] == "曾忽略学习率过大的震荡边界"
            return schema(
                challenge="假设损失曲面前方突然变陡，此时该如何解释更新方向与步长风险？",
                hint="回顾负号含义与过大步长的震荡条件",
            )
        return schema(
            verdict="partial",
            score_hint="again",
            feedback="正确说明了负梯度方向，但未指出步长过大会导致震荡。",
            missing_points=["学习率过大会引起震荡或发散"],
            matched_criteria=["能解释方向"],
        )

    t.monkeypatch.setattr(review, "ModelConnection", lambda profile: SimpleNamespace(structured=fake_structured))
    ch_live = t.api.post(path + "/reviews/1/ai-challenge").json()
    assert ch_live["available"] is True
    assert "更新方向" in ch_live["challenge"]

    vr_live = t.api.post(path + "/reviews/1/ai-verify", json={"answer": "沿负梯度更新"}).json()
    assert vr_live["available"] is True
    assert vr_live["verdict"] == "partial"
    assert vr_live["score_hint"] == "again"
    assert vr_live["matched_criteria"] == ["能解释方向"]
    assert vr_live["missing_points"] == ["学习率过大会引起震荡或发散"]

    # AI checks never alter Ebbinghaus review count, stage, or schedule.
    with t.factory() as db:
        row = db.get(CognitiveItem, 1)
        assert row.review_count == 0
        assert row.review_stage == 0
        assert row.next_review_at == start - timedelta(hours=2)

    # Ineligible card & blank answer remain guarded.
    assert t.api.post(path + "/reviews/3/ai-challenge").status_code == 409
    assert t.api.post(path + "/reviews/1/ai-verify", json={"answer": "   "}).status_code == 422
    assert t.api.post("/api/v1/documents/2/reviews/1/ai-challenge").status_code == 404
