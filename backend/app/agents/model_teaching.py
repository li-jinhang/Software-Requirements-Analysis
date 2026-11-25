"""Pedagogical decisions from the bound model, grounded and committed by the teaching service."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from pydantic_core import from_json
from sqlalchemy import select, func

from app.agents.contracts import Citation, MemoryUpdate, NodeUpdate, Report, Token, TurnMetadata, tokens
from app.agents.encyclopedia_agent import Explanation as LessonExplanation
from app.db.models import Message
from app.services import memory_service as memory
from app.services.model_connection import ModelConnection
from app.services.model_profiles import ModelError


class Assessment(BaseModel):
    follow_up: str = Field(min_length=1, max_length=2400)
    node_id: int
    delta: int = Field(ge=-15, le=20)
    feedback: str = Field(min_length=1, max_length=100)
    weakness_type: Literal["omission", "misconception", "jargon"] | None
    weakness_detail: str = Field(max_length=600)


class Explanation(LessonExplanation):
    node_id: int
    source_indices: list[int] = Field(max_length=4)


class GuidedLesson(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    lesson: str = Field(min_length=1, max_length=6000)
    source_indices: list[int] = Field(max_length=4)


def history(db, id):
    rows = db.scalars(
        select(Message).where(Message.session_id == id,
            func.json_extract(Message.meta_json, "$.no_assessment").is_(None)).order_by(Message.id.desc()).limit(32)
    ).all()
    return [
        {"window": m.window_type, "speaker": m.sender_role, "text": m.content[:1600]}
        for m in reversed(rows)
    ]


def evidence_nodes(nodes):
    return [
        {k: n[k] for k in ("id", "concept_name", "summary", "key_points", "mastery_score")}
        for n in nodes
    ]


def reply_preview(window, emit):
    """Project the human-facing strings of an incomplete structured response."""
    if emit is None:
        return None
    raw = ""
    shown = ""

    def update(delta):
        nonlocal raw, shown
        raw = raw + delta if delta else ""
        source = raw.lstrip()
        if source.startswith("```"):
            source = source.partition("\n")[2]
        try:
            fields = from_json(source, allow_partial="trailing-strings") if source else {}
        except ValueError:
            return
        if not isinstance(fields, dict):
            return
        if window == "feynman":
            text = fields.get("follow_up")
            text = text if isinstance(text, str) else ""
        elif window == "lesson":
            text = fields.get("lesson")
            text = text if isinstance(text, str) else ""
        else:
            parts = []
            for key, title in (
                ("source", "**课件溯源**\n\n"),
                ("example", "**学科知识补充与举例**\n\n"),
                ("retell", ""),
            ):
                value = fields.get(key)
                if isinstance(value, str) and value.strip():
                    parts.append(title + value)
            text = "\n\n".join(parts)
        if text != shown:
            shown = text
            emit(text)

    return update


def guided_lesson(db, turn, profile, emit_preview):
    ctx = memory.get_memory_context(db, turn.doc_id, concept=turn.target_node_id)
    node = next((n for n in evidence_nodes(ctx["nodes"]) if n["id"] == turn.target_node_id), None)
    if node is None:
        raise ModelError("本节讲解的知识点已不可用，请从图谱重新选择。")
    prerequisites = {n["id"]: n for n in evidence_nodes(ctx["prerequisites"])}
    hits = memory.search_rag(turn.doc_id, node["concept_name"])
    payload = json.dumps({
        "target": node,
        "prerequisites": [prerequisites[step["node_id"]] for step in ctx["recommended_learning_path"]
                          if step["node_id"] in prerequisites],
        "excerpts": [{"index": i, "chunk_index": hit["chunk_index"], "text": hit["snippet"]}
                     for i, hit in enumerate(hits)],
    }, ensure_ascii=False)
    db.commit()
    result = ModelConnection(profile).structured(
        GuidedLesson,
        "你是给零基础学生上第一课的中文老师。只讲 target 指定的知识点。"
        "先用一句话说明它解决什么问题，再用日常语言补齐理解本节所需的前置概念，优先补尚未掌握的前置。"
        "由浅入深讲解定义，解释首次出现的术语，提供一个带步骤和结果的具体例子，再指出一个常见误区。"
        "结尾提供一个只依赖本节内容、可以用自己的话回答的复述提示。"
        "lesson 使用 Markdown 小标题和短段落。课件依据用 [片段 #chunk_index] 标注，区分课件内容和补充举例。"
        "source_indices 只填实际引用的 excerpt index，缺少依据时填空列表，不得编造出处。",
        payload,
        on_chunk=reply_preview("lesson", emit_preview),
    )
    if any(i < 0 or i >= len(hits) for i in result.source_indices):
        raise ModelError("模型返回了本次检索之外的引用")
    yield TurnMetadata(metadata={"input_kind": "lesson", "related_node_ids": [node["id"]]})
    yield Citation(citations=[hits[i] for i in dict.fromkeys(result.source_indices)])
    yield Token(delta=result.lesson)


def prepare(db, turn, window, profile, emit_preview=None):
    ctx = memory.get_memory_context(db, turn.doc_id, turn.session_id, query=turn.content)
    nodes = evidence_nodes(ctx["nodes"])
    if not nodes:
        raise ModelError("当前资料没有可用知识点")
    hits = memory.search_rag(turn.doc_id, turn.content)
    payload = json.dumps(
        dict(
            content=turn.content,
            persona=turn.persona_type,
            history=history(db, turn.session_id),
            nodes=nodes,
            prerequisites=[n["concept_name"] for n in ctx["prerequisites"]],
            weaknesses=[w["detail"] for w in ctx["historical_weaknesses"][-12:]],
            excerpts=[{"index": i, "chunk_index": hit["chunk_index"], "text": hit["snippet"]}
                      for i, hit in enumerate(hits)],
        ),
        ensure_ascii=False,
    )
    db.commit()  # release the read snapshot before waiting on the provider
    model = ModelConnection(profile)
    on_chunk = reply_preview(window, emit_preview)
    # Constrain structured-output providers to evidence and graph IDs from this turn.
    node_type = Literal[tuple(n["id"] for n in nodes)]
    if window == "feynman":
        schema = create_model("Assessment", __base__=Assessment, node_id=(node_type, ...))
        result = model.structured(
            schema,
            "你是费曼反向教学听众。根据原文、历史和这次学生讲解，评估准确性、通俗度及适用条件。"
            "选一个本资料的 node_id，给有依据的掌握度变化与简短反馈。无漏洞时 weakness_type 为 null。"
            "把 follow_up 放在 JSON 的首个字段。"
            "应用 delta 后，若该知识点达到 80 分，优先追问另一个低于 80 分的知识点并点明名称；"
            "全部掌握时建议复讲或查看诊断报告。避免重复历史中的追问。"
            "用中文追问而不代讲。child 像小学生索要通俗例子，peer 像同窗讨论，examiner 追问边界与反例。",
            payload,
            on_chunk=on_chunk,
        )
    else:
        index_type = Literal[tuple(range(len(hits)))] if hits else int
        schema = create_model(
            "Explanation",
            __base__=Explanation,
            node_id=(node_type, ...),
            source_indices=(list[index_type], Field(max_length=len(hits))),
        )
        result = model.structured(
            schema,
            "你是中文百科助教。结合课程片段回答学生的具体疑问，解释前置概念，帮助他回到左窗复述。"
            "source 仅概括课件，正文用 [片段 #chunk_index] 标明原编号；source_indices 填实际用到的 index。"
            "example 根据通用学科知识给出请求的公式、类比或算例，即使课件没有举例也要补充。损失函数举例优先演算 MSE，"
            "学习率举例用二次函数演算两步。公式用 $...$，说明关键符号与结果。retell 提一个回左窗复述的问题。"
            "选择最相关的本课程 node_id。",
            payload,
            on_chunk=on_chunk,
        )
        if any(i < 0 or i >= len(hits) for i in result.source_indices):
            raise ModelError("模型返回了本次检索之外的引用")
    if result.node_id not in {n["id"] for n in nodes}:
        raise ModelError("模型返回了课程之外的知识点")
    return result, hits


def apply(db, turn, prepared):
    result, hits = prepared
    if isinstance(result, Explanation):
        memory.log_weakness(db, turn.session_id, result.node_id, "need_lookup", turn.content)
        yield TurnMetadata(metadata={"related_node_ids": [result.node_id]})
        yield Citation(citations=[hits[i] for i in dict.fromkeys(result.source_indices)])
        yield from tokens(result.render(bool(result.source_indices)))
        return
    node = memory.get_memory_context(db, turn.doc_id, turn.session_id)["nodes"]
    before = next(n for n in node if n["id"] == result.node_id)
    updated = memory.update_mastery(db, result.node_id, result.delta)
    if result.weakness_type:
        memory.log_weakness(
            db,
            turn.session_id,
            result.node_id,
            result.weakness_type,
            result.weakness_detail or result.feedback,
        )
    yield from tokens(result.follow_up)
    yield MemoryUpdate(
        updates=[
            NodeUpdate(
                node_id=result.node_id,
                concept_name=before["concept_name"],
                mastery_score=updated["mastery_score"],
                delta=updated["mastery_score"] - before["mastery_score"],
                status=updated["status"],
                tag=result.feedback,
            )
        ]
    )


def diagnose(db, session, profile):
    ctx = memory.get_memory_context(db, session.doc_id, session.id)
    material = json.dumps(
        dict(
            nodes=evidence_nodes(ctx["nodes"]),
            recent_dialogue=history(db, session.id),
            weaknesses=[w["detail"] for w in ctx["weaknesses"][-32:]],
        ),
        ensure_ascii=False,
    )
    db.commit()
    return ModelConnection(profile).structured(
        Report,
        "根据本轮费曼讲解、查证记录和掌握情况生成中文诊断。覆盖、准确、通俗、逻辑四项各 0–100 分。"
        "用对话中的具体例子解释评分，直接对学生说话。列出薄弱点，review_plan 每行写一项可执行的复讲任务。证据不足时明确说明。",
        material,
    )
