"""Evidence-grounded U0003/U0004 graph. One serial run owns one DB session."""

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import re

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.agents.contracts import (
    AgentInput, Citation, MemoryUpdate, NodeUpdate, Token, TurnMetadata, relevant,
)
from app.db.models import Message, Session, KnowledgeNode, WeaknessLog, public
from app.services import memory_service as memory
from app.services import cognitive
from . import model, rubric
from .prompts import opening
from .schemas import Assessment, TeachingState

log = logging.getLogger(__name__)


@dataclass
class TeachingRuntime:
    db: object
    turn: AgentInput


def load_context(state: TeachingState, runtime: Runtime[TeachingRuntime]):
    db, turn = runtime.context.db, runtime.context.turn
    ctx = memory.get_memory_context(db, turn.doc_id, turn.session_id)
    history = [public(m) for m in reversed(db.scalars(
        select(Message).where(Message.session_id == turn.session_id)
        .order_by(Message.id.desc()).limit(24)
    ).all())]
    node = None
    rows = cognitive.items(db, turn.doc_id)
    previous = cognitive.last_focus(db, turn.session_id) if rows else None
    current = cognitive.focus(rows, previous, turn.target_node_id)
    session = db.get(Session, turn.session_id)
    reviewing = session.mode == "review"
    remaining = []
    if reviewing:
        by_id = {r["id"]: r for r in rows}
        remaining = [by_id[i] for i in session.target_item_ids[session.review_cursor:] if i in by_id]
        current = next(iter(remaining), None)
        rows = remaining[:1]  # A retest evaluates only the selected criterion.
    if ctx["nodes"]:
        explicit = next((n for n in ctx["nodes"] if n["id"] == turn.target_node_id), None)
        named = [n for n in ctx["nodes"] if n["concept_name"] in turn.content]
        last_ids = next((m["related_node_ids"] for m in reversed(history)
                         if m["window_type"] == "feynman" and m["sender_role"] == "assistant"
                         and m["related_node_ids"]), [])
        last = next((n for n in ctx["nodes"] if n["id"] in last_ids), None)
        node = explicit or (max(named, key=lambda n: len(n["concept_name"])) if named else last)
        node = node or relevant(ctx["nodes"], turn.content)
        if current:
            node = next((n for n in ctx["nodes"] if n["id"] == current["node_id"]), node)
        ctx = memory.get_memory_context(db, turn.doc_id, turn.session_id, concept=node["id"])
    # No database snapshot or write lock is held while waiting for the provider.
    db.rollback()
    return {
        "trace": ["load_context"], "content": turn.content, "node": node,
        "memory": ctx, "history": history, "cognitive_items": rows, "accepted_early": False,
        "current_item_id": current["id"] if current else None, "next_item": current,
        "reviewing": reviewing, "review_queue": remaining,
        "assessment": None, "assessment_error": "", "repair_count": 0,
        "high_quality": False, "streak": 0, "delta": 0,
        "updates": [], "memory_synced": False,
    }


def classify(state: TeachingState, runtime: Runtime[TeachingRuntime]):
    text = state["content"].strip()
    help_intent = re.search(
        r"我(?:还|还是|完全)?(?:不会(?:讲|解释|做|了|[，。！？!?,\s]|$)|不懂|不知道)"
        r"|卡壳|讲不下去|没听懂|给我答案", text,
    ) or re.fullmatch(r"(?:不会|不懂|不知道)[了啊呀呢！!？?。\s]*", text)
    if not state["node"]:
        kind = "empty"
    elif runtime.context.turn.action == "opening":
        kind = "opening"
    elif state["cognitive_items"] and not state["current_item_id"]:
        kind = "finished"
    elif len(text) < 100 and help_intent:
        kind = "stuck"
    elif re.search(r"今天天气|明天天气|讲个笑话|股票推荐|买彩票|聊聊明星|订外卖", text):
        kind = "off_topic"
    else:
        kind = "explain"
    return {"trace": ["classify"], "kind": kind}


