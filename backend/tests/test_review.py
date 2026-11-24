"""Exercise voluntary and scheduled reviews through the same persisted LangGraph turns."""

from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace

from test_langgraph import SOURCE, frames, teaching


def test_schedule_advances_caps_and_resets(teaching, monkeypatch):
    from app.services import cognitive

    when = datetime(2026, 1, 2, 3)
    monkeypatch.setattr(cognitive, "now", lambda: when)
    item = SimpleNamespace(review_stage=0, review_count=0)
    cases = [
        ("mastered", 1, timedelta(days=1)), ("mastered", 2, timedelta(days=3)),
        ("mastered", 3, timedelta(days=7)), ("mastered", 4, timedelta(days=15)),
        ("mastered", 5, timedelta(days=15)), ("mastered", 5, timedelta(days=15)),
        ("wrong", 0, timedelta(minutes=10)), ("mastered", 1, timedelta(days=1)),
        ("partial", 0, timedelta(hours=12)),
    ]
    for n, (outcome, stage, delay) in enumerate(cases, 1):
        cognitive.schedule_next_review(item, outcome)
        assert (item.review_stage, item.review_count) == (stage, n)
        assert item.last_reviewed_at == when and item.next_review_at == when + delay
        when += delay


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


def create(t, ids=None):
    body = dict(doc_id=1, mode="review")
    if ids is not None:
        body["target_item_ids"] = ids
    return t.api.post("/api/v1/sessions", json=body)


def test_due_selection_skip_and_simulation_are_document_scoped(teaching):
    t = teaching
    path, start = prepare(t)
    queue = t.api.get(path + "/due-reviews").json()
    assert [i["id"] for i in queue["all_reviewable_items"]] == [2, 1]
    assert [i["id"] for i in queue["due_items"]] == [2]
    assert create(t).json()["target_item_ids"] == [2]
    assert create(t, [4]).status_code == 422
    assert create(t, [3]).json()["target_item_ids"] == [3]
    for _ in range(2):
        queue = t.api.post(path + "/reviews/2/skip").json()
        assert not queue["due_items"]
        item = queue["all_reviewable_items"][0]
        assert item["skip_count"] == 1 and item["review_count"] == 0
        assert abs(datetime.fromisoformat(item["next_review_at"]) - start - timedelta(days=1)) < timedelta(seconds=5)
    assert create(t, []).json()["target_item_ids"] == [2, 1]
    assert t.api.post(path + "/reviews/4/skip").status_code == 404
    assert t.api.post(path + "/simulate-time-advance", json={"days": -1}).status_code == 422
    queue = t.api.post(path + "/simulate-time-advance", json={"days": 2}).json()
    assert [i["id"] for i in queue["due_items"]] == [1, 2]
    other = t.api.get("/api/v1/documents/2/due-reviews").json()["all_reviewable_items"][0]
    assert datetime.fromisoformat(other["next_review_at"]) == start + timedelta(hours=2)


def test_review_turns_follow_selection_correct_and_complete(teaching):
    from app.agents.teaching import rubric
    from app.db.models import CognitiveItem, Session

    t = teaching
    path, _ = prepare(t)
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
        for item_id, interval in [(2, timedelta(minutes=10)), (1, timedelta(days=1))]:
            row = db.get(CognitiveItem, item_id)
            assert row.review_count == 1 and row.next_review_at - row.last_reviewed_at == interval
        assert db.get(CognitiveItem, 3).review_count == 0
