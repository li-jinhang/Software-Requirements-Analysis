"""Document rubric acceptance with provider injection only at the model boundary."""

import json
from types import SimpleNamespace
import pytest
from test_langgraph import teaching, turn, opening, history, SOURCE


def topic_plan():
    names = ["顺序", "中点", "边界", "终止"]
    return dict(
        structured_summary="在有序区间内逐步缩小查找范围。",
        nodes=[
            dict(concept_name=n, summary=n + "的定义", key_points=[n + "的要点"], importance=3)
            for n in names
        ],
        edges=[
            dict(
                source=names[0],
                target=names[1],
                relation_type="prerequisite",
                description="有序是前提",
            )
        ],
        cognitive_items=[
            dict(concept_name=n, statement=n + "的" + point, key_criteria="能说明" + n + point)
            for n in names
            for point in ("定义", "应用")
        ],
    )


def test_topic_requires_bound_model_then_stores_source_and_rubric(teaching, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import documents
    from app.services import cognitive
    from app.services.model_profiles import ModelRequired

    t = teaching
    from app.main import model_required

    app = FastAPI(exception_handlers={ModelRequired: model_required})
    app.include_router(documents.router, prefix="/api/v1")
    app.dependency_overrides.update(t.api.app.dependency_overrides)
    t.api = TestClient(app)

    def missing(purpose):
        raise ModelRequired(purpose)

    t.monkeypatch.setattr(documents, "require_profile", missing)
    path = "/api/v1/documents/generate-from-topic"
    response = t.api.post(path, json={"topic": "二分查找"})
    assert response.status_code == 409 and response.json()["detail"]["purpose"] == "extract"
    profile = object()

    def resolve(purpose):
        assert purpose == "extract"
        return profile

    def connection(selected):
        assert selected is profile
        return SimpleNamespace(structured=lambda schema, *args: schema.model_validate(topic_plan()))

    t.monkeypatch.setattr(documents, "require_profile", resolve)
    t.monkeypatch.setattr(cognitive, "ModelConnection", connection)
    t.monkeypatch.setattr(cognitive.settings, "data_dir", tmp_path)
    (tmp_path / "uploads").mkdir()
    indexed = {}
    t.monkeypatch.setattr(
        cognitive.rag_store, "index", lambda i, title, chunks: indexed.update({i: chunks})
    )
    response = t.api.post(
        path, json={"topic": "二分查找", "subject": "数据结构", "detail_level": "基础"}
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["chunk_count"] == len(indexed[doc["id"]]) > 1
    base = f"/api/v1/documents/{doc['id']}"
    rubric = t.api.get(base + "/cognitive-doc").json()
    assert rubric["total"] == 8 and rubric["mastery_score"] == 0 and rubric["mastered_count"] == 0
    assert len(rubric["sections"]) == 4
    assert t.api.get(base + "/cognitive-doc?session_id=1").status_code == 404
    assert all(
        i["statement"] in "\n".join(indexed[doc["id"]])
        for s in rubric["sections"]
        for i in s["items"]
    )
    assert t.api.get(base + "/memory/export?format=json").json()["Agent 认知文档"] == rubric


@pytest.mark.parametrize(
    "kind,status,value",
    [("wrong", "wrong", 15), ("inaccurate", "partial", 50), ("vague", "partial", 50)],
)
def test_correction_is_committed_before_followup_and_advances_on_repair(
    teaching, kind, status, value
):
    from app.agents.teaching import rubric
    from app.services import cognitive
    from app.db.models import CognitiveItem, KnowledgeNode

    t = teaching
    with t.factory.begin() as db:
        for i in (1, 2):
            db.add(
                CognitiveItem(
                    id=i,
                    doc_id=1,
                    node_id=1,
                    item_order=i,
                    section_title="梯度下降",
                    statement=f"第{i}项：沿负梯度更新",
                    key_criteria="解释方向",
                )
            )
    assert "第1项" in "".join(d["delta"] for k, d in opening(t) if k == "token")
    claim = "梯度下降沿正梯度更新。"

    def assess(state, *args):
        evidence = state["content"]
        bad = evidence == claim
        return json.dumps(
            dict(
                node_id=1,
                student_evidence=evidence,
                source_evidence=SOURCE,
                accuracy=35 if bad else 90,
                clarity=80,
                followup_focus="解释更新方向",
                problems=[],
                item_marks=[
                    dict(
                        item_id=1,
                        status=status if bad else "mastered",
                        user_evidence=evidence,
                        issues=(
                            [
                                dict(
                                    issue_type=kind,
                                    detail="应使用负梯度方向。",
                                    user_claim=evidence,
                                )
                            ]
                            if bad
                            else []
                        ),
                    )
                ],
            )
        )

    def correct(schema, prompt, material):
        assert json.loads(material)["issues"][0]["item_statement"].startswith("第1项")
        return schema(
            explanations=[
                dict(
                    issue_number=1,
                    accurate_explanation="取负梯度，例如沿山坡下行。",
                    source_indices=[0],
                )
            ]
        )

    def followup(state, persona):
        if state["content"] == claim:
            with t.factory() as reader:
                assert reader.get(CognitiveItem, 1).status == status  # durable before left tokens
        yield state["next_item"]["statement"]

    t.monkeypatch.setattr(t.model, "assess", assess)
    t.monkeypatch.setattr(t.model, "followup", followup)
    t.monkeypatch.setattr(rubric, "require_profile", lambda purpose: {"purpose": purpose})

    def connection(profile):
        assert profile["purpose"] == "encyclopedia"
        return SimpleNamespace(structured=correct)

    t.monkeypatch.setattr(rubric, "ModelConnection", connection)
    bad = turn(t, claim)
    kinds = [k for k, _ in bad]
    assert kinds.index("encyclopedia_push") < kinds.index("token") < kinds.index("done")
    pushed = dict(bad)["encyclopedia_push"]
    assert pushed["issue_type"] == kind and pushed["user_claim"] == claim and pushed["citations"]
    update = dict(bad)["cognitive_update"]
    assert update["items"][0]["status"] == status and update["current_item_id"] == 1
    assert update["mastery_score"] == round(value / 2)
    restored = next(m for m in history(t) if m["id"] == pushed["message_id"])
    assert restored["meta_json"] == {k: v for k, v in pushed.items() if k != "message_id"}
    assert len([m for m in history(t) if m["content"] == claim]) == 1
    good = turn(t, "梯度下降沿负梯度更新。")
    assert "encyclopedia_push" not in dict(good)
    updated = dict(good)["cognitive_update"]
    assert updated["mastery_score"] == 50 and updated["current_item_id"] == 2
    assert updated["items"][0]["issue_note"] == "" and updated["items"][0]["status"] == "mastered"
    assert "第2项" in dict(good)["token"]["delta"]
    with t.factory() as db:
        assert db.get(KnowledgeNode, 1).mastery_score == 50
        assert cognitive.document(db, 1)["mastered_count"] == 1


def test_multiple_items_and_foreign_or_fabricated_marks(teaching):
    from app.agents.teaching.rubric import validate_marks, save_marks
    from app.agents.teaching.schemas import Assessment
    from app.db.models import CognitiveItem
    from app.services import cognitive

    t = teaching
    with t.factory.begin() as db:
        for i in (1, 2):
            db.add(
                CognitiveItem(
                    id=i,
                    doc_id=1,
                    node_id=i,
                    item_order=i,
                    section_title="概念",
                    statement=SOURCE,
                    key_criteria="解释方向",
                )
            )
    text = "沿负梯度更新；梯度指向最快上升方向。"
    raw = dict(
        node_id=1,
        student_evidence=text,
        source_evidence=SOURCE,
        accuracy=90,
        clarity=90,
        followup_focus="应用",
        item_marks=[
            dict(item_id=i, status="mastered", user_evidence=part, issues=[])
            for i, part in enumerate(text.split("；"), 1)
        ],
    )
    with t.factory.begin() as db:
        state = dict(current_item_id=1, cognitive_items=cognitive.items(db, 1), content=text)
        validate_marks(Assessment.model_validate(raw), state)
        state["assessment"] = raw
        scores, event = save_marks(db, state, SimpleNamespace(doc_id=1, session_id=1))
        assert (
            event.current_item_id is None
            and event.mastered_count == 2
            and event.mastery_score == 100
        )
        assert {n.node_id for n in scores.updates} == {1, 2}
        raw["item_marks"][0]["item_id"] = 900
        with pytest.raises(ValueError):
            validate_marks(Assessment.model_validate(raw), state)
        raw["item_marks"][0].update(item_id=1, user_evidence="捏造的引文")
        with pytest.raises(ValueError):
            validate_marks(Assessment.model_validate(raw), state)
