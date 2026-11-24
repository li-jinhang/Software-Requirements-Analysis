"""LangGraph rubric evaluation and the handoff to the bound encyclopedia model."""

import json
from pydantic import BaseModel, Field
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from sqlalchemy import select, update

from app.agents.contracts import CognitiveUpdate, EncyclopediaPush, MemoryUpdate
from app.db.models import Message, Session, WeaknessLog
from app.services import cognitive, memory_service as memory
from app.services.model_connection import ModelConnection
from app.services.model_profiles import ModelError, require_profile
from .schemas import TeachingState


class Explanation(BaseModel):
    issue_number: int = Field(ge=1)
    accurate_explanation: str = Field(min_length=2, max_length=700)
    source_indices: list[int] = Field(default_factory=list)


class Corrections(BaseModel):
    explanations: list[Explanation]


LABELS = {"wrong": "理解错误", "inaccurate": "表述不准确", "vague": "概念模糊"}


def validate_marks(assessment, state):
    if not state["cognitive_items"] or assessment.needs_help or not assessment.on_topic:
        return
    valid_ids = {i["id"] for i in state["cognitive_items"]}
    marks = assessment.item_marks
    ids = {m.item_id for m in marks}
    if not ids or len(ids) != len(marks) or not ids <= valid_ids:
        raise ValueError("评估缺少有效条目或条目重复")
    for m in marks:
        if m.user_evidence not in state["content"] or any(
            i.user_claim not in state["content"] for i in m.issues
        ):
            raise ValueError("认知评估缺少学生原话")
        expected = (
            "wrong"
            if any(i.issue_type == "wrong" for i in m.issues)
            else "partial" if m.issues else "mastered"
        )
        if m.status != expected:
            raise ValueError("认知标记与问题类型不一致")


def projected_focus(state):
    marks = {m["item_id"]: m["status"] for m in state["assessment"]["item_marks"]}
    rows = [{**i, "status": marks.get(i["id"], i["status"])} for i in state["cognitive_items"]]
    return cognitive.focus(rows, state["current_item_id"])


def save_marks(db, state, turn):
    marks = state["assessment"]["item_marks"]
    rows, changes = cognitive.apply_marks(db, turn.doc_id, marks)
    by_id = {r["id"]: r for r in rows}
    for mark in marks:
        item = by_id[mark["item_id"]]
        if item["node_id"] is None:
            continue
        for issue in mark["issues"]:
            memory.log_weakness(
                db,
                turn.session_id,
                item["node_id"],
                "misconception" if issue["issue_type"] == "wrong" else "omission",
                issue["detail"],
            )
    mastered_nodes = [n["node_id"] for n in changes if n["mastery_score"] == 100]
    if mastered_nodes:
        db.execute(
            update(WeaknessLog)
            .where(
                WeaknessLog.node_id.in_(mastered_nodes),
                WeaknessLog.is_resolved.is_(False),
                WeaknessLog.session_id.in_(select(Session.id).where(Session.doc_id == turn.doc_id)),
            )
            .values(is_resolved=True)
        )
    current = cognitive.focus(rows, state["current_item_id"])
    event = CognitiveUpdate(
        items=[by_id[m["item_id"]] for m in marks],
        current_item_id=current["id"] if current else None,
        mastery_score=cognitive.score(rows),
        mastered_count=sum(r["status"] == "mastered" for r in rows),
    )
    return MemoryUpdate(updates=changes), event


def correct(state: TeachingState, runtime: Runtime):
    """Commit the accepted student's evidence and corrections before the next left question streams."""
    db, turn = runtime.context.db, runtime.context.turn
    by_id = {i["id"]: i for i in state["cognitive_items"]}
    problems = []
    for mark in state["assessment"]["item_marks"]:
        item = by_id[mark["item_id"]]
        for issue in mark["issues"]:
            problems.append(
                {
                    **issue,
                    "item_id": item["id"],
                    "concept_name": item["section_title"],
                    "item_statement": item["statement"],
                    "key_criteria": item["key_criteria"],
                }
            )
    hits = state["citations"]
    result = ModelConnection(require_profile("encyclopedia")).structured(
        Corrections,
        "按 issues 顺序逐一纠偏，issue_number 从1开始。每个问题用一至三句中文解释正确原理并给一个例子。"
        "仅选实际引用的片段下标（从0开始），仅据认知条目即可解释时留空。",
        json.dumps(
            {"issues": problems, "excerpts": [h["snippet"] for h in hits]}, ensure_ascii=False
        ),
    )
    keyed = {e.issue_number: e for e in result.explanations}
    if len(result.explanations) != len(problems) or set(keyed) != set(range(1, len(problems) + 1)):
        raise ModelError("右窗纠偏未逐项覆盖本轮问题，请重试。")
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    scores, marks_event = save_marks(db, state, turn)
    db.add(
        Message(
            session_id=turn.session_id,
            window_type="feynman",
            sender_role="user",
            content=turn.content,
        )
    )
    pushes = []
    for index, issue in enumerate(problems, 1):
        explanation = keyed[index]
        if any(i < 0 or i >= len(hits) for i in explanation.source_indices):
            raise ModelError("纠偏模型返回的出处不属于本次检索。")
        payload = {
            k: issue[k]
            for k in ("item_id", "issue_type", "concept_name", "item_statement", "user_claim")
        }
        payload.update(
            proactive=True,
            label=LABELS[issue["issue_type"]],
            accurate_explanation=explanation.accurate_explanation,
            citations=[
                {
                    "kind": "cognitive_item",
                    "item_id": issue["item_id"],
                    "section_title": issue["concept_name"],
                },
                *(hits[i] for i in dict.fromkeys(explanation.source_indices)),
            ],
        )
        node_id = by_id[issue["item_id"]]["node_id"]
        message = Message(
            session_id=turn.session_id,
            window_type="encyclopedia",
            sender_role="assistant",
            content=payload["accurate_explanation"],
            meta_json=json.dumps(payload, ensure_ascii=False),
            related_node_ids=json.dumps([node_id] if node_id is not None else []),
        )
        db.add(message)
        db.flush()
        pushes.append(EncyclopediaPush(message_id=message.id, **payload))
    db.commit()
    writer = get_stream_writer()
    for push in pushes:
        writer(push)
    writer(marks_event)
    writer(scores)
    return {
        "trace": ["correct"],
        "accepted_early": True,
        "memory_synced": True,
        "updates": scores.model_dump()["updates"],
    }