def retrieve(state: TeachingState, runtime: Runtime[TeachingRuntime]):
    hits = memory.search_rag(
        runtime.context.turn.doc_id,
        " ".join([state["node"]["concept_name"], (state.get("next_item") or {}).get("statement", ""),
                  state["content"]]), top_k=4,
    )
    if hits:
        get_stream_writer()(Citation(citations=hits))
    return {"trace": ["retrieve"], "citations": hits}


def assess(state: TeachingState):
    if not state["citations"] and not state.get("next_item"):
        return {"trace": ["assess"], "assessment_error": "未召回课件原文，本轮不更新掌握度。"}
    try:
        raw = model.assess(state, Assessment.model_json_schema(), state["repair_count"] > 0)
        if raw.startswith("```") and raw.endswith("```"):
            raw = raw.partition("\n")[2].removesuffix("```").strip()
        assessment = Assessment.model_validate_json(raw)
        rubric.validate_marks(assessment, state)
        if assessment.node_id != state["node"]["id"]:
            raise ValueError("评估知识点与当前教学目标不一致")
        if assessment.student_evidence not in state["content"]:
            raise ValueError("学生证据不是本轮输入原文")
        evidence = [h["snippet"] for h in state["citations"]]
        for item in state["cognitive_items"]:
            evidence.extend((item["statement"], item["key_criteria"]))
        if not any(assessment.source_evidence in source for source in evidence):
            raise ValueError("原文证据不属于召回片段")
        return {"trace": ["assess"], "assessment": assessment.model_dump(), "assessment_error": ""}
    except model.ModelError:
        raise
    except (ValueError, IndexError) as exc:
        reason = ("字段错误：" + "、".join(".".join(map(str, err["loc"])) + "/" + err["type"]
                                  for err in exc.errors(include_input=False)[:4])
                  if isinstance(exc, ValidationError) else str(exc))
        log.warning("教学评估校验失败 (%s)，纠错次数 %s", reason, state["repair_count"])
        return {"trace": ["assess"], "assessment": None,
                "assessment_error": reason + "。本轮不更新掌握度。"}


def repair(state: TeachingState):
    return {"trace": ["repair"], "repair_count": state["repair_count"] + 1}


def assessment_route(state):
    if state.get("assessment"):
        return "plan_memory"
    if (state["citations"] or state.get("next_item")) and state["repair_count"] == 0:
        return "repair"
    return "respond"


def plan_memory(state: TeachingState):
    assessment, node = state["assessment"], state["node"]
    if assessment["needs_help"]:
        return {"trace": ["plan_memory"], "kind": "stuck", "assessment": None}
    if not assessment["on_topic"]:
        return {"trace": ["plan_memory"], "kind": "off_topic", "assessment": None}
    if state["cognitive_items"]:
        return {"trace": ["plan_memory"], "next_item": rubric.projected_focus(state)}
    challenge = next((m for m in reversed(state["history"])
                      if m["window_type"] == "feynman" and m["sender_role"] == "assistant"), None)
    answered_challenge = bool(challenge and node["id"] in challenge["related_node_ids"]
                              and challenge["meta_json"].get("question_kind") == "deep")
    # Implementation rubric (not a numeric threshold specified by the SRS).
    good = (not model.offline() and answered_challenge and not assessment["problems"]
            and assessment["accuracy"] >= 80 and assessment["clarity"] >= 80)
    if assessment["problems"]:
        delta = -10 if any(p["weakness_type"] == "misconception" for p in assessment["problems"]) else -3
    else:
        delta = 15 if good else (8 if assessment["accuracy"] >= 60 else 0)
    return {"trace": ["plan_memory"], "high_quality": good, "delta": delta}


