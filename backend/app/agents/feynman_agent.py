from collections.abc import Iterator
from sqlalchemy import select
from app.db.models import Message
from app.agents.contracts import AgentInput, AgentEvent, MemoryUpdate, NodeUpdate, tokens, relevant
from app.services import memory_service as memory


QUESTIONS = {
    "child": ("用一句话告诉我它是什么好吗？", "它像生活中的什么？能打个比方吗？",
              "什么时候这个比方会失灵呢？", "它和一个相近的概念有什么不同？"),
    "peer": ("你会怎样用自己的话定义它？", "能用一个生活类比解释它如何起作用吗？",
             "它在什么条件下成立？请举一个反例。", "选一个相关概念，比较它们各自解决的问题。"),
    "examiner": ("请给出定义并解释其中的关键量。", "请给出一个具体例子并说明对应关系。",
                 "请指出一个必要条件，并检验边界情形。", "请对比一个易混概念，说明判别依据。"),
}


def stream(db, turn: AgentInput) -> Iterator[AgentEvent]:
    # TODO(Agent 队友): replace baseline evidence heuristic with LangGraph assessment.
    nodes = memory.get_memory_context(db, turn.doc_id, turn.session_id)["nodes"]
    if not nodes:
        yield from tokens("这份资料的知识点已被清理，请重新上传资料后再练习。")
        return
    previous = db.scalar(select(Message.content).where(
        Message.session_id == turn.session_id, Message.window_type == "feynman",
        Message.sender_role == "assistant",
    ).order_by(Message.id.desc()).limit(1)) or ""
    mentioned = [n for n in nodes if n["concept_name"] in turn.content]
    previous_focus = next((n for n in nodes if f"「{n['concept_name']}」" in previous), None)
    node = (max(mentioned, key=lambda n: len(n["concept_name"])) if mentioned
            else previous_focus or relevant(nodes, turn.content))
    match = node["concept_name"] in turn.content
    delta = 8 if match else -3
    label = "提及核心概念" if match else "需补充概念"
    updated = memory.update_mastery(db, node["id"], delta)
    if not match:
        memory.log_weakness(
            db, turn.session_id, node["id"], "omission", "请补充：" + node["concept_name"]
        )
    target = updated
    if updated["mastery_score"] >= 80:
        candidates = [n for n in nodes if n["id"] != node["id"] and n["mastery_score"] < 80]
        priority = {"weak": 0, "learning": 1, "unlearned": 2}
        target = min(candidates, key=lambda n: (priority[n["status"]], n["mastery_score"], n["id"]),
                     default=None)
    if target:
        questions = QUESTIONS[turn.persona_type]
        prev_dimension = next((i for i, q in enumerate(questions) if previous.endswith(q)), -1)
        question = questions[(prev_dimension + 1) % len(questions)]
        yield from tokens(f"「{target['concept_name']}」：{question}")
    else:
        yield from tokens("本课知识点均已达到掌握阈值。可以结束本轮查看诊断报告，或自选一个概念复习。")
    actual = updated["mastery_score"] - node["mastery_score"]
    yield MemoryUpdate(
        updates=[
            NodeUpdate(
                node_id=node["id"],
                concept_name=node["concept_name"],
                mastery_score=updated["mastery_score"],
                delta=actual,
                status=updated["status"],
                tag=f"{label} {actual:+d}",
            )
        ]
    )