def respond(state: TeachingState, runtime: Runtime[TeachingRuntime]):
    writer, turn = get_stream_writer(), runtime.context.turn
    node = state["node"]
    if state["kind"] == "empty":
        chunks = ["这份资料没有可用知识点，请重新解析或上传资料后再练习。"]
        question_kind = "clarify"
    elif state.get("reviewing") and not state["next_item"]:
        chunks = ["本轮选定条目已复测完毕。你可以查看标记，或随时再选条目复习。"]
        question_kind = "complete"
    elif state["cognitive_items"] and not state["next_item"]:
        chunks = ["这份认知文档的条目已全部掌握，可以结束本次教学并查看诊断报告。"]
        question_kind = "complete"
    elif state["kind"] == "opening":
        item = state["next_item"]
        if item and state.get("reviewing"):
            chunks = model.followup(state, turn.persona_type)
        elif item:
            question = {"child": "你能打个生活中的比方，让我听懂这句话吗？",
                        "peer": "你能用自己的话，解释这句话背后的道理吗？",
                        "examiner": "请通过一个具体情形说明这条结论的依据。"}[turn.persona_type]
            chunks = [f"我们先看「{item['section_title']}」里的一句：\n\n{item['statement']}\n\n{question}"]
        else:
            chunks = [opening(node["concept_name"], turn.persona_type)]
        question_kind = "deep"
    elif state["kind"] == "stuck":
        chunks = [f"没关系，我们先查证「{node['concept_name']}」。点击“去右侧查证”，看过原文后再用自己的话讲一遍，好吗？"]
        question_kind = "lookup"
    elif state["kind"] == "off_topic":
        chunks = [f"我们先回到「{node['concept_name']}」。你能说说它的含义，并举一个例子吗？"]
        question_kind = "redirect"
    else:
        question_kind = "deep" if state.get("assessment") else "clarify"
        chunks = model.followup(state, turn.persona_type)
    response = []
    for text in chunks:
        response.append(text)
        # The shared transport forwards provider deltas during its blocking call.
        if not isinstance(text, model.StreamedText):
            writer(Token(delta=text))
    return {"trace": ["respond"], "response": "".join(response), "question_kind": question_kind}


def persist_memory(state: TeachingState, runtime: Runtime[TeachingRuntime]):
    """Short final write section; a savepoint isolates optional memory failures."""
    assessment, node = state.get("assessment"), state["node"]
    if not assessment or state["accepted_early"]:
        return {"trace": ["persist_memory"]}
    if state["cognitive_items"]:
        db, turn = runtime.context.db, runtime.context.turn
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        scores, marks_event = rubric.save_marks(db, state, turn)
        get_stream_writer()(scores)
        get_stream_writer()(marks_event)
        return {"trace": ["persist_memory"], "memory_synced": True, "updates": scores.model_dump()["updates"]}
    db, turn = runtime.context.db, runtime.context.turn
    try:
        # Establish a real outer SQLite transaction before SAVEPOINT; releasing
        # an outermost savepoint would otherwise commit before messages are saved.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        with db.begin_nested():
            current = db.get(KnowledgeNode, node["id"], populate_existing=True)
            if current is None or current.doc_id != turn.doc_id:
                raise ValueError("知识点已删除或不属于当前资料")
            latest_message = db.scalar(select(Message).join(Session).where(
                Session.doc_id == turn.doc_id, Message.window_type == "feynman",
                Message.sender_role == "assistant",
                func.json_extract(Message.meta_json, "$.assessment.node_id") == node["id"],
            ).order_by(Message.id.desc()).limit(1))
            latest = json.loads(latest_message.meta_json) if latest_message else {}
            prior = latest.get("assessment", {})
            continuous = (latest.get("memory_synced") is True and prior.get("high_quality") is True
                          and prior.get("lookup_count") == current.lookup_count)
            streak = (min(2, prior.get("streak", 0) + 1) if continuous else 1) if state["high_quality"] else 0
            old = current.mastery_score
            delta = state["delta"]
            if streak >= 2:
                delta = max(delta, 80 - old)
            elif old < 80:
                delta = min(delta, 79 - old)
            updated = memory.update_mastery(db, node["id"], delta)
            if updated["mastery_score"] >= 80 and streak < 2 and assessment["problems"]:
                updated = memory.update_mastery(db, node["id"], 79 - updated["mastery_score"])
            for problem in assessment["problems"]:
                memory.log_weakness(db, turn.session_id, node["id"], **problem)
            if streak >= 2:
                db.execute(update(WeaknessLog).where(
                    WeaknessLog.node_id == node["id"],
                    WeaknessLog.session_id.in_(select(Session.id).where(Session.doc_id == turn.doc_id)),
                    WeaknessLog.is_resolved.is_(False),
                ).values(is_resolved=True))
            db.flush()
        change = NodeUpdate(
            node_id=node["id"], concept_name=node["concept_name"],
            mastery_score=updated["mastery_score"], delta=updated["mastery_score"] - old,
            status=updated["status"],
            tag=("本地规则演示 · " if model.offline() else "")
                + ("准确清晰" if state["high_quality"] else "待补漏" if assessment["problems"] else "继续深入")
                + f" {updated['mastery_score'] - old:+d}",
        )
        get_stream_writer()(MemoryUpdate(updates=[change]))
        return {"trace": ["persist_memory"], "memory_synced": True, "streak": streak,
                "updates": [change.model_dump()], "node": {**node, "lookup_count": current.lookup_count}}
    except Exception as exc:
        log.warning("记忆更新失败，保留追问 (%s)", type(exc).__name__)
        get_stream_writer()(Token(delta="\n\n本轮掌握度未同步，请稍后重试。"))
        return {"trace": ["persist_memory"], "memory_synced": False}


def complete(state: TeachingState):
    assessment = dict(state.get("assessment") or {})
    if state["node"]:
        assessment.update(
            node_id=state["node"]["id"], high_quality=state["high_quality"],
            streak=state["streak"] if state["memory_synced"] else 0,
            lookup_count=state["node"]["lookup_count"],
        )
    get_stream_writer()(TurnMetadata(metadata={
        "graph": "feynman_teaching", "graph_nodes": state["trace"] + ["complete"],
        "current_item_id": state["next_item"]["id"] if state.get("next_item") else None,
        "accepted_early": state["accepted_early"],
        "mode": "offline" if model.offline() else "live",
        "input_kind": state["kind"], "question_kind": state["question_kind"],
        "assessment": assessment, "assessment_error": state["assessment_error"],
        "memory_synced": state["memory_synced"],
        "related_node_ids": [state["node"]["id"]] if state["node"] else [],
    }))
    return {"trace": ["complete"]}


@lru_cache
def build_graph():
    graph = StateGraph(TeachingState, context_schema=TeachingRuntime)
    for node in (load_context, classify, retrieve, assess, repair, plan_memory,
                 rubric.correct, respond, persist_memory, complete):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "classify")
    graph.add_conditional_edges("classify", lambda s: "retrieve" if s["kind"] == "explain" else "respond",
                                {"retrieve": "retrieve", "respond": "respond"})
    graph.add_edge("retrieve", "assess")
    graph.add_conditional_edges("assess", assessment_route,
                                {n: n for n in ("plan_memory", "repair", "respond")})
    graph.add_edge("repair", "assess")
    def after_plan(state):
        marks = (state.get("assessment") or {}).get("item_marks", [])
        return "correct" if any(m["issues"] for m in marks) else "respond"

    graph.add_conditional_edges("plan_memory", after_plan, {"correct": "correct", "respond": "respond"})
    graph.add_edge("correct", "respond")
    graph.add_edge("respond", "persist_memory")
    graph.add_edge("persist_memory", "complete")
    graph.add_edge("complete", END)
    return graph.compile()
